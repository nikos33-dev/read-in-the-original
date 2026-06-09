#!/usr/bin/env python3
"""Raw public-domain text -> per-unit, language-neutral unit files (the text layer).

Parses a Project Gutenberg edition into units and runs spaCy over each to attach, per word
token, its (lemma, pos) and char span back into the line, plus the two-level unit structure:

  - Segment (`g`) — the selectable/explained unit: a clause, split on punctuation
    ". , ; : ! ?" ONLY (never line breaks — poetry enjambment runs a clause across verses).
  - Sentence (`s`) — the grammatical unit ending in ". ! ?", used as grammar context.

Each unit becomes its own self-contained file (see store.py). IDs are group-namespaced
(`inferno-5-l3`) so the three Unit I's never collide. This is the ONLY place lemmatization
happens (spaCy; surface form as fallback). Computed once, independent of reader language.

Supports the full Divina Commedia edition (Gutenberg #1012), whose unit headers read
"<Group> • Unit <ROMAN>".
"""
from __future__ import annotations

import argparse
import re

import store

ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
# "Inferno • Canto I" (bullet may be spaced); also plain "Canto I". The literal "Canto"
# is matched in the source text (the "headed" format) — do NOT genericize this regex.
HEADER_RE = re.compile(r"^(?:(Inferno|Purgatorio|Paradiso)\s*[•·]\s*)?Canto\s+([IVXLCDM]+)\s*$")
START_RE = re.compile(r"\*\*\* START OF")
END_RE = re.compile(r"\*\*\* END OF")

# "marked" format markers (used for sources that fit neither units nor prose — e.g. the
# Shahnameh): `=== <group>` opens a group-level group (a reign), `--- <num> [title]` opens a
# navigable unit (a story) within it. Blank line = stanza (bayt) break; any other line = a verse
# line (hemistich). This decouples sourcing from parsing: massage any source into this shape.
MARK_GROUP_RE = re.compile(r"^===\s+(.+?)\s*$")
MARK_UNIT_RE = re.compile(r"^---\s+(\d+)\s*(.*?)\s*$")

SEG_BOUNDARY = set(".;:!?,")     # segment ends here — deliberately NOT line breaks (enjambment)
SENT_BOUNDARY = set(".!?")       # sentence (context unit) ends only at terminal punctuation
CLOSERS = set("»”\")'›'")


def roman_to_int(s: str) -> int:
    total, prev = 0, 0
    for ch in reversed(s):
        v = ROMAN[ch]
        total += -v if v < prev else v
        prev = max(prev, v)
    return total


def parse_headed(raw: str, default_group: str = "Unit") -> list[dict]:
    """Body -> list of {group, num, id, title, stanzas:[[line,...]]}."""
    lines = raw.splitlines()
    body_start = next((i for i, l in enumerate(lines) if START_RE.search(l)), -1) + 1
    body_end = next((i for i, l in enumerate(lines) if END_RE.search(l)), len(lines))
    lines = lines[body_start:body_end]

    units: list[dict] = []
    cur: dict | None = None
    stanza: list[str] = []

    def flush_stanza():
        nonlocal stanza
        if stanza and cur is not None:
            cur["stanzas"].append(stanza)
        stanza = []

    for line in lines:
        m = HEADER_RE.match(line.strip())
        if m:
            flush_stanza()
            if cur is not None:
                units.append(cur)
            group = m.group(1) or default_group
            num = roman_to_int(m.group(2))
            cur = {"group": group, "num": num,
                   "id": f"{group.lower()}-{num}",
                   "title": line.strip(), "stanzas": []}
            continue
        if cur is None:
            continue                      # front matter before the first unit
        if line.strip() == "":
            flush_stanza()
        else:
            stanza.append(line.strip())   # #1012 indents verses 2 spaces; normalise
    flush_stanza()
    if cur is not None:
        units.append(cur)
    return units


