"""ENGRAM x Qwen Cloud (Alibaba Cloud Model Studio) integration.

== PROOF OF ALIBABA CLOUD USAGE ==
Every model call in ENGRAM goes through this module to Alibaba Cloud's
Qwen Cloud / Model Studio international endpoint:

    https://dashscope-intl.aliyuncs.com/compatible-mode/v1

Models used:
  * qwen3.7-plus      - conversational reasoning (streaming chat)
  * qwen3.6-flash     - memory extraction, conflict arbitration, consolidation
  * text-embedding-v4 - 256-dim memory embeddings for semantic retrieval

Pure Python standard library (urllib) on purpose: the production host is a
728 MB RAM CentOS 7 box, so ENGRAM runs with zero pip dependencies.
"""

import json
import os
import ssl
import time
import urllib.error
import urllib.request

QWEN_BASE_URL = os.environ.get(
    "QWEN_BASE_URL", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
)
QWEN_API_KEY = os.environ.get("QWEN_API_KEY", "")

CHAT_MODEL = os.environ.get("ENGRAM_CHAT_MODEL", "qwen3.7-plus")
FAST_MODEL = os.environ.get("ENGRAM_FAST_MODEL", "qwen3.6-flash")
EMBED_MODEL = os.environ.get("ENGRAM_EMBED_MODEL", "text-embedding-v4")
EMBED_DIM = int(os.environ.get("ENGRAM_EMBED_DIM", "256"))

_SSL_CTX = ssl.create_default_context()


class QwenError(Exception):
    """Raised when Qwen Cloud returns an unrecoverable error."""


def _request(path, payload, timeout=90):
    """POST JSON to Qwen Cloud, with retry on transient failures."""
    if not QWEN_API_KEY:
        raise QwenError("QWEN_API_KEY is not configured")
    body = json.dumps(payload).encode("utf-8")
    last_err = None
    for attempt in range(3):
        req = urllib.request.Request(
            QWEN_BASE_URL + path,
            data=body,
            headers={
                "Authorization": "Bearer " + QWEN_API_KEY,
                "Content-Type": "application/json",
                "User-Agent": "engram-memory-agent/1.0",
            },
            method="POST",
        )
        try:
            return urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX)
        except urllib.error.HTTPError as err:
            detail = err.read().decode("utf-8", "replace")[:500]
            if err.code in (429, 500, 502, 503, 504) and attempt < 2:
                last_err = "HTTP %d: %s" % (err.code, detail)
                time.sleep(1.5 * (attempt + 1))
                continue
            raise QwenError("HTTP %d from Qwen Cloud: %s" % (err.code, detail))
        except (urllib.error.URLError, OSError) as err:
            last_err = str(err)
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
    raise QwenError("Qwen Cloud unreachable after retries: %s" % last_err)


def chat(messages, model=None, temperature=0.7, max_tokens=1200,
         enable_thinking=False, timeout=90):
    """Non-streaming chat completion. Returns (text, usage_dict)."""
    resp = _request(
        "/chat/completions",
        {
            "model": model or CHAT_MODEL,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "enable_thinking": enable_thinking,
        },
        timeout=timeout,
    )
    data = json.loads(resp.read().decode("utf-8"))
    choice = data["choices"][0]["message"]
    return choice.get("content") or "", data.get("usage", {})


def chat_stream(messages, model=None, temperature=0.7, max_tokens=1200,
                enable_thinking=False, timeout=120):
    """Streaming chat completion.

    Yields ("delta", text) chunks and finally ("usage", usage_dict).
    """
    resp = _request(
        "/chat/completions",
        {
            "model": model or CHAT_MODEL,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "enable_thinking": enable_thinking,
            "stream": True,
            "stream_options": {"include_usage": True},
        },
        timeout=timeout,
    )
    usage = {}
    for raw in resp:
        line = raw.decode("utf-8", "replace").strip()
        if not line.startswith("data:"):
            continue
        line = line[5:].strip()
        if line == "[DONE]":
            break
        try:
            chunk = json.loads(line)
        except ValueError:
            continue
        if chunk.get("usage"):
            usage = chunk["usage"]
        choices = chunk.get("choices") or []
        if choices:
            delta = choices[0].get("delta") or {}
            text = delta.get("content")
            if text:
                yield ("delta", text)
    yield ("usage", usage)


def extract_json(messages, model=None, max_tokens=700):
    """Ask the fast model for a JSON-only answer and parse it defensively."""
    text, _usage = chat(
        messages,
        model=model or FAST_MODEL,
        temperature=0.1,
        max_tokens=max_tokens,
        enable_thinking=False,
    )
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    start = min(
        (i for i in (text.find("["), text.find("{")) if i >= 0), default=-1
    )
    if start < 0:
        return None
    end = max(text.rfind("]"), text.rfind("}"))
    if end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except ValueError:
        return None


def embed(texts, dimensions=None):
    """Embed a list of strings. Returns list of float vectors."""
    if not texts:
        return []
    vectors = []
    # text-embedding-v4 accepts up to 10 inputs per call
    for i in range(0, len(texts), 10):
        batch = texts[i:i + 10]
        resp = _request(
            "/embeddings",
            {
                "model": EMBED_MODEL,
                "input": batch,
                "dimensions": dimensions or EMBED_DIM,
            },
            timeout=30,
        )
        data = json.loads(resp.read().decode("utf-8"))
        rows = sorted(data["data"], key=lambda d: d["index"])
        vectors.extend(row["embedding"] for row in rows)
    return vectors
