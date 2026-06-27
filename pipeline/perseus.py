"""Fetch Perseus TEI XML and parse it into the uniform segment IR.

A segment is the smallest unit the rest of the pipeline works with:

    {
      "speaker":  str | None,   # "Σωκράτης" / "Χορός" / None (epic narrator)
      "label":    str | None,   # "ΣΩ." / "ΣΤΡ." / None
      "ref":      str,          # "75d" | "Ant.452" | "Il.1.1"
      "section_n": str | None,  # outer grouping key ("75" / book / episode)
      "text":     str,          # display-normalized text (prose) — see note below
      "lines":    list[str] | None,  # verse: individual lines; None for prose
    }

Everything downstream (chunker, annotator, matcher, merger, web) consumes this
IR, so adding a new text type means writing one ``parse_<type>`` function that
returns ``list[segment]`` — nothing else changes.
"""
from __future__ import annotations

import os
import urllib.request
import xml.etree.ElementTree as ET
from . import greeknorm

PERSEUS_RAW = (
    "https://raw.githubusercontent.com/PerseusDL/canonical-greekLit/master/"
    "data/{auth}/{grp}/{auth}.{grp}.perseus-grc2.xml"
)

# Elements whose text we drop when extracting spoken text.
_SKIP_TAGS = {"label", "milestone", "bibl", "note", "ref", "head", "del"}


# --------------------------------------------------------------------------- #
# generic XML helpers (namespace-agnostic via local-name)
# --------------------------------------------------------------------------- #
def _local(tag: str) -> str:
    return tag.split("}", 1)[-1]


def _iter_local(elem, name: str):
    for el in elem.iter():
        if _local(el.tag) == name:
            yield el


def _first_local(elem, name: str):
    for el in elem.iter():
        if _local(el.tag) == name:
            return el
    return None


def _inner_text(elem, skip=_SKIP_TAGS) -> str:
    """All text under ``elem`` excluding elements whose local name is in ``skip``
    (but keeping their tails)."""
    if _local(elem.tag) in skip:
        return ""
    s = []
    if elem.text:
        s.append(elem.text)
    for child in elem:
        s.append(_inner_text(child, skip))
        if child.tail:
            s.append(child.tail)
    return "".join(s)


# --------------------------------------------------------------------------- #
# fetch + cache
# --------------------------------------------------------------------------- #
def fetch_work(cts: str, dest_dir: str) -> str:
    """Download the TEI XML for a CTS id like ``tlg0059.tlg024.perseus-grc2``."""
    auth, grp = cts.split(".")[:2]
    url = PERSEUS_RAW.format(auth=auth, grp=grp)
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, f"{cts}.xml")
    if not os.path.exists(path):
        req = urllib.request.Request(url, headers={"User-Agent": "rhadios/0.1"})
        with urllib.request.urlopen(req) as r, open(path, "wb") as f:
            f.write(r.read())
    return path


def _edition_root(root):
    ed = None
    for el in _iter_local(root, "div"):
        if el.get("type") == "edition":
            ed = el
            break
    return ed or root


# --------------------------------------------------------------------------- #
# parse dispatch
# --------------------------------------------------------------------------- #
def parse(xml_path: str, work_type: str) -> list[dict]:
    root = ET.parse(xml_path).getroot()
    if work_type == "dialogue":
        return parse_dialogue(root)
    if work_type == "drama":
        return parse_drama(root)
    if work_type == "epic":
        return parse_epic(root)
    raise ValueError(f"unknown work type: {work_type!r}")