def parse_prose(raw: str) -> list[dict]:
    """Prose -> list of {group(part), num(chapter), id, title, stanzas:[[paragraph]]}.

    Expects a cleaned text where part headings start with 'Часть'/'Part', chapter headings are
    a bare roman numeral on their own line, and each paragraph is one (reflowed) line. Each
    chapter is one navigable unit; each paragraph is one block (a single 'line'). Chapter ids
    are part-namespaced (`p1-3`) so repeated chapter numbers across parts don't collide.
    """
    units: list[dict] = []
    cur: dict | None = None
    part_title, part_num = None, 0
    pending: list[str] = []                          # paragraphs seen before a part's ch.1

    def open_chapter(num: int, label: str):
        nonlocal cur, pending
        if cur is not None:
            units.append(cur)
        cur = {"group": part_title or "", "num": num, "id": f"p{part_num}-{num}",
               "title": f"{part_title} — {label}" if part_title else label,
               "stanzas": [[p] for p in pending]}
        pending = []

    for line in raw.splitlines():
        s = line.strip()
        if not s:
            continue
        if re.match(r"^(Часть|Part|ЧАСТЬ)\b", s):
            if cur is not None:
                units.append(cur); cur = None
            part_title, part_num = s, part_num + 1
            pending = []
            continue
        if re.fullmatch(r"[IVXLCDM]+", s):           # chapter heading
            open_chapter(roman_to_int(s), s)
            continue
        if cur is None:
            pending.append(s)                        # e.g. the author's note before ch. I
        else:
            cur["stanzas"].append([s])
    if cur is not None:
        units.append(cur)
    return units


def parse_marked(raw: str) -> list[dict]:
    """Marked verse -> list of {group, num, id, title, stanzas:[[hemistich,...]]}.

    `=== <reign>` sets the current group group; `--- <num> [title]` opens a story (the
    navigable unit, id `<num>` zero-padded so sort order is stable); blank lines break stanzas
    (one bayt = two hemistichs). Ids are global across the work (`s007`) so they never collide.
    """
    units: list[dict] = []
    cur: dict | None = None
    stanza: list[str] = []
    group = ""

    def flush_stanza():
        nonlocal stanza
        if stanza and cur is not None:
            cur["stanzas"].append(stanza)
        stanza = []

    for line in raw.splitlines():
        s = line.strip()
        mc = MARK_GROUP_RE.match(s)
        if mc:
            flush_stanza()
            group = mc.group(1)
            continue
        mk = MARK_UNIT_RE.match(s)
        if mk:
            flush_stanza()
            if cur is not None:
                units.append(cur)
            num = int(mk.group(1))
            title = mk.group(2).strip()
            cur = {"group": group, "num": num, "id": f"s{num:03d}",
                   "title": title or f"{group} {num}".strip(), "stanzas": []}
            continue
        if cur is None:
            continue                         # front matter before the first unit
        if s == "":
            flush_stanza()
        else:
            stanza.append(s)
    flush_stanza()
    if cur is not None:
        units.append(cur)
    return units


def _split_spans(full: str, boundary: set, split_newline: bool) -> list[tuple[int, int]]:
    """Split `full` into [start,end) spans at boundary chars (+ optional newline)."""
    spans, start, n, i = [], 0, len(full), 0
    while i < n:
        c = full[i]
        if c in boundary or (split_newline and c == "\n"):
            end = i + 1
            while end < n and full[end] in CLOSERS:
                end += 1
            if full[start:end].strip():
                spans.append((start, end))
            start = end
            i = end
        else:
            i += 1
    if full[start:].strip():
        spans.append((start, n))
    return spans


