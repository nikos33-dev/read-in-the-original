#!/usr/bin/env python3
"""Dictionary-API reference lookup for a lemma, used to GROUND the LLM gloss.

Per the design: the precompute first consults a real dictionary for the language (if one
exists) and feeds those definitions to the LLM as reference, rather than letting the model
invent meanings unaided. The LLM still writes the final reader-language gloss; the
dictionary is the source it's told to prefer.

Primary source: the English Wiktionary REST definition endpoint
  https://en.wiktionary.org/api/rest_v1/page/definition/<word>
which returns definitions grouped by source language, written in English -> ideal reference
for English-reader glosses of any supported target language. Returns [] when the word/section
isn't found; callers degrade to a pure-LLM gloss.

Network via /usr/bin/curl (macOS cert store), matching oai.py. Stdlib otherwise.
"""
from __future__ import annotations

import html
import json
import re
import subprocess
import urllib.parse

# Wiktionary section language codes match our target codes (it, fr, es, de, ...).
WIKTIONARY = "https://en.wiktionary.org/api/rest_v1/page/definition/{word}"
TAG_RE = re.compile(r"<[^>]+>")


def _strip(s: str) -> str:
    return html.unescape(TAG_RE.sub("", s or "")).strip()


def _get(url: str, timeout: int = 20) -> dict | list | None:
    proc = subprocess.run(
        ["curl", "-sS", "--max-time", str(timeout),
         "-H", "User-Agent: read-in-the-original/1.0 (language study; precompute)",
         url],
        capture_output=True,
    )
    if proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout.decode(errors="replace"))
    except json.JSONDecodeError:
        return None


def lookup(word: str, target: str, max_defs: int = 4) -> dict:
    """Return {source, pos, definitions:[...]} for `word` in language `target`.

    Empty definitions list when nothing is found. `source` is the dictionary name or None.
    Multiword/contraction lemmas (containing a space) are skipped — dictionaries key on
    single headwords; the LLM handles those from context.
    """
    if not word or " " in word:
        return {"source": None, "pos": None, "definitions": []}
    url = WIKTIONARY.format(word=urllib.parse.quote(word))
    data = _get(url)
    if not isinstance(data, dict):
        return {"source": None, "pos": None, "definitions": []}
    entries = data.get(target) or []
    defs, pos = [], None
    for entry in entries:
        if pos is None and entry.get("partOfSpeech"):
            pos = entry["partOfSpeech"]
        for d in entry.get("definitions", []):
            txt = _strip(d.get("definition", ""))
            if txt:
                defs.append(txt)
            if len(defs) >= max_defs:
                break
        if len(defs) >= max_defs:
            break
    return {"source": "wiktionary" if defs else None, "pos": pos, "definitions": defs}


if __name__ == "__main__":
    import sys
    w = sys.argv[1] if len(sys.argv) > 1 else "selva"
    t = sys.argv[2] if len(sys.argv) > 2 else "it"
    print(json.dumps(lookup(w, t), ensure_ascii=False, indent=2))
