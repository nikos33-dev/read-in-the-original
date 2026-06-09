---
name: reader
description: Use to add or manage works in the "Read in the Original" language-learning reader — process a public-domain text so learners can hover any word for its in-context meaning, click any phrase for an LLM grammar explanation + translation, and browse unit by unit. Trigger on "/reader", "add a work to the reader", "process <book> for the reader", "add <language> to the reader", "run/resume the reader pipeline", "regenerate the reader", "rebuild the reader site". Runs the offline, staged, resumable precompute (spaCy segmentation + dictionary-grounded multi-sense glossary + batched per-sentence grammar) and serves a zero-LLM static site. Cost-gated — dry-runs + calibrates cost and confirms before any large paid run.
argument-hint: [work + language, e.g. "Inferno it->en" / "run Purgatorio" / "rebuild site" / "status"]
allowed-tools: Bash, Read, Edit, Write, AskUserQuestion
---

## What this skill does

Adds and maintains works in the **"Read in the Original"** reader. Public-domain texts processed
so a learner can **hover a word** → its in-context meaning (fitting sense first, others below),
**click a phrase** → an LLM grammar note + the whole sentence's translation, and **browse unit by
unit**. Everything is **precomputed offline; the served site makes zero LLM/network calls** (one
unit loaded at a time).

Corpus (multi-work, multi-language): the **whole Divine Comedy** (`dante-divine-comedy`, it→en,
verse/100 units), **Notes from Underground** (`dostoevsky-notes-underground`, ru→en,
prose/21 chapters), and the **Shahnameh** (`ferdowsi-shahnameh`, fa→en, verse/777 stories).
Glossaries are per language-pair (`it__en`, `ru__en`, `fa__en`).

**Cost-gated.** Writes only under the repo. Segmentation is free (local spaCy / Hazm for Persian);
glossary + grammar make paid LLM calls (default gpt-5-mini, cheap). ALWAYS `--dry-run` for counts
and **calibrate on 1–2 units** for real tokens/time, then confirm before a large run.

## Mental model — read before touching it

**Two granularities:**
- **Segment** (`inferno-5-g7`) — the selectable/explained unit: a clause, split on punctuation
  `. , ; : ! ?` **only, never line breaks** (poetry enjambment → a segment may span verses).
- **Sentence** (`inferno-5-s3`) — grammatical unit ending in `. ! ?`; used as context. The LLM
  gets the target's full sentence + previous + next sentence + the work's name, and returns a
  per-segment explanation, a per-sentence **translation**, and a per-word in-context sense pick.

IDs are **group-namespaced** so the three Unit I's don't collide.

**Per-unit storage, split by compute cost** (one file per unit → browser/pipeline touch one at
a time):
```
works/<t>/<slug>/meta.json                  work metadata + unit manifest
works/<t>/<slug>/units/<cid>.json          text (language-neutral; tokens carry seg+sid)
works/<t>/<slug>/grammar__<k>/<cid>.json    {segments:{segId:expl}, sentences:{sid:translation}}
works/<t>/<slug>/senses__<k>/<cid>.json     {lineId:{tokIdx:senseIdx}}
glossaries/<t>__<k>.json                    SHARED across all works/units in <t> (dedup win)
```
Lemmatization happens in ONE place (`segment.py`, spaCy / Hazm for Persian). Glossary stores
**multiple ranked senses per lemma** (polysemy), dictionary-grounded. Modules: `segment →
dictionary → glossary → grammar → build_site`; `precompute.py` (staged driver), `verify_all.py`
(coverage+resume), `run_group.py` (one group + cost report), `run_range.py` (a unit-number range),
`store.py` (sole writer, atomic), `llm.py` (retry/backoff + token metering around the bundled
`oai.py`). LLM defaults to **gpt-5-mini** (set in `llm.py`; ~25-30× cheaper than a flagship,
on-par quality for this task) — override with `--model`. Full detail in `README.md`.

## Routing — read the argument first

- **"status" / bare "/reader"** → `python3 verify_all.py --known <k>` + list `works/` units
  ready; report, then ask what to do.
- **"run/resume <group>"** → `run_group.py <Group>` (resumable).
- **"add a work" / a book + language** → New work (below).
- **"add <lang>"** → new pair on existing text (glossary + grammar for that known; skip segment).
- **"rebuild site"** → `precompute.py --stage site`.

## Adding / running a work (the staged, cost-safe flow)

1. **Confirm public domain.** Only public-domain texts (this is what makes it publishable).
   Default source: Project Gutenberg plain text. Record the ebook id/URL.
2. **spaCy model** for the target language installed (Italian: `it_core_news_sm`; Russian:
   `ru_core_news_sm`). Persian has no spaCy model — the pipeline uses a blank tokenizer + Hazm.
   If a language has no model, the pipeline falls back to LLM lemmatization — flag lower quality.
