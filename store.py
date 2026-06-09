#!/usr/bin/env python3
"""Sole reader/writer for the reader/ data layer (per-unit model).

Scales to large works (the whole Divine Comedy = 100 units) by splitting every work into
ONE FILE PER UNIT, so the browser and the pipeline only ever touch one unit at a time.

  works/<target>/<slug>/meta.json                      work metadata + unit manifest
  works/<target>/<slug>/units/<unitId>.json          text: lines / sentences / segments
  works/<target>/<slug>/grammar__<known>/<unitId>.json  {segments:{segId:expl}, sentences:{sid:tr}}
  works/<target>/<slug>/senses__<known>/<unitId>.json   {lineId:{tokIdx:senseIdx}}
  glossaries/<target>__<known>.json                    SHARED across all works/units in <target>

A unitId is group-namespaced, e.g. "inferno-5", so the three Unit I's never collide.
All writes are atomic (tmp + rename) → safe to interrupt and resume.
"""
from __future__ import annotations

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent           # reader/
WORKS = ROOT / "works"
GLOSSARIES = ROOT / "glossaries"


# ---- generic json io -------------------------------------------------------
def _read(path: pathlib.Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: pathlib.Path, data: dict) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


# ---- paths -----------------------------------------------------------------
def work_dir(target: str, slug: str) -> pathlib.Path:
    return WORKS / target / slug


def meta_path(target: str, slug: str) -> pathlib.Path:
    return work_dir(target, slug) / "meta.json"


def unit_path(target: str, slug: str, unit_id: str) -> pathlib.Path:
    return work_dir(target, slug) / "units" / f"{unit_id}.json"


def grammar_path(target: str, slug: str, unit_id: str, known: str) -> pathlib.Path:
    return work_dir(target, slug) / f"grammar__{known}" / f"{unit_id}.json"


def senses_path(target: str, slug: str, unit_id: str, known: str) -> pathlib.Path:
    return work_dir(target, slug) / f"senses__{known}" / f"{unit_id}.json"


def glossary_path(target: str, known: str) -> pathlib.Path:
    return GLOSSARIES / f"{target}__{known}.json"


# ---- discovery -------------------------------------------------------------
def list_units(target: str, slug: str) -> list[str]:
    """Unit ids that have a text file, ordered by the meta manifest when available."""
    cdir = work_dir(target, slug) / "units"
    on_disk = {p.stem for p in cdir.glob("*.json")} if cdir.exists() else set()
    meta = load_meta(target, slug)
    ordered = [c["id"] for c in meta.get("units", []) if c["id"] in on_disk]
    extra = sorted(on_disk - set(ordered))
    return ordered + extra


# ---- typed accessors -------------------------------------------------------
def load_meta(target: str, slug: str) -> dict:
    return _read(meta_path(target, slug))


def save_meta(target: str, slug: str, data: dict) -> pathlib.Path:
    return _write(meta_path(target, slug), data)


def load_unit(target: str, slug: str, unit_id: str) -> dict:
    return _read(unit_path(target, slug, unit_id))


def save_unit(target: str, slug: str, unit_id: str, data: dict) -> pathlib.Path:
    return _write(unit_path(target, slug, unit_id), data)


def load_grammar(target: str, slug: str, unit_id: str, known: str) -> dict:
    return _read(grammar_path(target, slug, unit_id, known))


def save_grammar(target: str, slug: str, unit_id: str, known: str, data: dict) -> pathlib.Path:
    return _write(grammar_path(target, slug, unit_id, known), data)


def load_senses(target: str, slug: str, unit_id: str, known: str) -> dict:
    return _read(senses_path(target, slug, unit_id, known))


def save_senses(target: str, slug: str, unit_id: str, known: str, data: dict) -> pathlib.Path:
    return _write(senses_path(target, slug, unit_id, known), data)


def load_glossary(target: str, known: str) -> dict:
    return _read(glossary_path(target, known))


def save_glossary(target: str, known: str, data: dict) -> pathlib.Path:
    return _write(glossary_path(target, known), data)
