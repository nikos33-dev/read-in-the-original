#!/usr/bin/env python3
"""Minimal OpenAI chat client (JSON mode). Stdlib only.

Routes HTTPS through the system `curl` rather than urllib, which on some Python builds
fails CERTIFICATE_VERIFY_FAILED. The API key comes from the environment (OPENAI_API_KEY);
a non-empty value in a local .env file (if present) is honored too.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parent      # repo root

DEFAULT_MODEL = "gpt-5-mini"


def load_env() -> None:
    """Load non-empty values from a local .env, falling back to the shell env."""
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip().strip('"').strip("'")
        if v:
            os.environ[k.strip()] = v


def _key() -> str:
    load_env()
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError(
            "OPENAI_API_KEY not set. Export it in your shell (or put it in a local .env) "
            "before running the precompute pipeline."
        )
    return key


def chat_json(system: str, user: str, model: str | None = None,
              timeout: int = 120, usage_sink: list | None = None) -> dict:
    """One chat-completions call expecting a JSON object back. Returns the parsed dict.

    Uses response_format=json_object and keeps the payload minimal (no temperature —
    some reasoning models reject non-default sampling params). Raises on transport or
    API error. If usage_sink is given, the API's usage block is appended for metering.
    """
    key = _key()
    mdl = model or os.environ.get("OPENAI_MODEL") or DEFAULT_MODEL
    body = json.dumps({
        "model": mdl,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "response_format": {"type": "json_object"},
    }).encode("utf-8")

    proc = subprocess.run(
        ["curl", "-sS", "--max-time", str(timeout),
         "https://api.openai.com/v1/chat/completions",
         "-H", f"Authorization: Bearer {key}",
         "-H", "Content-Type: application/json",
         "--data-binary", "@-"],
        input=body, capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"curl failed: {proc.stderr.decode(errors='replace')[:400]}")
    out = proc.stdout.decode(errors="replace")
    try:
        resp = json.loads(out)
    except json.JSONDecodeError:
        raise RuntimeError(f"non-JSON from OpenAI: {out[:400]}")
    if resp.get("error"):
        raise RuntimeError(f"OpenAI error (model={mdl}): {json.dumps(resp['error'])[:400]}")
    if usage_sink is not None:
        usage_sink.append(resp.get("usage") or {})
    content = (((resp.get("choices") or [{}])[0]).get("message") or {}).get("content", "")
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        raise RuntimeError(f"model did not return JSON: {content[:400]}")
