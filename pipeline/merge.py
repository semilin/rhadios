"""Merge manifest segments + chunk annotations into docs/<id>.json.

All gloss-to-text matching happens HERE (exact, then fuzzy), so the web app
receives pre-computed character offsets and does no matching at all.
"""
from __future__ import annotations

import json
import os
import re
from . import greeknorm


def merge(work: dict, manifest: dict, annotations: list[dict | None], out_path: str):
    """work: config entry. manifest: {segments, chunks, ...}. annotations: per-chunk."""
    segments = manifest["segments"]
    chunks = manifest["chunks"]

    # Build paragraph list in order, remembering which chunk each belongs to.
    paragraphs = []
    seg_idx = 0
    para_chunk = []  # parallel: chunk index per paragraph
    for ci, ch in enumerate(chunks):
        n_segs = len(ch["segment_refs"])
        for _ in range(n_segs):
            if seg_idx >= len(segments):
                break
            seg = segments[seg_idx]
            paragraphs.append(
                {
                    "speaker": seg["speaker"],
                    "label": seg["label"],
                    "ref": seg["ref"],
                    "section_n": seg["section_n"],
                    "chunk": ci,
                    "text": seg["text"],
                    "lines": seg.get("lines"),
                }
            )
            para_chunk.append(ci)
            seg_idx += 1

    # Apply glosses: for each chunk, for each of its paragraphs, find all spans.
    seen_gloss_pairs = set()
    gloss_list = []
    summaries = []
    unmatched = []

    for ci, ann in enumerate(annotations):
        if not ann:
            summaries.append({"chunk": ci, "range": chunks[ci]["range"], "text": "", "key_terms": []})
            continue
        summaries.append(
            {"chunk": ci, "range": chunks[ci]["range"],
             "text": ann.get("summary", ""),
             "key_terms": ann.get("key_terms", [])}
        )
        for key, gloss in ann.get("glosses", []):
            pair = (key, gloss)
            if pair not in seen_gloss_pairs:
                seen_gloss_pairs.add(pair)
                gloss_list.append([key, gloss])
            # find spans across this chunk's paragraphs
            found_any = False
            for pi in range(len(paragraphs)):
                if para_chunk[pi] != ci:
                    continue
                spans = greeknorm.find_all_gloss_spans(paragraphs[pi]["text"], key)
                if spans:
                    found_any = True
                    paragraphs[pi].setdefault("glosses", [])
                    for (start, end) in spans:
                        paragraphs[pi]["glosses"].append(
                            {"start": start, "end": end, "gloss": gloss}
                        )
            if not found_any:
                unmatched.append({"chunk": ci, "key": key, "gloss": gloss})

    # sort each paragraph's glosses by start; drop overlaps (keep earliest, longest)
    for p in paragraphs:
        gs = p.get("glosses", [])
        gs.sort(key=lambda g: (g["start"], -(g["end"] - g["start"])))
        kept = []
        last_end = -1
        for g in gs:
            if g["start"] >= last_end:
                kept.append(g)
                last_end = g["end"]
        p["glosses"] = kept

    # group paragraphs into sections for rendering
    sections = []
    cur_n = None
    cur = None
    for p in paragraphs:
        if p["section_n"] != cur_n:
            cur = {"n": p["section_n"], "paragraphs": []}
            sections.append(cur)
            cur_n = p["section_n"]
        cur["paragraphs"].append(p)

    if work.get("type") == "epic" and len(sections) > 1:
        return _write_epic(work, sections, summaries, out_path)

    doc = {
        "meta": {
            "id": work["id"],
            "title": work["title"],
            "author": work["author"],
            "type": work["type"],
        },
        "sections": sections,
        "summaries": summaries,
        "glosses": gloss_list,
        "unmatched": unmatched,
    }
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    print(
        f"[merge] wrote {out_path}: {len(paragraphs)} paragraphs, "
        f"{len(gloss_list)} glosses ({len(unmatched)} unmatched), "
        f"{sum(len(s.get('key_terms',[])) for s in summaries)} key terms."
    )
    return doc


# --------------------------------------------------------------------------- #
# epic: split into per-book reader docs + a lightweight index.
# Each book gets its own docs/<id>/<book>.json so the web app renders one book
# at a time (a 24-book epic is an unmanageable single scroll). Annotation still
# threads across all books; the split is a render/payload concern only.
# --------------------------------------------------------------------------- #
def _book_range(section: dict) -> str:
    """Line span of a book from its paragraphs' refs: '24.1-804'."""
    refs = [p["ref"] for p in section["paragraphs"] if p.get("ref")]
    if not refs:
        return ""
    m1 = re.match(r"(\d+)\.(\d+)-(\d+)", refs[0])
    m2 = re.match(r"(\d+)\.(\d+)-(\d+)", refs[-1])
    if not (m1 and m2):
        return ""
    return f"{m1.group(1)}.{m1.group(2)}-{m2.group(3)}"


def _write_epic(work: dict, sections: list[dict], summaries: list[dict],
                out_path: str) -> dict:
    """Write docs/<id>/<book>.json per book + docs/<id>.json index."""
    books = [{"n": s["n"], "range": _book_range(s)} for s in sections]
    base = os.path.dirname(out_path)
    stem = os.path.basename(out_path)
    stem = stem[:-5] if stem.endswith(".json") else stem
    book_dir = os.path.join(base, stem)
    os.makedirs(book_dir, exist_ok=True)

    total_paras = 0
    total_glossed = 0
    for s in sections:
        chunk_ids = {p["chunk"] for p in s["paragraphs"]}
        book_summaries = [sm for sm in summaries if sm["chunk"] in chunk_ids]
        doc = {
            "meta": {
                "id": work["id"],
                "title": work["title"],
                "author": work["author"],
                "type": work["type"],
                "book": s["n"],
                "book_range": _book_range(s),
                "books": books,
            },
            "sections": [s],
            "summaries": book_summaries,
        }
        with open(os.path.join(book_dir, f"{s['n']}.json"), "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
        total_paras += len(s["paragraphs"])
        total_glossed += sum(len(p.get("glosses", [])) for p in s["paragraphs"])

    index = {
        "meta": {
            "id": work["id"],
            "title": work["title"],
            "author": work["author"],
            "type": work["type"],
        },
        "books": books,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)
    print(
        f"[merge] wrote {len(sections)} book files to {book_dir}/ "
        f"({total_paras} paragraphs, {total_glossed} inline gloss spans); "
        f"index {out_path}."
    )
    return index
