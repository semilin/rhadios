"""Chunk segments, call OpenRouter for each chunk, cache per-chunk to disk.

Processing is SEQUENTIAL so each chunk can be told which key terms were already
introduced in earlier passages (avoids re-teaching διδακτός every chunk).
Resume-safe: cached chunks in the prefix are read back to rebuild the
"already introduced" set, so a partial run picks up correctly. ``--force``
re-annotates everything; ``--limit N`` annotates only the first N pending
chunks (useful for a smoke test before paying for the whole run).
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "google/gemini-3-flash-preview"

PROMPT_TEMPLATE = """\
You are making an immersive, in-language Attic Greek reader of {title} for early-intermediate students. Every gloss, definition, and summary is in Attic Greek — never English — so the student stays in the target language.

You will receive {passage_desc}.{dialect_note} Produce three things:

1. "summary": a brief, simplified Attic Greek paraphrase of the passage.

2. "key_terms": the genuinely DIFFICULT vocabulary a student should learn BEFORE reading — the tiered-reader vocabulary box. Be SELECTIVE: at most 6 terms, only words that are (a) truly unfamiliar to an intermediate student AND (b) need MORE than a one-phrase synonym to grasp. For each, give a FULL explanatory definition in common vocabulary (a short phrase or circumlocution is good — the box is the place for nuance); do NOT give a bare one-word synonym here. Each term is an object with:
   - "lemma": the dictionary form (nom. sg. for nouns/adjectives, 1st sg. pres. indic. for verbs)
   - "form": one inflected form EXACTLY as it appears in the passage (copy it verbatim)
   - "gloss": a full Attic Greek definition, written so an intermediate student understands it

3. "glosses": inline help for moderately difficult words that HAVE a clean one-phrase synonym — these do NOT need the fuller box treatment. A handful per section is right. Each is a 2-element list [phrase, gloss] copied VERBATIM and CONTIGUOUSLY from the passage.

DECISION — where does a word go?
- VOCAB BOX: a genuinely hard word that needs unpacking. Ideal: {{"lemma":"διδακτός","form":"διδακτὸν","gloss":"ὃ ἔξεστι διδάσξασθαι"}}, {{"lemma":"ἀσκητός","form":"ἀσκητὸν","gloss":"ὃ ἔξεστι μελετᾶν"}}, {{"lemma":"ἱππική","form":"ἱππικῇ","gloss":"ἡ τῶν ἵππων τέχνη"}}. The student likely doesn't know the word; the definition uses vocabulary they do.
- INLINE GLOSS: a word with a clean one-phrase equivalent, read in situ. Ideal: ["καταμέμφομαι","αἰτιῶμαι ἐμαυτόν"], ["μεγαλοπρεπῶς","λαμπρῶς"], ["προστάττεις","κελεύεις"]. If a single equivalent phrase suffices, use an inline gloss, NOT the box.
- NOTHING: common words, and the exclusions below.

DO NOT (these are noise or counterproductive):
- Do NOT put a word in the box if a one-phrase synonym would do — make it an inline gloss instead.
- Do NOT give a gloss that is merely a simpler cognate/stem (παραγίγνομαι -> γίγνεται is trivial; exclude it).
- Do NOT give a gloss that uses RARER vocabulary than the key (ἔθος -> τὸ εἰωθός, ἐθίζω -> εἴωθεν ποιεῖν — the student knows ἔθος but not εἰωθός; these are anti-helpful). A gloss must use vocabulary AT LEAST as common as the key.
- Do NOT re-list any term already introduced in an earlier passage (listed below); if such a word reappears and the reader might need a reminder, at most a single inline gloss.
- Do NOT gloss articles, pronouns, or common particles/verbs (καί, δέ, γάρ, μέν, ἀλλά, ἤ, εἰ, ὡς, οὖν, ἄν, δή, που, ἆρα, μῶν, εἰμί, ἔχω, φημί, λέγω), nor any word appearing more than ~6 times in the passage.
- Do NOT produce accent-only "glosses" (πέρι -> περί), un-elision (ἄπʼ -> ἀπό), or an article merely dropped/added (ὁποῖα -> ποῖα).

