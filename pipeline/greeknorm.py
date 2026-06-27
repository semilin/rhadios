"""Greek text normalization and gloss-to-text matching.

Two layers of normalization:
  - ``normalize_display``: canonical form for stored/IR text and for the text
    fed to the model. Collapses whitespace and normalizes elision marks to
    U+02BC (MODIFIER LETTER APOSTROPHE). Accents are preserved.
  - the internal fuzzy layer: NFD-decomposed, combining diacritics stripped,
    lowercased, elision marks dropped, whitespace collapsed. Used only as a
    fallback so a gloss key the model returned with a slightly different
    apostrophe or stray accent still matches.

Matching is **token-aware**: a gloss key matches whole Greek word-tokens (or a
run of consecutive tokens for a phrase), never a substring buried inside a
larger word. So ``ἀλλ`` matches the standalone token ``ἀλλʼ`` but not the
``ἀλλ`` inside ``πάλλω`` or ``σπουδάζω``. The merger pre-computes character
offsets, so the web app does zero matching.
"""
from __future__ import annotations

import unicodedata

# Characters that may appear as elision / apostrophe in Perseus or model output.
_APOS = set("'\u2019\u2018\u02bc`")
ELISION = "\u02bc"


def normalize_display(text: str) -> str:
    """Canonical form: elision -> U+02BC, whitespace collapsed and trimmed."""
    out = []
    for ch in text:
        if ch in _APOS:
            out.append(ELISION)
        elif ch.isspace():
            out.append(" ")
        else:
            out.append(ch)
    return " ".join("".join(out).split())


def _is_greek_letter(ch: str) -> bool:
    o = ord(ch)
    return (0x0370 <= o <= 0x03FF) or (0x1F00 <= o <= 0x1FFF)  # Greek + Greek Extended


def _is_word_char(ch: str) -> bool:
    """A Greek letter, a combining diacritic, or an elision mark."""
    if _is_greek_letter(ch):
        return True
    if unicodedata.category(ch) in ("Mn", "Me"):
        return True
    return ch in _APOS


def _tokens(text: str) -> list[tuple[int, int]]:
    """Word-token spans: maximal runs of word-chars (letters + diacritics +
    elision). Punctuation and whitespace separate tokens."""
    toks = []
    i, n = 0, len(text)
    while i < n:
        if _is_word_char(text[i]):
            j = i
            while j < n and _is_word_char(text[j]):
                j += 1
            toks.append((i, j))
            i = j
        else:
            i += 1
    return toks


def _fuzzy(s: str) -> str:
    """Lowercase, NFD, drop combining diacritics and elision marks, collapse ws."""
    out = []
    prev_space = True
    for ch in s:
        for d in unicodedata.normalize("NFD", ch):
            if unicodedata.category(d) in ("Mn", "Me"):
                continue
            if d in _APOS:
                continue
            if d.isspace():
                if not prev_space:
                    out.append(" ")
                    prev_space = True
                continue
            low = d.lower()
            if low.isspace():
                if not prev_space:
                    out.append(" ")
                    prev_space = True
                continue
            out.append(low)
            prev_space = False
    return "".join(out).strip()


def _strip_punct(s: str) -> str:
    """Strip leading/trailing chars that are not word-chars (keeps elision marks)."""
    while s and not _is_word_char(s[0]):
        s = s[1:]
    while s and not _is_word_char(s[-1]):
        s = s[:-1]
    return s


def find_all_gloss_spans(text: str, key: str) -> list[tuple[int, int]]:
    """All (start, end) offsets in ``text`` (display-normalized) whose whole
    token(s) equal ``key``. Exact match first, then fuzzy (accent/elision drift).
    """
    nk = normalize_display(key)
    kwords = [w for w in (_strip_punct(w) for w in nk.split()) if w]
    if not kwords:
        return []
    toks = _tokens(text)
    if not toks:
        return []
    L = len(kwords)
    if L > len(toks):
        return []

    spans: list[tuple[int, int]] = []

    # 1) exact (display-normalized, token-by-token)
    for i in range(len(toks) - L + 1):
        run = toks[i : i + L]
        if all(text[a:b] == kwords[k] for k, (a, b) in enumerate(run)):
            spans.append((run[0][0], run[-1][1]))
    if spans:
        return spans

    # 2) fuzzy fallback
    fkwords = [_fuzzy(w) for w in kwords]
    if not all(fkwords):
        return []
    ftok_cache: dict[tuple[int, int], str] = {}
    for i in range(len(toks) - L + 1):
        run = toks[i : i + L]
        ok = True
        for k, (a, b) in enumerate(run):
            if (a, b) not in ftok_cache:
                ftok_cache[(a, b)] = _fuzzy(text[a:b])
            if ftok_cache[(a, b)] != fkwords[k]:
                ok = False
                break
        if ok:
            spans.append((run[0][0], run[-1][1]))
    return spans