def segment_one(nlp, unit: dict, mode: str = "punct") -> dict:
    """spaCy-process one unit into a self-contained per-unit data dict.

    mode='punct' (Dante/prose): sentences split on terminal punctuation, segments on clause
    punctuation, neither on line breaks (enjambment).
    mode='verse' (unpunctuated verse like the Shahnameh): the metre carries the structure, so a
    sentence = one bayt (stanza) and a segment = one hemistich (line). No reliance on punctuation.
    """
    cid = unit["id"]
    flat: list[tuple[str, str]] = []                 # (lineId, text)
    spans: dict[str, tuple[int, int]] = {}
    pieces, cursor, li = [], 0, 0
    stanzas_out = []
    line_stanza: dict[str, int] = {}                 # lid -> stanza index
    for si, stanza in enumerate(unit["stanzas"]):
        st_lines = []
        for text in stanza:
            li += 1
            lid = f"{cid}-l{li}"
            flat.append((lid, text))
            spans[lid] = (cursor, cursor + len(text))
            pieces.append(text)
            cursor += len(text) + 1                   # +1 for the joining "\n"
            st_lines.append(lid)
            line_stanza[lid] = si
        stanzas_out.append({"lines": st_lines})

    full = "\n".join(pieces)
    flat_text = dict(flat)
    doc = nlp(full)

    def trim_start(s, e):
        raw = full[s:e]
        return s + (len(raw) - len(raw.lstrip()))

    def lines_overlapping(s, e):
        return [lid for lid, (a, b) in spans.items() if a < e and b > s]

    out_sents, out_segs = {}, {}

    if mode == "verse":
        # sentence per bayt (stanza), segment per hemistich (line); structure, not punctuation.
        seg_of_line: dict[str, tuple[str, str]] = {}     # lid -> (segId, sid)
        gk = 0
        for si, st in enumerate(stanzas_out, 1):
            sid = f"{cid}-s{si}"
            out_sents[sid] = {"text": " ".join(flat_text[l] for l in st["lines"]),
                              "lines": list(st["lines"]), "segs": []}
            for lid in st["lines"]:
                gk += 1
                segid = f"{cid}-g{gk}"
                out_segs[segid] = {"text": flat_text[lid], "sid": sid, "lines": [lid]}
                out_sents[sid]["segs"].append(segid)
                seg_of_line[lid] = (segid, sid)
        line_sids = {lid: [sid] for lid, (_g, sid) in seg_of_line.items()}

        def loc_of(off):
            lid = next((l for l, (a, b) in spans.items() if a <= off < b), flat[-1][0])
            return lid, seg_of_line[lid]
    else:
        sent_index = []
        for k, (s, e) in enumerate(_split_spans(full, SENT_BOUNDARY, split_newline=False), 1):
            s2 = trim_start(s, e)
            sid = f"{cid}-s{k}"
            out_sents[sid] = {"text": full[s2:e].strip(), "lines": lines_overlapping(s2, e),
                              "segs": []}
            sent_index.append((sid, s2, e))

        def sid_of(off):
            for sid, s, e in sent_index:
                if s <= off < e:
                    return sid
            return sent_index[-1][0]

        seg_index = []
        for k, (s, e) in enumerate(_split_spans(full, SEG_BOUNDARY, split_newline=False), 1):
            s2 = trim_start(s, e)
            segid = f"{cid}-g{k}"
            sid = sid_of(s2)
            out_segs[segid] = {"text": full[s2:e].strip(), "sid": sid,
                               "lines": lines_overlapping(s2, e)}
            out_sents[sid]["segs"].append(segid)
            seg_index.append((segid, s2, e))

        def seg_of(off):
            for segid, s, e in seg_index:
                if s <= off < e:
                    return segid, out_segs[segid]["sid"]
            last = seg_index[-1][0]
            return last, out_segs[last]["sid"]

        line_sids = {lid: [] for lid, _ in flat}
        for sid, _s, _e in sent_index:
            for lid in out_sents[sid]["lines"]:
                line_sids[lid].append(sid)

    toks = {lid: [] for lid, _ in flat}
    for t in doc:
        if t.is_space or t.is_punct:
            continue
        lid = next((l for l, (a, b) in spans.items() if a <= t.idx < b), None)
        if lid is None:
            continue
        a, _ = spans[lid]
        if mode == "verse":
            seg, sid = seg_of_line[lid]
        else:
            seg, sid = seg_of(t.idx)
        toks[lid].append({
            "s": t.idx - a, "e": t.idx - a + len(t.text),
            "surface": t.text, "lemma": t.lemma_.strip().lower() or t.text.lower(),
            "pos": t.pos_, "seg": seg, "sid": sid,
        })

    out_lines = {lid: {"text": text, "sids": line_sids.get(lid, []), "tokens": toks[lid]}
                 for lid, text in flat}

    return {
        "id": cid, "group": unit["group"], "num": unit["num"],
        "title": unit["title"],
        "stanzas": stanzas_out, "lines": out_lines,
        "sentences": out_sents, "segments": out_segs,
    }


class _FaNLP:
    """spaCy blank('fa') tokeniser (real offsets + punct/space flags) + Hazm lemmas.

    No Persian spaCy pipeline exists, so we tokenise with the rule-based blank-fa tokenizer and
    fill each content token's lemma from Hazm. POS is only used downstream to drop PUNCT/SYM/
    SPACE/NUM/X (SKIP_POS), so content words get a generic non-skip tag ('NOUN'); the user-facing
    part of speech comes from the glossary headword, not this token tag.
    """

    def __init__(self):
        import spacy
        from hazm import Lemmatizer
        self.tok = spacy.blank("fa")
        self.lem = Lemmatizer()

    def __call__(self, text):
        doc = self.tok(text)
        for t in doc:
            if t.is_space or t.is_punct:
                continue
            if t.like_num:
                t.pos_ = "NUM"
                continue
            t.lemma_ = self.lem.lemmatize(t.text) or t.text
            t.pos_ = "NOUN"
        return doc