DEFINITIONS — expand for clarity:
- For BOX terms, write a definition an intermediate student can actually understand. Prefer common vocabulary; if you must use a less-common word, briefly gloss it in parentheses so no extra lookup is needed. Clarity over brevity. (e.g. αὐχμός = "ἔνδεια ὕδατος" is too terse when ἔνδεια is itself unfamiliar — prefer something like "τὸ μὴ ἔχειν ὕδωρ (ἢ σοφίαν)".)
- For INLINE glosses, keep the one-phrase synonym tight.

LEMMA vs FORM (important):
- A box term has two parts: "form" = the inflected word AS IT APPEARS in the passage (copied verbatim); "gloss" = a definition of the abstract LEMMA in its dictionary form, NOT of that specific inflected occurrence. So define εἰκάζω as "λέγω ὅτι ὁμοιός ἐστί τις ἄλλῳ…" (1st person, matching the lemma εἰκάζω), NOT "λέγεις…"; define ἐραστής as "ὁ φιλῶν τινα…" (singular, matching the lemma ἐραστής), NOT "οἱ φιλοῦντες…". The "form" records the passage's inflection; the "gloss" describes the lemma.
- INLINE glosses, by contrast, substitute for the exact form in the text, so their synonym matches that form's parsing.

RULES (both):
- All glosses/definitions are Attic Greek. INLINE glosses match the inflected form's parsing (perfect -> perfect, deponent -> deponent, accusative -> accusative, infinitive -> infinitive). BOX definitions describe the LEMMA in its dictionary form (see LEMMA vs FORM above).
- A "phrase" key must be a contiguous substring copied exactly from the passage. No "..." or discontinuous spans; no lemmas that do not appear verbatim.
- A term is EITHER a key_term OR an inline gloss — never both.
- Before emitting each box term, check: is this word genuinely hard, not already introduced, and does my definition use common vocabulary?
- Before emitting each inline gloss, check: is this word genuinely rare here, not a key term, and is my synonym the same parsing?

ALREADY INTRODUCED (do not re-list these as key_terms): {introduced}

Passage:
```
{chunk_text}
```

