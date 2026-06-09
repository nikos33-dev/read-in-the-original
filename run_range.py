#!/usr/bin/env python3
"""Run glossary + grammar (auto-converging) + site for a numeric range of unit ids, reporting
token cost. Same engine as run_group.py but for an arbitrary `s<lo>..s<hi>` span (handy for
batch-processing a work that isn't split into groups you'd name one at a time, e.g. the
Shahnameh's 777 poems). Resumable + heartbeated (see runstatus.py) — launch it detached:

  ./run_detached.sh run_range.py 65 164 --target fa --slug ferdowsi-shahnameh
  python3 runstatus.py            # watch progress from any shell
"""
from __future__ import annotations

import argparse
import time

import build_site
import glossary as glossary_mod
import grammar as grammar_mod
import llm
import runstatus
import store
import verify_all


def _missing(target, slug, known, cids):
    glossary = store.load_glossary(target, known)
    tot = 0
    for cid in cids:
        r = verify_all.check_unit(target, slug, cid, known, glossary)
        tot += r["miss_seg"] + r["miss_tr"]
    return tot


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("lo", type=int, help="first poem number")
    ap.add_argument("hi", type=int, help="last poem number (inclusive)")
    ap.add_argument("--target", default="fa")
    ap.add_argument("--slug", default="ferdowsi-shahnameh")
    ap.add_argument("--known", default="en")
    ap.add_argument("--workers", type=int, default=60)
    ap.add_argument("--batch", type=int, default=12)
    ap.add_argument("--per-call", type=int, default=4, dest="per_call")
    ap.add_argument("--model", default=None)
    ap.add_argument("--in-rate", type=float, default=0.25)
    ap.add_argument("--out-rate", type=float, default=2.0)
    args = ap.parse_args()

    on_disk = set(store.list_units(args.target, args.slug))
    cids = [f"s{n:03d}" for n in range(args.lo, args.hi + 1) if f"s{n:03d}" in on_disk]
    if not cids:
        raise SystemExit(f"No segmented units in s{args.lo:03d}..s{args.hi:03d} — segment first")
    print(f"=== range s{args.lo:03d}..s{args.hi:03d}: {len(cids)} poems ===", flush=True)

    llm.reset_usage()
    t0 = time.time()
    print("-- glossary --", flush=True)
    glossary_mod.build(args.target, args.slug, args.known, units=cids,
                       batch_size=args.batch, workers=args.workers, model=args.model)
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
    runstatus.finish(stage=f"s{args.lo:03d}..s{args.hi:03d} complete", done=len(cids), total=len(cids),
                     est_cost=round(cost, 2))
    print(f"\n=== DONE {len(cids)} poems in {dt/60:.1f} min ===")
    print(f"calls {u['calls']} | in {u['prompt_tokens']:,} out {u['completion_tokens']:,}")
    print(f"est cost @ ${args.in_rate}/${args.out_rate} per Mtok: ${cost:.2f}  "
          f"(${cost/len(cids):.3f}/poem)")


if __name__ == "__main__":
    main()
