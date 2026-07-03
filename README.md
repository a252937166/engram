# ENGRAM — a neural long-term memory engine for Qwen agents

> **Global AI Hackathon Series with Qwen Cloud · Track 1: MemoryAgent**
>
> **Live on Alibaba Cloud ECS (Beijing):** http://47.93.234.51 · **Mirror (HTTPS):** https://engram.axiqo.xyz · **Demo video:** see Devpost submission

LLM agents wake up with amnesia every session. ENGRAM gives a Qwen agent a
**persistent, self-organizing long-term memory** modeled on how biological
memory actually works — and renders the whole thing live as an interactive
"memory constellation," so every recall, reinforcement, contradiction and
consolidation is visible and explainable.

Tell it once that you're vegetarian with a peanut allergy. Days later, in a
brand-new session, ask for a dinner idea — it quietly serves you something
safe. Tell it you changed jobs — the old belief is *superseded*, not
duplicated. Press **Sleep Cycle** — scattered episodic fragments consolidate
into dense semantic knowledge, and stale traces are forgotten.

![architecture](docs/architecture.png)

## Why this is not "just RAG"

| Naive approach | ENGRAM |
|---|---|
| Append full chat history to context | **~800-token memory budget**, greedy-packed from scored memories |
| Similarity-only retrieval | Hybrid score: `0.55·semantic + 0.18·recency + 0.17·importance + 0.10·usage`, with per-type half-lives and a rescue floor for safety-critical memories (allergies surface even at low similarity) |
| Contradictions pile up | **LLM arbitration**: embeddings shortlist neighbors, `qwen3.6-flash` rules *duplicate / replaces / distinct* — changed jobs supersede, a diet and an allergy coexist |
| Store grows forever | **Sleep cycle**: union-find clustering (cos ≥ .80) merges fragments into semantic summaries; retention `0.5·importance + 0.3·usage + 0.2·recency` below floor ⇒ forgotten |
| Memory is a black box | Every recall streams its **score components** to the UI; every memory keeps an audit trail (`superseded_by`, `consolidated_into`, access counts) |

### Memory lifecycle

```
user turn ─▶ EXTRACT (qwen3.6-flash, typed JSON)
                │  preference / semantic / procedural / episodic + importance
                ▼
             EMBED (text-embedding-v4, 256-d)
                │  cos ≥ .90 ──▶ reinforce existing trace (strength +1)
                │  cos ≥ .45 ──▶ ARBITER: duplicate? replaces? distinct?
                ▼                    │ replaces ⇒ old memory superseded
             INSERT (SQLite, WAL)   ◀┘
                ▼
             RECALL on next turn — hybrid score under token budget,
             recalled memories are reinforced (use strengthens the trace)
                ▼
             SLEEP CYCLE — consolidate clusters ⇒ semantic knowledge,
             forget low-retention traces (grace period for young memories)
```

## Built on Qwen Cloud (Alibaba Cloud)

All model calls go through **[`backend/qwen_client.py`](backend/qwen_client.py)**
— this file is the proof of Alibaba Cloud usage — against the Qwen Cloud
Model Studio international endpoint:

```
https://dashscope-intl.aliyuncs.com/compatible-mode/v1
```

| Model | Role |
|---|---|
| `qwen3.7-plus` | conversational reasoning, SSE streaming, grounded on the injected MEMORY block |
| `qwen3.6-flash` | memory extraction (typed+scored JSON), contradiction arbitration, sleep-cycle summarization |
| `text-embedding-v4` | 256-dim vectors for recall, dedupe and clustering |

The full stack (nginx + systemd + SQLite + backend) is deployed on an
**Alibaba Cloud ECS instance (Beijing)** — live at http://47.93.234.51 —
with an HTTPS mirror at https://engram.axiqo.xyz. All model inference
(reasoning, extraction, arbitration, embeddings) runs on Qwen Cloud, so both
the hosting and the AI backbone are Alibaba Cloud. (The hackthon.site domain
attaches once its ICP filing completes; the IP endpoint is unaffected.)

## Zero-dependency engineering

The production host is a **1-core / 728 MB** CentOS 7 box, so ENGRAM is
**pure Python 3.6 stdlib** — no pip install, no frameworks, no vector DB:

- `backend/server.py` — threaded HTTP + Server-Sent-Events API (~300 lines)
- `backend/engine.py` — the memory engine (~500 lines)
- `backend/qwen_client.py` — Qwen Cloud client with retry/backoff
- `backend/mcp_server.py` — the same engine exposed over MCP
- `frontend/index.html` — single-file UI, no frameworks: canvas force-layout
  constellation, SSE chat, retrieval-score chips, embedded fonts

Guard rails: per-IP rate limiting (nginx + in-process), daily demo quota,
input validation, memory caps per user, systemd `MemoryLimit`, key kept in
`/etc/engram/engram.env` (never in the repo or the client).

## Run it yourself

```bash
git clone https://github.com/a252937166/engram && cd engram
export QWEN_API_KEY=sk-...          # from https://home.qwencloud.com/api-keys
python3 backend/server.py           # http://127.0.0.1:8788
```

Production deploy (nginx + systemd, one shot):

```bash
QWEN_API_KEY=sk-... bash deploy/deploy.sh your.domain.com
certbot --nginx -d your.domain.com
```

## Mount ENGRAM into any agent (MCP)

The same memory store speaks the Model Context Protocol — Claude Code,
Qwen agents or any MCP client can share the agent's memory:

```json
{ "mcpServers": { "engram": {
    "command": "python3", "args": ["backend/mcp_server.py"],
    "env": { "QWEN_API_KEY": "sk-..." } } } }
```

Tools: `engram_remember` · `engram_recall` · `engram_forget` ·
`engram_sleep` · `engram_stats`.

## HTTP API

| Endpoint | Description |
|---|---|
| `POST /api/chat` | SSE stream: `retrieval` (scored memories) → `delta`* → `memory_ops` → `done` |
| `GET /api/memories?user_id=` | full memory graph: nodes + similarity links |
| `POST /api/sleep` | run consolidation + forgetting, returns the report |
| `POST /api/forget` | explicit right-to-be-forgotten for one memory |
| `GET /api/bootstrap` · `/api/stats` · `/api/messages` · `POST /api/sessions` | app plumbing |

## Judging map

- **Technical depth** — hybrid scored retrieval with per-type decay,
  LLM-arbitrated belief revision, sleep-cycle consolidation, MCP server,
  streaming pipeline; all on a 728 MB box with zero dependencies.
- **Innovation** — memory as a *first-class visualized citizen*: the
  constellation shows recall beams, reinforcement pulses, supersede flashes
  and consolidation vortexes in real time; every recall is explainable.
- **Value** — cross-session personalization under a fixed token budget
  (context stays ~800 tokens while history grows unbounded); engine is
  embeddable via MCP in any agent stack.
- **Docs** — this README + architecture diagram + one-shot deploy script.

## License

[MIT](LICENSE)
