#!/usr/bin/env python3
"""Coverage check across all units of a work for one reader language.

Reports, per unit: gloss coverage, missing segment explanations, missing sentence
translations, invalid sense picks. Prints the unit ids that still need work so a resume run
can target them. Read-only.

  python3 verify_all.py --known en
  python3 verify_all.py --known en --group Inferno
"""
from __future__ import annotations

import argparse

import glossary as glossary_mod
import store

SKIP = glossary_mod.SKIP_POS


def check_unit(target, slug, cid, known, glossary):
    unit = store.load_unit(target, slug, cid)
    grammar = store.load_grammar(target, slug, cid, known)
    senses = store.load_senses(target, slug, cid, known)
    segs = grammar.get("segments", {})
    sents = grammar.get("sentences", {})

    content = [(lid, ti, t) for lid, ln in unit["lines"].items()
               for ti, t in enumerate(ln["tokens"]) if t["pos"] not in SKIP]
    glossed = sum(1 for _l, _t, tk in content if tk["lemma"] in glossary)
    # ignore punctuation-only units (e.g. a stray "." split into its own segment/sentence)
    has_text = lambda s: any(c.isalnum() for c in s["text"])
    miss_seg = [s for s, S in unit["segments"].items() if has_text(S) and not segs.get(s)]
    miss_tr = [s for s, S in unit["sentences"].items() if has_text(S) and not sents.get(s)]
    bad = 0
    for lid, d in senses.items():
        for ti, idx in d.items():
            tk = unit["lines"][lid]["tokens"][int(ti)]
            e = glossary.get(tk["lemma"])
            if not e or idx >= len(e["senses"]):
                bad += 1
    complete = not miss_seg and not miss_tr and glossed == len(content)
    return {"cid": cid, "content": len(content), "glossed": glossed,
            "miss_seg": len(miss_seg), "miss_tr": len(miss_tr), "bad": bad,
            "complete": complete}


def main():
    ap = argparse.ArgumentParser(description="Verify reader coverage across units")
    ap.add_argument("--target", default="it")
    ap.add_argument("--slug", default="dante-divine-comedy")
    ap.add_argument("--known", default="en")
    ap.add_argument("--group", default=None)
    args = ap.parse_args()

    glossary = store.load_glossary(args.target, args.known)
    cids = store.list_units(args.target, args.slug)
    if args.group:
        meta = store.load_meta(args.target, args.slug)
        keep = {c["id"] for c in meta.get("units", [])
                if c["group"].lower() == args.group.lower()}
        cids = [c for c in cids if c in keep]

    incomplete, tot_content, tot_glossed, tot_seg, tot_tr, tot_bad = [], 0, 0, 0, 0, 0
    for cid in cids:
        r = check_unit(args.target, args.slug, cid, args.known, glossary)
        tot_content += r["content"]; tot_glossed += r["glossed"]
        tot_seg += r["miss_seg"]; tot_tr += r["miss_tr"]; tot_bad += r["bad"]
        if not r["complete"]:
            incomplete.append(r["cid"])
            print(f"  {r['cid']}: gloss {r['glossed']}/{r['content']}, "
                  f"miss_seg {r['miss_seg']}, miss_tr {r['miss_tr']}, bad {r['bad']}")
    print(f"\n{len(cids)} units | gloss {tot_glossed}/{tot_content} | "
          f"missing explanations {tot_seg} | missing translations {tot_tr} | invalid picks {tot_bad}")
    if incomplete:
        print(f"\nincomplete ({len(incomplete)}): {' '.join(incomplete)}")
        print("resume: python3 precompute.py --stage grammar --units " + " ".join(incomplete))
    else:
        print("\nALL UNITS COMPLETE ✓")


if __name__ == "__main__":
    main()
