"""Merge manifest segments + chunk annotations into docs/<id>.json.

All gloss-to-text matching happens HERE (exact, then fuzzy), so the web app
receives pre-computed character offsets and does no matching at all.
"""
from __future__ import annotations

import json
import os
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
