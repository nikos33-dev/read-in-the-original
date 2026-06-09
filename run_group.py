#!/usr/bin/env python3
"""Run glossary + grammar + site for one group, reporting real token cost + time.

Resumable: skips lemmas/sentences already done. Reusable per group (Inferno/Purgatorio/
Paradiso). Cost is computed from measured token usage at an assumed gpt-5.5 rate (override
with --in-rate/--out-rate $ per million tokens).

  python3 run_group.py Inferno --workers 12 --per-call 4
"""
from __future__ import annotations

import argparse
import time

import build_site
import glossary as glossary_mod
import grammar as grammar_mod
import llm
import precompute
import runstatus
import store
import verify_all


def _missing(target, slug, known, cids):
    """Total missing segment explanations + sentence translations across cids."""
    glossary = store.load_glossary(target, known)
    tot = 0
    for cid in cids:
        r = verify_all.check_unit(target, slug, cid, known, glossary)
        tot += r["miss_seg"] + r["miss_tr"]
    return tot


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("group")
    ap.add_argument("--target", default="it")
    ap.add_argument("--slug", default="dante-divine-comedy")
    ap.add_argument("--known", default="en")
    ap.add_argument("--workers", type=int, default=60,
                    help="max concurrent LLM calls (AIMD ceiling; auto-throttles down on 429s)")
    ap.add_argument("--batch", type=int, default=12)
    ap.add_argument("--per-call", type=int, default=4, dest="per_call")
    ap.add_argument("--model", default=None, help="LLM id (default: oai.py gpt-5.5)")
    ap.add_argument("--in-rate", type=float, default=0.25, help="$ / 1M input tokens")
    ap.add_argument("--out-rate", type=float, default=2.0, help="$ / 1M output tokens")
    args = ap.parse_args()

    if args.group.lower() == "all":
        cids = store.list_units(args.target, args.slug)
    else:
        cids = precompute.resolve_units(args.target, args.slug, group=args.group)
    if not cids:
        raise SystemExit(f"No units for {args.group!r}")
    print(f"=== {args.group}: {len(cids)} units ===", flush=True)

    llm.reset_usage()
    t0 = time.time()
    print("-- glossary --", flush=True)
    glossary_mod.build(args.target, args.slug, args.known, units=cids,
                       batch_size=args.batch, workers=args.workers, model=args.model)

    # Grammar with auto-convergence: gpt-5-mini occasionally omits items, leaving empties;
    # the resume done-check redoes only empties, so re-run with shrinking batches until clean.
    for pc in [args.per_call, 3, 2, 1]:
        print(f"-- grammar (per-call {pc}) --", flush=True)
        grammar_mod.build(args.target, args.slug, args.known, units=cids,
                          workers=args.workers, sents_per_call=pc, model=args.model)
        miss = _missing(args.target, args.slug, args.known, cids)
        print(f"   remaining gaps: {miss}", flush=True)
        if miss == 0:
            break

    print("-- site --", flush=True)
    build_site.build()
    dt = time.time() - t0
    u = llm.usage_totals()

    cost = u["prompt_tokens"] / 1e6 * args.in_rate + u["completion_tokens"] / 1e6 * args.out_rate
    runstatus.finish(stage=f"{args.group} complete")
    print(f"\n=== {args.group} DONE in {dt/60:.1f} min ===")
    print(f"calls: {u['calls']}  |  tokens in {u['prompt_tokens']:,}  out {u['completion_tokens']:,}")
    print(f"est. cost @ ${args.in_rate}/${args.out_rate} per Mtok: ${cost:.2f}")


if __name__ == "__main__":
    main()
