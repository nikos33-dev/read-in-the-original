#!/usr/bin/env python3
"""Grammar + translation + sense disambiguation, per unit, batched and parallel.

For each SENTENCE (the grammatical context unit) we produce: a full-sentence translation, a
per-SEGMENT grammar explanation (the selectable clause unit), and a per-word in-context sense
pick. To scale to the whole Divine Comedy we:

  - **Batch** several consecutive sentences into one LLM call (with the sentence immediately
    before and after as read-only context + the work's name). Cuts call count ~Nx while
    keeping neighbour context.
  - Feed all batches across all requested units through a single **global thread pool**, so
    workers never idle at unit boundaries.
  - Write per-unit overlay files, checkpointing after every batch under a per-unit lock —
    fully **resumable** (re-running skips sentences already done).

Overlay shape, per unit:
  grammar__<known>/<unitId>.json : { "segments": {segId: explanation}, "sentences": {sid: translation} }
  senses__<known>/<unitId>.json  : { lineId: {tokIdx: senseIdx} }
"""
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import glossary as glossary_mod
import llm
import runstatus
import store

CALL_TIMEOUT = 180

SYS = """You help a learner read «{title}» by {author}, written in {target_name}, \
explaining in {known_name}.

You are given several consecutive TARGET sentences from the work, each broken into numbered \
segments (short clauses), plus the sentence immediately BEFORE and AFTER them as read-only \
context. Use all of it to understand the grammar, but EXPLAIN ONLY THE TARGET SEGMENTS — \
never explain or translate the context sentences.

For each target sentence return:
- "translation": a faithful, natural {known_name} translation of the whole sentence.
- "segments": for each of its segments, an object with "explanation" (a 1-3 sentence \
{known_name} grammar note covering THAT segment fully — word order, verb tense/mood, archaic \
or poetic forms, contractions, and how it links to the rest of the sentence; be specific, do \
not just translate) and "senses" (for each listed content word, the integer index of the \
sense it uses HERE, or -1).

Return STRICT JSON: {{"sentences": {{"<sid>": {{"translation": "...", "segments": \
{{"<segId>": {{"explanation": "...", "senses": {{"w0": 0, ...}}}}}}}}}}}}. Include every \
target sid and every target segId; output {known_name} only."""


def lang_name(code: str) -> str:
    return glossary_mod.lang_name(code)


def _segment_words(segid, sent, lines, glossary):
    out, wid = [], 0
    for lid in sent["lines"]:
        for ti, tok in enumerate(lines.get(lid, {}).get("tokens", [])):
            if tok.get("seg") != segid or tok["pos"] in glossary_mod.SKIP_POS:
                continue
            ent = glossary.get(tok["lemma"])
            out.append((f"w{wid}", lid, ti, tok, ent.get("senses", []) if ent else []))
            wid += 1
    return out


def _process_batch(task, st, glossary, work, sys_prompt, model):
    """Run one LLM call for a batch of sentences; merge into the unit's overlays."""
    cid, before, after, chunk = task
    unit = st["unit"]
    sentences, lines, segments = unit["sentences"], unit["lines"], unit["segments"]

    word_refs = {}                                   # (sid, segid) -> [(wid,lid,ti,tok,senses)]
    target_payload = []
    for sid in chunk:
        seg_payload = []
        for segid in sentences[sid]["segs"]:
            words = _segment_words(segid, sentences[sid], lines, glossary)
            word_refs[(sid, segid)] = words
            seg_payload.append({
                "id": segid, "text": segments[segid]["text"],
                "words": [{"id": w, "surface": tok["surface"], "lemma": tok["lemma"],
                           "senses": [s.get("gloss", "") for s in senses]}
                          for (w, _l, _t, tok, senses) in words],
            })
        target_payload.append({"id": sid, "text": sentences[sid]["text"],
                               "segments": seg_payload})

    user = json.dumps({"work": work, "context_before": before, "context_after": after,
                       "target_sentences": target_payload}, ensure_ascii=False)
    resp = llm.chat_json(sys_prompt, user, model=model, timeout=CALL_TIMEOUT)
    sents_out = resp.get("sentences", {}) or {}

    with st["lock"]:
        for sid in chunk:
            entry = sents_out.get(sid, {})
            st["grammar"]["sentences"][sid] = (entry.get("translation") or "").strip()
            segs_out = entry.get("segments", {}) or {}
            for segid in sentences[sid]["segs"]:
                seg_entry = segs_out.get(segid, {})
                st["grammar"]["segments"][segid] = (seg_entry.get("explanation") or "").strip()
                chosen = seg_entry.get("senses", {}) or {}
                for (w, lid, ti, _tok, senses) in word_refs[(sid, segid)]:
                    idx = chosen.get(w)
                    if isinstance(idx, int) and 0 <= idx < len(senses):
                        st["senses"].setdefault(lid, {})[str(ti)] = idx
        store.save_grammar(*st["save_args_grammar"], st["grammar"])
        store.save_senses(*st["save_args_senses"], st["senses"])
    return len(chunk)