# --------------------------------------------------------------------------- #
# Plato (prose dialogue)
#   <div section n="75">
#     <p><said who="#Σωκράτης"><label>ΣΩ.</label> ... <milestone unit="section" n="75a"/> ... <q>...</q> ...</said></p>
# --------------------------------------------------------------------------- #
def parse_dialogue(root) -> list[dict]:
    edition = _edition_root(root)
    segments: list[dict] = []
    current_ref: str | None = None

    for sec in _iter_local(edition, "div"):
        if sec.get("subtype") != "section":
            continue
        sec_n = sec.get("n")
        for p in _iter_local(sec, "p"):
            for said in _iter_local(p, "said"):
                who = said.get("who")
                speaker = who.lstrip("#") if who else None
                label_el = _first_local(said, "label")
                label = (
                    label_el.text.strip() if label_el is not None and label_el.text else None
                )
                # ref = first Stephanus section milestone inside this speech,
                # else the one carried from the previous speech.
                ref = current_ref or sec_n
                for ms in _iter_local(said, "milestone"):
                    if ms.get("unit") == "section" and ms.get("resp") == "Stephanus":
                        ref = ms.get("n")
                        current_ref = ref
                        break
                text = greeknorm.normalize_display(_inner_text(said))
                if text:
                    segments.append(
                        {
                            "speaker": speaker,
                            "label": label,
                            "ref": ref or sec_n,
                            "section_n": sec_n,
                            "text": text,
                            "lines": None,
                        }
                    )
    return segments


# --------------------------------------------------------------------------- #
# Sophocles / Euripides (verse drama)  — SEAM
#   Perseus groups by episode; speeches are <sp><speaker>X</speaker><l>...</l></sp>.
#   ref = line number n -> "Ant.452"; lines preserved for verse rendering.
# --------------------------------------------------------------------------- #
def parse_drama(root) -> list[dict]:
    raise NotImplementedError(
        "parse_drama: implement <sp>/<speaker>/<l n> extraction returning the "
        "segment IR. Same shape as parse_dialogue but with `lines` populated."
    )


# --------------------------------------------------------------------------- #
# Homer (dactylic hexameter) [implemented]
#   <div book n="1"> ... <l n="1">...</l>; chunk by ~24-line cards.
#   ref = "book.line" -> "Il.1.1"; speaker=None (narrator / character speech
#   carried by <sp> in some editions).
# --------------------------------------------------------------------------- #
def parse_epic(root) -> list[dict]:
    """Homer hexameter. This Perseus edition encodes direct speech as <q> blocks
    of <l> lines and narrative lines as direct children of <div subtype=Book>;
    there is no <sp>/<speaker> markup, so speaker is None throughout (the
    narrator's voice, the way the Iliad has been read for millennia) and <q> is
    used only as a chunking signal. A segment is a maximal run of consecutive
    lines of one kind (a speech, or a narrative block); ~40-line cards that
    never split a speech are built by build_chunks_epic in annotate.py.
    """
    edition = _edition_root(root)
    segments: list[dict] = []

    for book in _iter_local(edition, "div"):
        if book.get("subtype") != "Book":
            continue
        book_n = book.get("n")
        q_ids = {id(q) for q in _iter_local(book, "q")}
        parent = {id(c): id(p) for p in book.iter() for c in p}

        def in_q(el) -> bool:
            cur = id(el)
            while cur in parent:
                cur = parent[cur]
                if cur in q_ids:
                    return True
            return False

        # <l> in document order: (n, normalized text, inside-speech flag)
        ordered: list[tuple[str | None, str, bool]] = []
        for l in _iter_local(book, "l"):
            txt = greeknorm.normalize_display(_inner_text(l))
            if txt:
                ordered.append((l.get("n"), txt, in_q(l)))

        # group consecutive lines into maximal speech/narrative runs
        units: list[list[tuple[str | None, str]]] = []
        prev: bool | None = None
        cur: list[tuple[str | None, str]] = []
        for n, txt, iq in ordered:
            if iq is not prev:
                if cur:
                    units.append(cur)
                cur = []
                prev = iq
            cur.append((n, txt))
        if cur:
            units.append(cur)

        for unit in units:
            ls = [t for _, t in unit]
            ref = f"{book_n}.{unit[0][0]}-{unit[-1][0]}"
            segments.append(
                {
                    "speaker": None,
                    "label": None,
                    "ref": ref,
                    "section_n": book_n,
                    "text": "\n".join(ls),
                    "lines": ls,
                }
            )
    return segments
