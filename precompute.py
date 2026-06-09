#!/usr/bin/env python3
"""Driver for the reader precompute — staged, resumable, group-aware.

Stages (each idempotent and re-runnable):
  segment   raw text -> per-unit text files + meta manifest   (free, local spaCy)
  glossary  -> shared glossaries/<t>__<k>.json                 (paid; dedup across all units)
  grammar   -> per-unit grammar__<k>/ + senses__<k>/          (paid; batched + parallel)
  site      assemble the static per-unit bundle               (free)

The served site makes zero LLM/network calls. The paid stages are parallel and resumable —
interrupt any time, re-run to continue.

Examples:
  python3 precompute.py --raw .raw/pg1012.txt --dry-run           # segment all + counts, no spend
  python3 precompute.py --stage glossary                          # gloss everything not yet glossed
  python3 precompute.py --stage grammar --group Inferno         # grammar for one group
  python3 precompute.py --stage grammar --units inferno-5        # one unit (calibration)
  python3 precompute.py --stage site                              # rebuild the bundle
"""
from __future__ import annotations

import argparse

import build_site
import glossary as glossary_mod
import grammar as grammar_mod
import store


def resolve_units(target, slug, group=None, units=None):
    """Map --group / --units to an ordered list of unit ids (None = all)."""
    if not group and not units:
        return None
    meta = store.load_meta(target, slug)
    all_ = meta.get("units", [])
    if units:
        want = set(units)
        return [c["id"] for c in all_ if c["id"] in want or str(c["num"]) in want]
    cl = group.lower()
    return [c["id"] for c in all_ if c["group"].lower() == cl]


def main():
    ap = argparse.ArgumentParser(description="Precompute the reader (staged)")
    ap.add_argument("--stage", choices=["segment", "glossary", "grammar", "site", "all"],
                    default="all")
    ap.add_argument("--raw", default=None, help="raw source (required for segment/all)")
    ap.add_argument("--target", default="it")
    ap.add_argument("--slug", default="dante-divine-comedy")
    ap.add_argument("--known", default="en")
    ap.add_argument("--group", default=None, help="limit to a group (Inferno/...)")
    ap.add_argument("--units", nargs="*", default=None, help="limit to unit ids/numbers")
    ap.add_argument("--title", default="The Divine Comedy")
    ap.add_argument("--author", default="Dante Alighieri")
    ap.add_argument("--source", default="Project Gutenberg eBook #1012 (public domain)")
    ap.add_argument("--spacy-model", default="it_core_news_sm")
    ap.add_argument("--format", choices=["headed", "prose", "marked"], default="headed",
                    dest="fmt")
    ap.add_argument("--unit", default="Unit", help="display label (Unit / Chapter)")
    ap.add_argument("--workers", type=int, default=60,
                    help="max concurrent LLM calls (AIMD ceiling; auto-throttles down on 429s)")
    ap.add_argument("--batch", type=int, default=12, help="glossary lemmas per call")
    ap.add_argument("--per-call", type=int, default=3, dest="per_call",
                    help="grammar sentences per call")
    ap.add_argument("--model", default=None)
    ap.add_argument("--dry-run", action="store_true",
                    help="segment + report counts only; no paid calls")
    args = ap.parse_args()

    do = lambda s: args.stage in (s, "all")  # noqa: E731

    if args.dry_run or do("segment"):
        if not args.raw:
            raise SystemExit("--raw is required to segment")
        import segment
        raw = open(args.raw, encoding="utf-8").read()
        summ = segment.build_work(raw, target=args.target, slug=args.slug, title=args.title,
                                  author=args.author, source=args.source,
                                  model=args.spacy_model, fmt=args.fmt, unit=args.unit,
                                  verbose=False)
        print(f"segment: {summ['units']} units, {summ['lines']} lines, "
              f"{summ['sentences']} sentences, {summ['segments']} segments, "
              f"{summ['lemmas']} unique lemmas")
        if args.dry_run:
            # REAL new-lemma count: this batch's content lemmas (SKIP_POS-filtered) not already
            # glossed — not a crude count subtraction (which wrongly assumed full overlap and
            # could read 0 once the glossary outgrew a batch's unique count).
            glossary = store.load_glossary(args.target, args.known)
            batch_lemmas = glossary_mod.content_lemmas(args.target, args.slug, summ["cids"])
            new_lemmas = sum(1 for lm in batch_lemmas if lm not in glossary)
            g_calls = -(-new_lemmas // args.batch)            # ceil
            gr_calls = -(-summ["sentences"] // args.per_call)
            print(f"\nDRY RUN — estimated paid calls for {args.known}:")
            print(f"  glossary: ~{g_calls} calls ({new_lemmas} new lemmas / {args.batch})")
            print(f"  grammar:  ~{gr_calls} calls ({summ['sentences']} sentences / {args.per_call})")
            print(f"  TOTAL:    ~{g_calls + gr_calls} LLM calls")
            return

    units = resolve_units(args.target, args.slug, args.group, args.units)

    if do("glossary"):
        print("== glossary ==")
        glossary_mod.build(args.target, args.slug, args.known, units=units,
                           batch_size=args.batch, workers=args.workers, model=args.model)

    if do("grammar"):
        print("== grammar ==")
        grammar_mod.build(args.target, args.slug, args.known, units=units,
                          workers=args.workers, sents_per_call=args.per_call, model=args.model)

    if do("site"):
        print("== site ==")
        build_site.build()


if __name__ == "__main__":
    main()
