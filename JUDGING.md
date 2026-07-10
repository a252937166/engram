# Judge Quickstart

Five ways to verify ENGRAM, fastest first.

## 0. Live instance (10 seconds)

```bash
curl https://engram.hackthon.site/api/health   # Alibaba Cloud ECS, Beijing (direct IP: http://47.93.234.51:8080)
```

Open https://engram.hackthon.site (global mirror: https://engram.axiqo.xyz) —
you land on a seeded constellation; chat, watch recalls/supersedes live,
press **Sleep Cycle**. Try the DevOps scenario at
https://engram.hackthon.site/?seed=devops

## 1. Offline smoke — no API key needed (30 seconds)

```bash
git clone https://github.com/a252937166/engram && cd engram
ENGRAM_FAKE_QWEN=1 python3 tests/smoke_offline.py
# -> OFFLINE SMOKE: all assertions passed
```

Deterministic fake Qwen client; exercises extract → embed → arbitrate →
recall → forget → sleep → seed-clone with zero network. (Same check runs
in GitHub Actions on every push. Any warning shown on the Actions page is
GitHub's Node-runtime deprecation notice for `actions/checkout` /
`setup-python` — the ENGRAM smoke job itself passes.)

## 2. Run it yourself with a key (2 minutes)

```bash
export QWEN_API_KEY=sk-...        # https://home.qwencloud.com/api-keys
python3 backend/server.py         # http://127.0.0.1:8788
```

No pip install — pure Python 3.6+ stdlib.

## 3. Reproduce the benchmark (≈4 minutes, ≈25k tokens measured)

```bash
python3 eval/run_eval.py --base http://127.0.0.1:8788
# expected: 5/5 scenarios passed
```

Methodology + full tables (including ablations) in
[docs/evaluation.md](docs/evaluation.md).

## 4. Proof of Alibaba Cloud deployment

One page with instance identity, systemd state, and verifiable endpoints:
[docs/proof-of-deployment.md](docs/proof-of-deployment.md)

## 5. Mount ENGRAM into your own agent (MCP)

```json
{ "mcpServers": { "engram": {
    "command": "python3", "args": ["backend/mcp_server.py"],
    "env": { "QWEN_API_KEY": "sk-..." } } } }
```

Tools: `engram_remember` · `engram_recall` · `engram_forget` ·
`engram_sleep` · `engram_stats`.
