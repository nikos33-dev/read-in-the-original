#!/usr/bin/env bash
# Launch a long reader run as a nohup process with a tailable log in your OWN shell namespace.
#
# Why: agent `run_in_background` tasks are reliable (they finish + send a completion
# notification), but they run in an isolated sandbox — a later `ps`/`tail` from another Bash call
# can't see them, which is easy to misread as "the run died" and then wrongly relaunch (that
# duplicate-launch mistake once processed 50 poems twice, ~$6 wasted). This launcher keeps the
# process visible to `ps` and streams to `.raw/run-*.log` so you can `tail -f` it directly. The
# tradeoff: you poll it yourself (no auto-notification). The pipeline is resumable either way, so
# a genuine interruption loses zero completed work.
#
# Usage (run from reader/):
#   ./run_detached.sh run_cantica.py Inferno --workers 60
#   ./run_detached.sh precompute.py --stage grammar --target fa --slug ferdowsi-shahnameh
# Then watch:   tail -f .raw/run-*.log     (and verify_all.py for coverage + resume command)
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p .raw
# Each LLM call spawns a curl subprocess, so high --workers needs process headroom. Raise the
# soft NPROC limit to the hard ceiling (best-effort) so big runs don't hit fork failures.
ulimit -u "$(ulimit -Hu)" 2>/dev/null || true
ts=$(date +%Y%m%d-%H%M%S)
log=".raw/run-${ts}.log"
nohup python3 "$@" > "$log" 2>&1 &
pid=$!
echo "detached PID $pid  ->  $log"
echo "watch:  tail -f $log     resume-if-killed:  python3 verify_all.py --known <k>"
