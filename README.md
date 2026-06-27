# ῥᾳδίως (rhadios)

Another immersive Ancient Greek reader experiment. This one, unlike
[Shark Reader](https://github.com/semilin/shark-reader), is a simple
static website. It is basically a test of how capable LLMs should be
at automating the creation of "good enough" tiered reader materials
for learners. It is also a pedagogical tool I am using for my own
Greek study.

## How it works

1. **fetch** — downloads a Perseus TEI XML (e.g. `tlg0059.tlg024.perseus-grc2`,
   Plato's *Meno*) from `PerseusDL/canonical-greekLit` into `data/xml/`.
2. **parse** — extracts the text into a flat list of segments
   `{speaker, label, ref, section_n, text, lines}`. Downstream stages are
   type-agnostic; only `perseus.parse_*` knows the TEI shape. Sections are
   grouped into chunks (`sections_per_chunk` per work).
3. **annotate** — for each chunk, POSTs the chunk text + a glossing prompt to
   OpenRouter (`google/gemini-3-flash-preview`). Each chunk yields a Greek
   `summary`, a small `key_terms` list (the vocab box), and a sparse `glosses`
   list (inline one-off synonyms). Processing is sequential so each chunk's
   prompt is told which key terms were already introduced, avoiding re-teaching
   the same word every passage. Results cache per chunk at
   `data/annotations/<id>__chunkNN.json`, so a failed run resumes without
   re-paying for finished chunks.
4. **merge** — for each chunk's glosses, finds all whole-token matches in that
   chunk's paragraphs (exact match first, then a fuzzy fallback that strips
   accents / normalizes elision so model drift still matches). Writes
   `docs/<id>.json` with pre-computed character offsets so the web app does
   zero matching. For multi-book works (epic), writes one reader doc per book
   at `docs/<id>/<book>.json` plus a lightweight index at `docs/<id>.json`, so
   each book is its own view. Also refreshes `docs/works.json`.

## Usage

Requires Python 3.11+ (for `tomllib`) and an `OPENROUTER_API_KEY`.

```bash
python main.py list
export OPENROUTER_API_KEY=sk-or-...
python main.py all meno                 # fetch -> parse -> annotate -> merge
# smoke-test one chunk before paying for the whole run:
python main.py annotate meno --limit 1
python main.py merge meno
# annotate one book of a multi-book (epic) work; key-terms thread from
# already-annotated earlier books so words aren't re-taught:
python main.py annotate iliad --book 1 --limit 1   # smoke-test book 1
python main.py annotate iliad --book 24             # all of book 24
python main.py merge iliad
# serve locally:
cd docs && python -m http.server 8000
# open a work:    http://localhost:8000/?w=meno
# open a book:    http://localhost:8000/?w=iliad        (defaults to book 1)
#                 http://localhost:8000/?w=iliad&b=24
```

`--force` re-annotates (ignores the cache); `--limit N` annotates only the
first N pending chunks.
