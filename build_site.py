#!/usr/bin/env python3
"""Assemble the static reader bundle (per-unit) the browser serves.

For each work + unit it emits the text, the grammar/senses overlays per reader language, and
a per-unit GLOSSARY SLICE (only the lemmas that occur in that unit) so the browser loads a
few small files per unit instead of the whole 100-unit corpus or the full glossary. Writes a
manifest the unit-browser reads. Serving makes zero LLM/network calls.

Layout:
  site/data/manifest.json
  site/data/works/<t>/<slug>/units/<cid>.json
  site/data/works/<t>/<slug>/grammar__<k>/<cid>.json
  site/data/works/<t>/<slug>/senses__<k>/<cid>.json
  site/data/works/<t>/<slug>/glossary__<k>/<cid>.json   (slice)
"""
from __future__ import annotations

import json
import re
import shutil

import glossary as glossary_mod
import store

SITE = store.ROOT / "site"
DATA = SITE / "data"
PAIR_RE = re.compile(r"^([a-z]{2,3})__([a-z]{2,3})\.json$")


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _unit_lemmas(unit: dict) -> set[str]:
    return {t["lemma"] for ln in unit.get("lines", {}).values() for t in ln["tokens"]
            if t["pos"] not in glossary_mod.SKIP_POS}


def build() -> dict:
    if DATA.exists():
        shutil.rmtree(DATA)
    DATA.mkdir(parents=True, exist_ok=True)

    # which reader languages have a glossary, per target
    glos = {}
    for gp in store.GLOSSARIES.glob("*.json"):
        m = PAIR_RE.match(gp.name)
        if m:
            glos.setdefault(m.group(1), {})[m.group(2)] = json.loads(
                gp.read_text(encoding="utf-8"))

    manifest = []
    for target_dir in sorted(p for p in store.WORKS.glob("*") if p.is_dir()):
        target = target_dir.name
        for work_dir in sorted(p for p in target_dir.glob("*") if p.is_dir()):
            slug = work_dir.name
            meta = store.load_meta(target, slug)
            if not meta:
                continue
            on_disk = set(store.list_units(target, slug))
            units_out = []
            for c in meta.get("units", []):
                cid = c["id"]
                base = {**{k: c[k] for k in ("id", "group", "num", "title")},
                        "incipit": c.get("incipit", ""), "lines": c.get("lines", 0)}
                if cid not in on_disk:
                    # known but not yet processed -> listed in the nav, grayed out (no pairs)
                    units_out.append({**base, "pairs": []})
                    continue
                unit = store.load_unit(target, slug, cid)
                _write(DATA / "works" / target / slug / "units" / f"{cid}.json", unit)

                pairs = []
                lemmas = _unit_lemmas(unit)
                for known in glos.get(target, {}):
                    gpath = store.grammar_path(target, slug, cid, known)
                    if not gpath.exists():
                        continue
                    _write(DATA / "works" / target / slug / f"grammar__{known}" / f"{cid}.json",
                           store.load_grammar(target, slug, cid, known))
                    _write(DATA / "works" / target / slug / f"senses__{known}" / f"{cid}.json",
                           store.load_senses(target, slug, cid, known))
                    master = glos[target][known]
                    slice_ = {lm: master[lm] for lm in lemmas if lm in master}
                    _write(DATA / "works" / target / slug / f"glossary__{known}" / f"{cid}.json",
                           slice_)
                    pairs.append(known)
                units_out.append({**base,
                                   "lines": c.get("lines", len(unit.get("lines", {}))),
                                   "pairs": pairs})

            ready = [c for c in units_out if c["pairs"]]
            if ready:
                manifest.append({
                    "target": target, "slug": slug,
                    "title": meta.get("title", slug), "author": meta.get("author", ""),
                    "source": meta.get("source", ""), "unit": meta.get("unit", "Unit"),
                    "groups": meta.get("groups", []),
                    "units": units_out,
                })

    _write(DATA / "manifest.json", manifest)
    for w in manifest:
        ready = sum(1 for c in w["units"] if c["pairs"])
        print(f"site: {w['title']} — {ready}/{len(w['units'])} units ready "
              f"({', '.join(w['groups'])})")
    if not manifest:
        print("site: no ready units yet")
    return {"works": manifest}


if __name__ == "__main__":
    build()
