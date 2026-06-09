#!/usr/bin/env python3
"""A disk heartbeat so a run is NEVER invisible.

The trap (learned the hard way): agent `run_in_background` tasks live in an isolated sandbox, so
`ps`/`pgrep`/`tail` from another shell can't see them — easy to misread as "dead" and relaunch a
costly duplicate. The cure is process-independent: every run writes its progress + a fresh
timestamp to ONE file (`reader/.run_status.json`) that any process in any sandbox can read. So
liveness is "how long since the heartbeat", not "can I see the PID".

  python3 runstatus.py            # print current run status (ALIVE / DONE / STALE)
  python3 runstatus.py --watch    # refresh every 2s

The build stages (glossary.py, grammar.py) call `update()` on every checkpoint; cost +
concurrency are pulled from llm.py automatically.
"""
from __future__ import annotations

import json
import os
import pathlib
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parent
STATUS = ROOT / ".run_status.json"
STALE_AFTER = 180          # seconds without a heartbeat -> assume the run is no longer alive

_lock = threading.Lock()
_t0: float | None = None


def _write(d: dict):
    tmp = STATUS.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    tmp.replace(STATUS)                                   # atomic


def update(stage: str, done: int, total: int, *, state: str = "running", **extra):
    """Write a heartbeat. Cheap; call it freely (e.g. once per finished batch)."""
    global _t0
    with _lock:
        if _t0 is None:
            _t0 = time.time()
        d = {"pid": os.getpid(), "ts": time.time(), "state": state,
             "stage": stage, "done": done, "total": total,
             "elapsed_s": round(time.time() - _t0, 1)}
        try:
            import llm
            u = llm.usage_totals()
            c = llm.concurrency_state()
            d["calls"] = u["calls"]
            d["est_cost"] = round(u["prompt_tokens"] / 1e6 * 0.25
                                  + u["completion_tokens"] / 1e6 * 2.0, 2)
            d["concurrency"] = f"{c['limit']}/{c['ceiling']}"
        except Exception:                                # llm not importable / no usage yet
            pass
        d.update(extra)
        _write(d)


def finish(stage: str = "done", **extra):
    update(stage, extra.pop("done", 0), extra.pop("total", 0), state="done", **extra)


def read() -> dict | None:
    if not STATUS.exists():
        return None
    d = json.loads(STATUS.read_text(encoding="utf-8"))
    d["age_s"] = round(time.time() - d.get("ts", 0), 1)
    if d.get("state") == "done":
        d["status"] = "DONE"
    elif d["age_s"] <= STALE_AFTER:
        d["status"] = "ALIVE"
    else:
        d["status"] = "STALE"                            # likely killed/finished without finish()
    return d


def _print_once():
    d = read()
    if not d:
        print("no run status file yet (no run started, or it predates the heartbeat)")
        return
    pct = f"{100 * d['done'] / d['total']:.0f}%" if d.get("total") else "—"
    print(f"[{d['status']}] {d['stage']} {d['done']}/{d['total']} ({pct}) "
          f"| ${d.get('est_cost', '?')} · {d.get('calls', '?')} calls · conc {d.get('concurrency', '?')} "
          f"| heartbeat {d['age_s']}s ago · elapsed {d.get('elapsed_s', '?')}s · pid {d['pid']}")


def main():
    import sys
    if "--watch" in sys.argv:
        try:
            while True:
                print("\033[2J\033[H", end="")           # clear
                _print_once()
                time.sleep(2)
        except KeyboardInterrupt:
            pass
    else:
        _print_once()


if __name__ == "__main__":
    main()