3. **Fetch** to `.raw/` (git-ignored).
4. **Check structure, pick a format.** Three parsers in `segment.py`: `--format headed` (verse
   with in-text unit headers — its regex matches the literal `Canto`, e.g. Dante's
   `Inferno • Canto V`), `--format prose` (parts/chapters/paragraphs — part heading starts
   `Часть`/`Part`, chapter = a bare roman numeral on its own line, each paragraph one reflowed
   line; ids are part-namespaced `p1-3`), and `--format marked` (explicit `=== group` /
   `--- num title` markers — for unpunctuated verse like the Shahnameh). Set
   `--unit Canto`/`Chapter`/`Story` (the UI label). A source that fits none → reflow it to one of
   these shapes or extend the parser. **Strip HTML at ingest** (don't feed markup to the model or
   the page). Read the raw file first; don't assume it fits.
5. **Dry-run (free):** `python3 precompute.py --raw <file> --dry-run` → lines/sentences/segments/
   lemmas + **estimated paid calls**.
6. **Calibrate (small paid):** run 1–2 representative units and read real cost via
   `precompute.py --stage glossary --units <id1> <id2>` then `--stage grammar --units ...`,
   or `run_group.py <Group>` for a whole group. Report tokens/time/$ and CONFIRM.
7. **Run staged & resumable:** `./run_detached.sh run_group.py Inferno --per-call 4` for a
   `tail -f .raw/run-*.log`-able run, or a background run (then trust its completion notification,
   not `ps` — see the observability lesson below). Workers default 60 (auto-throttling). Each
   reports calls/tokens/$ and rebuilds the site.
8. **Verify + serve:** `python3 verify_all.py --known <k>` (lists gaps + resume cmd);
   `cd site && python3 serve.py` (no-cache dev server — plain `http.server` lets Chrome cache
   a stale manifest/app.js after a rebuild) → eyeball hover/click/unit-nav.

## Optimization & recovery lessons (bake these in)

- **Dry-run, then calibrate, then commit.** Never launch the whole corpus blind.
- **Parallelize** (`--workers`); grammar uses a **global queue across units** so workers never
  idle at unit boundaries. **Batch sentences per grammar call** (`--per-call 3–4`) — the main
  call-count cut. The work is **I/O-bound on the API**, so high `--workers` (default **60**)
  scales nearly linearly until rate limits.
- **Concurrency self-throttles (`llm.py` AIMD limiter).** All LLM calls pass one adaptive gate:
  starts at the `--workers` ceiling, **halves on any 429**, **creeps back up** after clean-success
  streaks. So a high default is safe for *any* account — high-limit users run full speed;
  low-limit users auto-scale down instead of failing.
- **A run is NEVER invisible — check the disk heartbeat: `python3 runstatus.py`.** Every run
  writes progress + a fresh timestamp to `.run_status.json` on every batch, so **any** shell can
  read its state: `[ALIVE/DONE/STALE] stage done/total · $cost · conc · heartbeat Ns ago · pid`.
  Liveness = heartbeat age (ALIVE if < 180 s), not "can I see the PID". This matters when a
  background run's buffered stdout looks frozen: **never judge a run by `ps`/`tail`, and never
  relaunch on a hunch** — concurrent duplicates just burn money. Re-running after a *genuine* stop
  is safe (resumable skips done work).
- **Glossary is shared + front-loaded:** built once across all units, dedup means later units add
  few new lemmas. Don't re-gloss when only adding grammar.
- **Resume = re-run.** Glossary skips glossed lemmas; grammar skips done sentences; writes are
  atomic. `verify_all.py` prints the exact `--units …` resume command for incomplete units.
- **Timeouts/limits:** `llm.py` retries with backoff; if calls still time out, drop `--batch`
  (glossary) or `--per-call` (grammar), and/or `--workers`.
- **spaCy mis-lemmatizes archaic forms**; a lemma that hangs the model is usually junk. Hand-fix
  in the shared glossary via `store.py`, keyed to what's in the unit file.
- **Cost metering:** `llm.usage_totals()` accumulates tokens; `run_group.py` reports $ at a
  configurable rate (`--in-rate/--out-rate`). Token counts are the reliable part — apply the real
  rate for your model.
- **LLM defaults to gpt-5-mini** (cheap, on-par for this task). Override with `--model` if a work
  needs a stronger model. gpt-5-mini occasionally omits items per batch → the `run_group`
  auto-converge loop (shrinking `--per-call`) closes the gaps; punctuation-only "sentences" are
  ignored by verify/done-checks.

## Security — prompt injection in source texts

The reader is the **lowest-risk** LLM surface: the model has **no tools/agency** (pure text→JSON
via `oai.chat_json`), so an injected instruction in a book can't make it *act* — the worst case is
a corrupted gloss/grammar/translation for that passage. Defenses already in place: text is passed
as JSON **data** (not instructions) with a clear task prompt; **HTML is stripped at ingest**; the
frontend renders all model output via `textContent`/`createTextNode`/`escapeHtml` (no `innerHTML`
of model text — audit any new field you add). Use **curated public-domain texts**, not arbitrary
user uploads. **If that ever changes** (users upload their own texts), revisit: add an injection
check, keep strict output-escaping + HTML sanitization at ingest.

## Don't

- Don't process copyrighted text. Public domain only.
- Don't split segments on line breaks (enjambment).
- Don't launch a full corpus without a dry-run + calibration + confirmation.
- Don't re-gloss when only adding grammar (the glossary is shared and the costly part).
- Don't hand-edit `works/**.json` except the documented manual-lemma fix via `store.py`.
