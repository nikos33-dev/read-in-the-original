# Read in the Original

Read public-domain literature in its **original language**, with help. Hover any word for its
meaning *in context* — the sense that fits this line, not the dictionary's first guess. Click any
phrase for a grammar note plus a full-sentence translation. Browse the work unit by unit.

Everything is **precomputed offline**. The served site makes **zero LLM/network calls** — it's
pure static JSON, one unit loaded at a time. Fast, and almost free to host.

## What's included

Three works, fully processed, all public-domain originals:

| Work | Language | Units | Source |
|---|---|---|---|
| Dante — *The Divine Comedy* | Italian → English | 100 cantos | Project Gutenberg #1012 |
| Dostoevsky — *Notes from Underground* | Russian → English | 21 chapters | Lib.ru |
| Ferdowsi — *Shahnameh* | Persian → English | 777 stories | ganjoor.net |

The original texts are public domain. The word glosses, grammar notes, and translations are
generated, and are shipped under this repo's license.

## Run it — driven by the `/reader` skill

The reader is operated through the bundled **[Claude Code](https://claude.com/claude-code) `/reader`
skill**: it builds the static site, serves it, reports coverage, and adds new works. From the repo
in Claude Code:

- `/reader rebuild site` — build the `site/data/` bundle from the shipped corpus.
- `/reader status` — coverage across the works.
- `/reader add a work …` — process a new public-domain text (see below).

The repo ships the processed corpus (`works/` + `glossaries/`) but **not** the built `site/data/`
bundle — it's a regenerable artifact, rebuilt from the corpus in seconds (free, no API key, pure
stdlib). Build it once, then open the served site.

### Or run it manually

Everything the skill does maps to a script, if you'd rather not use Claude Code:

```bash
python3 build_site.py          # build site/data/ from works/ + glossaries/   (free, stdlib only)
cd site && python3 serve.py    # http://localhost:8780  (no-cache dev server)
```

Reading the shipped works needs nothing but Python. Open the URL, pick a work in the left nav, hover
words, click phrases. To host it publicly, run `build_site.py` and deploy the static `site/` folder
(including the generated `site/data/`) to any static host.

## Add your own public-domain work

Adding a text is also a `/reader` skill action — `/reader add a work "<book> <src>-><known>"` — which
walks the staged, cost-gated flow (confirm public domain → segment → dry-run cost → calibrate →
process → rebuild). Under the hood it uses **spaCy** (segmentation + lemmatization) and an **OpenAI
API key** (the glosses, grammar, translations); reading the shipped works needs neither.

To run that pipeline by hand:

```bash
pip install spacy && python3 -m spacy download it_core_news_sm   # ru_core_news_sm, etc.
pip install hazm                                                  # Persian only
export OPENAI_API_KEY=...

python3 precompute.py --raw <file> --dry-run     # segment + cost estimate (free)
python3 run_group.py <Group>                      # process a group; reports real $ / time
python3 verify_all.py --known en                  # coverage + resume command for any gaps
python3 build_site.py                             # rebuild the static bundle
```

The pipeline is staged and **resumable** (re-run continues where it stopped) and **cost-gated**
(always `--dry-run` + calibrate on 1–2 units before a large run). The default model is
`gpt-5-mini` (override with `--model`).

## How it works

- **Two granularities.** You *select* a **segment** — a clause, split on punctuation, not on line
  breaks, so a clause can span verses. The grammar is reasoned over the whole **sentence** plus its
  neighbors for context; each sentence also gets one full translation.
- **Per-unit storage**, one file per unit, so the browser and the pipeline only ever touch one
  unit at a time. Glossaries are shared per language-pair and deduped across the whole corpus
  (`works/<t>/<slug>/…` + `glossaries/<t>__<k>.json`).
- **Pipeline:** `segment.py` (spaCy / Hazm for Persian — the only place lemmatization happens, and
  it's free) → `glossary.py` (dictionary-grounded multi-sense glosses, LLM) → `grammar.py` (batched
  per sentence with neighbor context, LLM) → `build_site.py` (the static bundle). Drivers:
  `precompute.py` (staged), `run_group.py` / `run_range.py`, `verify_all.py` (coverage + resume),
  `runstatus.py` (a disk heartbeat for long runs). `store.py` is the sole atomic writer; `llm.py`
  wraps `oai.py` with retry/backoff + token metering.

## Dependencies

- **Reading** the shipped corpus: Python 3.10+ standard library only.
- **Processing** new works: `spacy` (+ language models), `hazm` (Persian), `curl` on PATH, and an
  OpenAI API key. See `requirements.txt`.

## License

AGPL-3.0-or-later. Copyright (C) 2026 Nikos. See [LICENSE](LICENSE).