Respond with ONLY a JSON object of this shape: {{"summary": "...", "key_terms": [{{"lemma":"...","form":"...","gloss":"..."}}], "glosses": [["phrase","gloss"]]}}"""


def prompt_vars(work_type: str | None) -> dict:
    """Per-work-type substitutions for PROMPT_TEMPLATE.

    The gloss/summary language is ALWAYS Attic (the learner's target dialect).
    For epic the source text is Epic/Ionic, so a dialect note tells the model to
    produce Attic help for Epic forms rather than mirroring the source dialect.
    """
    if work_type == "epic":
        return {
            "passage_desc": "a passage of roughly forty lines of dactylic hexameter",
            "dialect_note": (
                " The source text is Epic/Ionic Greek; every gloss, definition, "
                "and summary must be in Attic \u2014 the learner's target dialect \u2014 "
                "giving Attic equivalents for Epic forms where they differ."
            ),
        }
    return {"passage_desc": "a passage of a few Stephanus sections", "dialect_note": ""}


# --------------------------------------------------------------------------- #
# chunking
# --------------------------------------------------------------------------- #
def build_chunks(segments: list[dict], sections_per_chunk: int,
                  work_type: str | None = None) -> list[dict]:
    """Group segments into chunks, preserving order.

    Prose (dialogue): group by section_n, N sections per chunk.
    Epic (verse): greedy line-budget cards that never split a speech or
    narrative run; ``sections_per_chunk`` is repurposed as the target line count
    per card (hardmax ~1.75x), flushing at book boundaries.
    """
    if work_type == "epic":
        target = sections_per_chunk or 40
        hardmax = max(int(target * 1.75), target + 30)
        return build_chunks_epic(segments, target, hardmax)
    chunks: list[dict] = []
    cur: list[dict] = []
    cur_sections: list[str] = []
    last_section = object()

    def flush():
        nonlocal cur, cur_sections
        if cur:
            chunks.append(_make_chunk(len(chunks), cur, cur_sections))
            cur, cur_sections = [], []

    for seg in segments:
        sn = seg.get("section_n")
        if sn != last_section:
            if cur_sections and len(cur_sections) >= sections_per_chunk:
                flush()
            if sn is not None and sn not in cur_sections:
                cur_sections.append(sn)
            last_section = sn
        cur.append(seg)
    flush()
    return chunks


def _make_chunk(index: int, segs: list[dict], sections: list[str]) -> dict:
    lines = []
    for s in segs:
        head = f"[{s['ref']}"
        if s.get("label"):
            head += f" {s['label'].strip()}"
        head += "]"
        lines.append(f"{head} {s['text']}")
    rng = sections[0] if sections else ""
    if len(sections) > 1:
        rng = f"{sections[0]}–{sections[-1]}"
    return {
        "index": index,
        "range": rng,
        "section_ns": sections,
        "segment_refs": [s["ref"] for s in segs],
        "text": "\n\n".join(lines),
    }


# --------------------------------------------------------------------------- #
# epic verse chunker — greedy line budget, never splits a speech/narrative run
# --------------------------------------------------------------------------- #
def build_chunks_epic(segments: list[dict], target: int = 40,
                      hardmax: int = 70) -> list[dict]:
    """Accumulate whole segments (each a speech or narrative run) into cards of
    ~``target`` lines, flushing when the target is reached, when the next unit
    would exceed ``hardmax``, or when the book changes. A unit is never split."""
    chunks: list[dict] = []
    cur: list[dict] = []
    cur_lines = 0
    cur_book: str | None = None
    for seg in segments:
        n = len(seg.get("lines") or [])
        book = seg.get("section_n")
        if cur and (book != cur_book or cur_lines >= target
                    or cur_lines + n > hardmax):
            chunks.append(_epic_chunk(len(chunks), cur))
            cur, cur_lines = [], 0
        cur.append(seg)
        cur_lines += n
        cur_book = book
    if cur:
        chunks.append(_epic_chunk(len(chunks), cur))
    return chunks


def _epic_chunk(index: int, segs: list[dict]) -> dict:
    books: list[str] = []
    for s in segs:
        b = s.get("section_n")
        if b is not None and b not in books:
            books.append(b)
    chunk = _make_chunk(index, segs, books)
    chunk["range"] = _epic_range(segs[0]["ref"], segs[-1]["ref"])
    return chunk


def _epic_range(first_ref: str, last_ref: str) -> str:
    """'24.1-32' + '24.33-54' -> '24.1-54' (one book) / '23.760-24.40' (cross)."""
    m1 = re.match(r"(\d+)\.(\d+)-(\d+)", first_ref or "")
    m2 = re.match(r"(\d+)\.(\d+)-(\d+)", last_ref or "")
    if not (m1 and m2):
        return f"{first_ref}-{last_ref}"
    b1, s1 = m1.group(1), m1.group(2)
    b2, e2 = m2.group(1), m2.group(3)
    if b1 == b2:
        return f"{b1}.{s1}-{e2}"
    return f"{b1}.{s1}-{b2}.{e2}"


# --------------------------------------------------------------------------- #
# OpenRouter call
# --------------------------------------------------------------------------- #
def _format_introduced(lemmas: list[str]) -> str:
    """Render the already-introduced key-term lemmas for the prompt."""
    lemmas = [l for l in lemmas if l]
    if not lemmas:
        return "(none yet — this is the first passage)"
    return ", ".join(lemmas)


def _extract_json(content: str) -> dict:
    content = content.strip()
    # strip ```json ... ``` fences
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", content, re.DOTALL)
    if fence:
        content = fence.group(1).strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    # last resort: outermost { ... }
    start, end = content.find("{"), content.rfind("}")
    if start >= 0 and end > start:
        return json.loads(content[start : end + 1])
    raise ValueError("no JSON object found in response")


def _validate(obj: dict) -> dict:
    """Normalize a model response to {summary, key_terms, glosses}.

    Lenient about input shape so model drift (a key_term given as a list,
    a missing "form", etc.) still yields usable data. Backward-compatible:
    an old annotation file with only {summary, glosses} validates to key_terms=[].
    """
    if not isinstance(obj, dict):
        raise ValueError("response is not a JSON object")
    summary = obj.get("summary", "")
    if not isinstance(summary, str):
        summary = str(summary)
    key_terms = []
    for kt in obj.get("key_terms", []) or []:
        # accept {lemma,form,gloss}; also accept [lemma, gloss] or [lemma, form, gloss]
        if isinstance(kt, dict):
            lemma = str(kt.get("lemma", "")).strip()
            form = str(kt.get("form", "")).strip() or lemma
            gloss = str(kt.get("gloss", kt.get("def", kt.get("definition", "")))).strip()
        elif isinstance(kt, list) and len(kt) >= 2 and all(isinstance(x, str) for x in kt):
            lemma = kt[0].strip()
            form = (kt[1].strip() if len(kt) >= 3 and kt[1] else lemma)
            gloss = (kt[-1]).strip()
        else:
            continue
        if lemma and gloss:
            key_terms.append({"lemma": lemma, "form": form, "gloss": gloss})
    glosses = []
    for g in obj.get("glosses", []):
        if isinstance(g, list) and len(g) == 2 and all(isinstance(x, str) for x in g):
            glosses.append([g[0], g[1]])
    return {"summary": summary, "key_terms": key_terms, "glosses": glosses}


def call_model(chunk_text: str, title: str, model: str, api_key: str,
               introduced: list[str] | None = None,
               passage_desc: str = "a passage of a few Stephanus sections",
               dialect_note: str = "") -> dict:
    prompt = PROMPT_TEMPLATE.format(
        title=title, chunk_text=chunk_text,
        introduced=_format_introduced(introduced or []),
        passage_desc=passage_desc, dialect_note=dialect_note,
    )
    body = json.dumps(
        {
            "model": model,
            "temperature": 0.2,
            "messages": [{"role": "user", "content": prompt}],
        }
    ).encode()
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-Title": "rhadios",
    }
    last_err = None
    for attempt in range(5):
        try:
            req = urllib.request.Request(OPENROUTER_URL, data=body, headers=headers)
            with urllib.request.urlopen(req, timeout=120) as r:
                resp = json.loads(r.read().decode())
            content = resp["choices"][0]["message"]["content"]
            return _validate(_extract_json(content))
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code in (429, 500, 502, 503, 504) and attempt < 4:
                time.sleep(2 ** attempt)
                continue
            raise
        except (urllib.error.URLError, TimeoutError, ValueError, KeyError) as e:
            last_err = e
            if attempt < 4:
                time.sleep(2 ** attempt)
                continue
            raise
    raise RuntimeError(f"call_model exhausted retries: {last_err}")


# --------------------------------------------------------------------------- #
# driver
# --------------------------------------------------------------------------- #
def _path_for(ann_dir: str, work_id: str, index: int) -> str:
    return os.path.join(ann_dir, f"{work_id}__chunk{index:02d}.json")


def _load_validated(path: str) -> dict | None:
    """Load + validate a cached annotation; None if missing or corrupt."""
    if not os.path.exists(path):
        return None
    try:
        return _validate(json.load(open(path, encoding="utf-8")))
    except Exception:
        return None


def introduced_from_cache(chunks: list[dict], work_id: str,
                          ann_dir: str) -> tuple[list[str], int]:
    """Rebuild the introduced key-term set from cached annotations of the given
    chunks (best-effort: missing chunks contribute nothing). Returns
    (lemmas, n_cached) — used to seed threading when annotating a subset
    (e.g. one book) so words already taught in earlier books are not re-taught.
    """
    introduced: list[str] = []
    n_cached = 0
    for ch in chunks:
        cached = _load_validated(_path_for(ann_dir, work_id, ch["index"]))
        if cached is not None:
            introduced = _merge_terms(introduced, cached)
            n_cached += 1
    return introduced, n_cached


def annotate(
    chunks: list[dict],
    title: str,
    work_id: str,
    model: str,
    api_key: str,
    ann_dir: str,
    force: bool = False,
    limit: int | None = None,
    passage_desc: str = "a passage of a few Stephanus sections",
    dialect_note: str = "",
    introduced_seed: list[str] | None = None,
) -> list[dict]:
    """Annotate chunks in order, threading already-introduced key terms forward.

    Sequential because chunk N's prompt depends on key terms from chunks 0..N-1.
    On resume, cached prefix chunks are read back to rebuild the introduced set.
    ``introduced_seed`` pre-seeds that set (e.g. terms from earlier books when
    annotating one book in isolation); it is folded in before the first chunk.
    """
    os.makedirs(ann_dir, exist_ok=True)
    results: dict[int, dict] = {}

    # First pass: load every cached chunk so results is populated for the
    # return value, and count pending work (for messaging + limit).
    pending = 0
    for ch in chunks:
        path = _path_for(ann_dir, work_id, ch["index"])
        if not force:
            cached = _load_validated(path)
            if cached is not None:
                results[ch["index"]] = cached
                continue
        pending += 1
    if limit is not None:
        pending = min(pending, limit)

    if pending == 0:
        print(f"[annotate] all {len(chunks)} chunks already cached.")
    else:
        note = " (sequential; key-term dedup across chunks)"
        print(f"[annotate] {pending} to annotate "
              f"({len(results)} cached){note}.")

    # Second pass: walk in index order, accumulating introduced key-term lemmas.
    # Cached chunks contribute their lemmas; pending chunks are annotated with
    # the current introduced set, then contribute their own. limit caps how
    # many NEW annotations we perform. introduced_seed pre-loads terms from
    # chunks outside this subset (e.g. earlier books) so they aren't re-taught.
    introduced: list[str] = list(introduced_seed or [])
    annotated_this_run = 0
    for ch in chunks:
        idx = ch["index"]
        path = _path_for(ann_dir, work_id, idx)
        res = results.get(idx)
        if res is not None and not force:
            # cached: just fold its key terms into the running set
            introduced = _merge_terms(introduced, res)
            continue
        # pending (or forced): respect the limit on NEW work
        if res is None and limit is not None and annotated_this_run >= limit:
            # over budget AND nothing cached to fold — stop entirely
            break
        try:
            res = call_model(ch["text"], title, model, api_key, introduced,
                             passage_desc=passage_desc, dialect_note=dialect_note)
        except Exception as e:
            print(f"[annotate] FAILED chunk {idx:02d}: {e}")
            break  # later chunks would be missing their introduced context
        with open(path, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=2)
        results[idx] = res
        introduced = _merge_terms(introduced, res)
        annotated_this_run += 1
        print(f"[annotate] chunk {idx:02d} done ({annotated_this_run}/{pending})")

    return [results.get(ch["index"]) for ch in chunks]


def _merge_terms(introduced: list[str], res: dict) -> list[str]:
    """Append any new key-term lemmas from ``res`` to the introduced list
    (preserving first-seen order, deduped)."""
    seen = set(introduced)
    out = list(introduced)
    for kt in res.get("key_terms", []):
        lem = kt.get("lemma", "").strip()
        if lem and lem not in seen:
            seen.add(lem)
            out.append(lem)
    return out
