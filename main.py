"""rhadios pipeline CLI.

Usage:
  python main.py list
  python main.py fetch   meno
  python main.py parse   meno
  python main.py annotate meno [--force] [--limit N] [--book N]
  python main.py merge   meno
  python main.py all     meno [--force] [--limit N] [--book N]

Run from the reader/ directory. OPENROUTER_API_KEY must be set for `annotate`.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    print("rhadios requires Python 3.11+ (tomllib).", file=sys.stderr)
    raise

from pipeline import annotate as A
from pipeline import merge as M
from pipeline import perseus as P

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
DOCS = os.path.join(HERE, "docs")
XML_DIR = os.path.join(DATA, "xml")
MANIFEST_DIR = os.path.join(DATA, "manifest")
ANN_DIR = os.path.join(DATA, "annotations")


def load_config() -> dict:
    with open(os.path.join(HERE, "config.toml"), "rb") as f:
        return tomllib.load(f)


def works(cfg: dict) -> dict:
    return cfg.get("work", {})


def get_work(cfg: dict, wid: str) -> dict:
    w = works(cfg).get(wid)
    if not w:
        sys.exit(f"unknown work id: {wid!r}. configured: {list(works(cfg))}")
    w = dict(w)
    w.setdefault("id", wid)
    w.setdefault("model", A.DEFAULT_MODEL)
    w.setdefault("type", "dialogue")
    w.setdefault("sections_per_chunk", 3)
    return w


def manifest_path(wid: str) -> str:
    return os.path.join(MANIFEST_DIR, f"{wid}.json")


def out_path(wid: str) -> str:
    return os.path.join(DOCS, f"{wid}.json")


# --------------------------------------------------------------------------- #
def cmd_list(cfg, args):
    for wid, w in works(cfg).items():
        ann_exists = os.path.exists(out_path(wid))
        print(f"  {wid:12} {w.get('title','?'):20} type={w.get('type','?'):8} "
              f"cts={w.get('cts','?')}  {'[built]' if ann_exists else ''}")


def cmd_fetch(cfg, args):
    w = get_work(cfg, args.work)
    path = P.fetch_work(w["cts"], XML_DIR)
    print(f"[fetch] {w['cts']} -> {path}")


def cmd_parse(cfg, args):
    w = get_work(cfg, args.work)
    xml_path = os.path.join(XML_DIR, f"{w['cts']}.xml")
    if not os.path.exists(xml_path):
        P.fetch_work(w["cts"], XML_DIR)
    segments = P.parse(xml_path, w["type"])
    chunks = A.build_chunks(segments, w["sections_per_chunk"], w["type"])
    os.makedirs(MANIFEST_DIR, exist_ok=True)
    manifest = {
        "work_id": w["id"],
        "cts": w["cts"],
        "title": w["title"],
        "author": w["author"],
        "type": w["type"],
        "sections_per_chunk": w["sections_per_chunk"],
        "segments": segments,
        "chunks": chunks,
    }
    with open(manifest_path(w["id"]), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"[parse] {len(segments)} segments -> {len(chunks)} chunks -> {manifest_path(w['id'])}")


def _book_chunks(manifest: dict, book) -> tuple[list[dict], list[dict]]:
    """Split a manifest's chunks into (target, prefix) for the given book.

    target = chunks whose ``section_ns`` contains the book (e.g. epic book 24);
    prefix = every chunk before the first target chunk, used to seed key-term
    threading from already-annotated earlier books. Exits if no match.
    """
    chunks = manifest["chunks"]
    key = str(book)
    target = [c for c in chunks if key in (c.get("section_ns") or [])]
    if not target:
        sys.exit(f"no chunks for book {book!r} in manifest "
                 f"{manifest.get('work_id', '?')!r}")
    first_idx = target[0]["index"]
    prefix = [c for c in chunks if c["index"] < first_idx]
    return target, prefix


def cmd_annotate(cfg, args):
    w = get_work(cfg, args.work)
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        sys.exit("OPENROUTER_API_KEY is not set.")
    manifest = json.load(open(manifest_path(w["id"]), encoding="utf-8"))
    pv = A.prompt_vars(w.get("type"))
    chunks = manifest["chunks"]
    seed: list[str] = []
    if args.book is not None:
        target, prefix = _book_chunks(manifest, args.book)
        seed, n_cached = A.introduced_from_cache(prefix, w["id"], ANN_DIR)
        chunks = target
        print(f"[annotate] book {args.book}: {len(target)} chunks to annotate; "
              f"threading from {n_cached}/{len(prefix)} cached prefix chunks")
        if n_cached < len(prefix):
            print(f"[annotate]   note: {len(prefix) - n_cached} earlier chunk(s) "
                  "are not annotated — key-term threading is best-effort")
    A.annotate(
        chunks=chunks,
        title=w["title"],
        work_id=w["id"],
        model=w["model"],
        api_key=api_key,
        ann_dir=ANN_DIR,
        force=args.force,
        limit=args.limit,
        passage_desc=pv["passage_desc"],
        dialect_note=pv["dialect_note"],
        introduced_seed=seed,
    )


def _load_annotations(wid: str, n_chunks: int) -> list:
    out = []
    for i in range(n_chunks):
        path = os.path.join(ANN_DIR, f"{wid}__chunk{i:02d}.json")
        if os.path.exists(path):
            out.append(json.load(open(path, encoding="utf-8")))
        else:
            out.append(None)
    return out


def cmd_merge(cfg, args):
    w = get_work(cfg, args.work)
    manifest = json.load(open(manifest_path(w["id"]), encoding="utf-8"))
    annotations = _load_annotations(w["id"], len(manifest["chunks"]))
    doc = M.merge(w, manifest, annotations, out_path(w["id"]))
    # refresh works index for the web app
    index = [
        {"id": wid, "title": ww.get("title", wid), "author": ww.get("author", "")}
        for wid, ww in works(cfg).items()
        if os.path.exists(out_path(wid))
    ]
    with open(os.path.join(DOCS, "works.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)
    return doc


def cmd_all(cfg, args):
    cmd_fetch(cfg, args)
    cmd_parse(cfg, args)
    cmd_annotate(cfg, args)
    cmd_merge(cfg, args)


def main():
    ap = argparse.ArgumentParser(prog="rhadios")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list").set_defaults(func=cmd_list)

    def work_args(p):
        p.add_argument("work")

    def book_args(p):
        p.add_argument("--book", "-b", default=None, metavar="N",
                       help="annotate only the given book (epic); threads key-terms "
                            "from cached earlier books")

    p = sub.add_parser("fetch"); work_args(p); p.set_defaults(func=cmd_fetch)
    p = sub.add_parser("parse"); work_args(p); p.set_defaults(func=cmd_parse)
    p = sub.add_parser("annotate"); work_args(p); book_args(p)
    p.add_argument("--force", action="store_true")
    p.add_argument("--limit", type=int, default=None)
    p.set_defaults(func=cmd_annotate)
    p = sub.add_parser("merge"); work_args(p); p.set_defaults(func=cmd_merge)
    p = sub.add_parser("all"); work_args(p); book_args(p)
    p.add_argument("--force", action="store_true")
    p.add_argument("--limit", type=int, default=None)
    p.set_defaults(func=cmd_all)

    args = ap.parse_args()
    cfg = load_config()
    args.func(cfg, args)


if __name__ == "__main__":
    main()