def build(target: str, slug: str, known: str, *, units: list[str] | None = None,
          workers: int = 8, sents_per_call: int = 3, model: str | None = None,
          verbose: bool = True) -> dict:
    cids = units if units is not None else store.list_units(target, slug)
    if not cids:
        raise SystemExit(f"No units for {target}/{slug} — run segment.py first")
    glossary = store.load_glossary(target, known)
    if not glossary:
        raise SystemExit("Glossary empty — run glossary.py before grammar.py")
    meta = store.load_meta(target, slug)
    work = f"{meta.get('title', slug)} by {meta.get('author', '')}".strip(" by")
    sys_prompt = SYS.format(title=meta.get("title", slug), author=meta.get("author", ""),
                            target_name=lang_name(target), known_name=lang_name(known))

    state, tasks = {}, []
    for cid in cids:
        unit = store.load_unit(target, slug, cid)
        grammar = store.load_grammar(target, slug, cid, known)
        grammar.setdefault("segments", {})
        grammar.setdefault("sentences", {})
        senses = store.load_senses(target, slug, cid, known)
        st = {"unit": unit, "grammar": grammar, "senses": senses,
              "lock": threading.Lock(),
              "save_args_grammar": (target, slug, cid, known),
              "save_args_senses": (target, slug, cid, known)}
        state[cid] = st

        ordered = list(unit["sentences"].keys())

        def done(sid, g=grammar, c=unit):
            S = c["sentences"][sid]
            if not any(ch.isalnum() for ch in S["text"]):
                return True                          # punctuation-only sentence — nothing to do
            # non-empty: a model that omits a sentence/segment leaves "" — redo it on resume
            return (bool(g["sentences"].get(sid))
                    and all(g["segments"].get(seg) for seg in S["segs"]))
        todo = [s for s in ordered if not done(s)]
        for i in range(0, len(todo), sents_per_call):
            chunk = todo[i:i + sents_per_call]
            fp, lp = ordered.index(chunk[0]), ordered.index(chunk[-1])
            before = unit["sentences"][ordered[fp - 1]]["text"] if fp > 0 else None
            after = unit["sentences"][ordered[lp + 1]]["text"] if lp < len(ordered) - 1 else None
            tasks.append((cid, before, after, chunk))

    total_sents = sum(len(t[3]) for t in tasks)
    llm.configure_concurrency(workers)               # AIMD ceiling; auto-throttles on 429s
    if verbose:
        print(f"grammar {target}/{slug}__{known}: {len(cids)} units, {len(tasks)} batches "
              f"(~{sents_per_call}/call), {total_sents} sentences to do, {workers} workers")
    if not tasks:
        return {"batches": 0, "sentences": 0}

    done_sents = 0
    lock = threading.Lock()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(_process_batch, t, state[t[0]], glossary, work, sys_prompt, model): t
                for t in tasks}
        for fut in as_completed(futs):
            t = futs[fut]
            try:
                n = fut.result()
            except Exception as e:
                print(f"  ! batch {t[0]} {t[3]} failed: {str(e)[:160]}")
                continue
            with lock:
                done_sents += n
                runstatus.update("grammar", done_sents, total_sents)   # disk heartbeat
                if verbose and (done_sents % 50 < sents_per_call or done_sents >= total_sents):
                    print(f"  {done_sents}/{total_sents} sentences", flush=True)
    return {"batches": len(tasks), "sentences": done_sents}


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Build grammar + sense overlays for a work")
    ap.add_argument("--target", default="it")
    ap.add_argument("--slug", default="dante-divine-comedy")
    ap.add_argument("--known", default="en")
    ap.add_argument("--units", nargs="*", default=None, help="unit ids (default: all)")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--per-call", type=int, default=3, dest="per_call")
    ap.add_argument("--model", default=None)
    args = ap.parse_args()
    s = build(args.target, args.slug, args.known, units=args.units, workers=args.workers,
              sents_per_call=args.per_call, model=args.model)
    print(f"done: {s['batches']} batches, {s['sentences']} sentences")


if __name__ == "__main__":
    main()