def _make_nlp(target: str, model: str):
    if target == "fa":
        return _FaNLP()
    import spacy
    return spacy.load(model)


def _normalize_raw(raw: str, target: str) -> str:
    """Persian: normalise script (Arabic↔Persian chars, ZWNJ, spacing) at ingest, line by line
    so structure/offsets stay consistent through the whole pipeline. No-op for other languages."""
    if target != "fa":
        return raw
    from hazm import Normalizer
    norm = Normalizer()
    return "\n".join(norm.normalize(l) if l.strip() else l for l in raw.splitlines())


def build_work(raw: str, *, target: str, slug: str, title: str, author: str, source: str,
               model: str, fmt: str = "headed", unit: str = "Unit", only=None,
               verbose=True) -> dict:
    """Segment all (or selected) units, write per-unit files + the meta manifest.

    fmt: 'headed' (Dante-style verse; auto-detects in-text unit headers), 'prose' (parts/chapters/paragraphs), or 'marked'
    (=== group / --- num title markers; unpunctuated verse → bayt/hemistich segmentation).
    unit: display label for one navigable unit ('Unit' / 'Chapter' / 'Story').
    """
    raw = _normalize_raw(raw, target)
    nlp = _make_nlp(target, model)
    seg_mode = "verse" if fmt == "marked" else "punct"
    if fmt == "prose":
        units = parse_prose(raw)
    elif fmt == "marked":
        units = parse_marked(raw)
    else:
        units = parse_headed(raw)
    if only:
        wanted = set(only)
        sel = [c for c in units if c["id"] in wanted or str(c["num"]) in wanted]
    else:
        sel = units
    if not sel:
        raise SystemExit(f"No units matched {only!r}")

    manifest = []
    totals = {"lines": 0, "sentences": 0, "segments": 0, "lemmas": set()}
    for c in units:                                  # manifest covers ALL units
        manifest.append({"id": c["id"], "group": c["group"], "num": c["num"],
                         "title": c["title"],
                         "incipit": (c["stanzas"][0][0] if c["stanzas"] else "")})

    for c in sel:
        data = segment_one(nlp, c, mode=seg_mode)
        store.save_unit(target, slug, c["id"], data)
        nl, ns, ng = len(data["lines"]), len(data["sentences"]), len(data["segments"])
        totals["lines"] += nl; totals["sentences"] += ns; totals["segments"] += ng
        totals["lemmas"].update(t["lemma"] for ln in data["lines"].values()
                                for t in ln["tokens"])
        # backfill manifest line count
        for m in manifest:
            if m["id"] == c["id"]:
                m["lines"] = nl
        if verbose:
            print(f"  {c['id']}: {nl} lines, {ns} sentences, {ng} segments")

    store.save_meta(target, slug, {
        "work": slug, "title": title, "author": author, "target_lang": target,
        "source": source, "unit": unit,
        "groups": list(dict.fromkeys(c["group"] for c in units)),
        "units": manifest,
    })
    return {"units": len(sel), "cids": [c["id"] for c in sel],
            **{k: (len(v) if isinstance(v, set) else v) for k, v in totals.items()}}


def main():
    ap = argparse.ArgumentParser(description="Segment a raw text into per-unit reader files")
    ap.add_argument("raw_file")
    ap.add_argument("--target", default="it")
    ap.add_argument("--slug", default="dante-divine-comedy")
    ap.add_argument("--title", default="The Divine Comedy")
    ap.add_argument("--author", default="Dante Alighieri")
    ap.add_argument("--source", default="Project Gutenberg eBook #1012 (public domain)")
    ap.add_argument("--model", default="it_core_news_sm")
    ap.add_argument("--format", choices=["headed", "prose", "marked"], default="headed",
                    dest="fmt")
    ap.add_argument("--unit", default="Unit", help="display label (Unit / Chapter / Story)")
    ap.add_argument("--only", nargs="*", default=None,
                    help="unit ids or numbers to limit to (default: all)")
    args = ap.parse_args()

    raw = open(args.raw_file, encoding="utf-8").read()
    summ = build_work(raw, target=args.target, slug=args.slug, title=args.title,
                      author=args.author, source=args.source, model=args.model,
                      fmt=args.fmt, unit=args.unit, only=args.only)
    print(f"\nsegmented {summ['units']} unit(s): {summ['lines']} lines, "
          f"{summ['sentences']} sentences, {summ['segments']} segments, "
          f"{summ['lemmas']} unique lemmas")


if __name__ == "__main__":
    main()
