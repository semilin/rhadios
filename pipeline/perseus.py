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
# Homer (dactylic hexameter)  — SEAM
#   <div book n="1"> ... <l n="1">...</l>; chunk by ~24-line cards.
#   ref = "book.line" -> "Il.1.1"; speaker=None (narrator / character speech
#   carried by <sp> in some editions).
# --------------------------------------------------------------------------- #
def parse_epic(root) -> list[dict]:
    raise NotImplementedError(
        "parse_epic: implement <div book>/<l n> extraction; group into ~24-line "
        "cards; ref 'book.line'. Speaker from <sp> where present, else None."
    )
