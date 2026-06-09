#!/usr/bin/env python3
"""Build / extend the per-language-pair glossary: lemma -> ranked list of senses.

For every content lemma in a work's text.json that isn't glossed yet, look it up in the
dictionary (dictionary.py) and hand those reference definitions to the LLM, which returns
clean, distinct, ranked senses in the reader's language. Multiple senses are first-class:
a polysemous word stores several, primary first (this is how "what if a word has multiple
meanings" is handled at the lexical layer; in-context selection lives in grammar.py).

Glossary entries are SHARED across every work in the same target language and only ever
extended, so re-runs and new works are cheap (already-glossed lemmas are skipped).

LLM via the bundled oai.py (OpenAI). Network/curl lives in oai.py + dictionary.py.

Schema per lemma:
  { headword, pos, dict_source, senses:[ {gloss, definition, register} ] }
    gloss      = short reader-language equivalent (1-4 words)
    definition = one short reader-language sentence
    register   = "" | "archaic" | "poetic" | "figurative" | ... (flag old/literary senses)
"""
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import dictionary
import llm
import runstatus
import store

# A few short language names for nicer prompts; falls back to the raw code.
LANG_NAMES = {"en": "English", "el": "Greek", "it": "Italian", "fr": "French",
              "es": "Spanish", "de": "German", "la": "Latin"}

SKIP_POS = {"PUNCT", "SYM", "SPACE", "NUM", "X"}

SYS = """You are a precise bilingual lexicographer building a study glossary for learners \
reading a classic literary work in the original {target_name}, with explanations in \
{known_name}.

For each {target_name} lemma you are given (with reference dictionary definitions when \
available), return its distinct senses, MOST COMMON / most relevant-to-classic-literature \
first. Prefer and stay faithful to the provided reference definitions; only add a sense \
from your own knowledge if the reference misses one that matters for reading older \
literature. Cap at 4 senses. Keep it tight and correct.

Return STRICT JSON: {{"entries": {{"<lemma>": {{"headword": "<dictionary headword in \
{target_name}>", "pos": "<noun|verb|adj|adv|prep|...>", "senses": [{{"gloss": "<1-4 word \
{known_name} equivalent>", "definition": "<one short {known_name} sentence>", "register": \
"<empty string, or archaic|poetic|figurative|literary|regional>"}}]}}}}}}

Every input lemma MUST appear as a key. Output {known_name} for gloss/definition, \
{target_name} only for the headword."""


def lang_name(code: str) -> str:
    return LANG_NAMES.get(code, code)


def content_lemmas(target: str, slug: str, units: list[str] | None = None) -> list[str]:
    """Unique content lemmas across all (or selected) units of a work, first-seen order."""
    seen, out = set(), []
    for cid in (units if units is not None else store.list_units(target, slug)):
        unit = store.load_unit(target, slug, cid)
        for ln in unit.get("lines", {}).values():
            for t in ln["tokens"]:
                lemma = t["lemma"]
                if t["pos"] in SKIP_POS:
                    continue
                if lemma and lemma not in seen:
                    seen.add(lemma)
                    out.append(lemma)
    return out


def _batch(items: list, n: int):
    for i in range(0, len(items), n):
        yield items[i:i + n]


def _process_batch(chunk: list[str], target: str, sys_prompt: str, model: str | None) -> dict:
    """Look up references (parallel within the batch) + one LLM call. Pure: returns entries."""
    refs = {}
    with ThreadPoolExecutor(max_workers=min(8, len(chunk))) as pool:
        for lemma, r in zip(chunk, pool.map(lambda lm: dictionary.lookup(lm, target), chunk)):
            refs[lemma] = {"reference_definitions": r["definitions"],
                           "dict_pos": r["pos"], "dict_source": r["source"]}
    user = "Lemmas with reference definitions (JSON):\n" + json.dumps(refs, ensure_ascii=False)
    resp = llm.chat_json(sys_prompt, user, model=model)
    entries = resp.get("entries", resp)
    out = {}
    for lemma in chunk:
        ent = entries.get(lemma)
        if isinstance(ent, dict) and ent.get("senses"):
            ent["dict_source"] = refs[lemma]["dict_source"]
            out[lemma] = ent
    return out


def build(target: str, slug: str, known: str, *, units: list[str] | None = None,
          batch_size: int = 12, workers: int = 8, model: str | None = None,
          verbose: bool = True) -> dict:
    if not store.list_units(target, slug):
        raise SystemExit(f"No units for {target}/{slug} — run segment.py first")
    glossary = store.load_glossary(target, known)

    todo = [lm for lm in content_lemmas(target, slug, units) if lm not in glossary]
    llm.configure_concurrency(workers)               # AIMD ceiling; auto-throttles on 429s
    if verbose:
        print(f"glossary {target}__{known}: {len(glossary)} existing, {len(todo)} new "
              f"lemmas, {workers} workers")
    if not todo:
        return glossary

    sys_prompt = SYS.format(target_name=lang_name(target), known_name=lang_name(known))
    chunks = list(_batch(todo, batch_size))
    lock = threading.Lock()
    done = 0
    # Batches run concurrently; merge + checkpoint under a lock as each finishes.
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(_process_batch, c, target, sys_prompt, model): c for c in chunks}
        for fut in as_completed(futs):
            try:
                out = fut.result()
            except Exception as e:                  # one bad batch shouldn't kill the run
                print(f"  ! batch failed ({len(futs[fut])} lemmas): {str(e)[:160]}")
                continue
            with lock:
                glossary.update(out)
                done += len(out)
                store.save_glossary(target, known, glossary)
                runstatus.update("glossary", done, len(todo))          # disk heartbeat
                if verbose:
                    print(f"  glossed {done}/{len(todo)}", flush=True)
    return glossary


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Build/extend a reader glossary")
    ap.add_argument("--target", default="it")
    ap.add_argument("--slug", default="dante-divine-comedy")
    ap.add_argument("--known", default="en")
    ap.add_argument("--batch", type=int, default=12)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--model", default=None)
    args = ap.parse_args()
    g = build(args.target, args.slug, args.known, batch_size=args.batch,
              workers=args.workers, model=args.model)
    print(f"glossary now holds {len(g)} lemmas -> {store.glossary_path(args.target, args.known)}")


if __name__ == "__main__":
    main()
