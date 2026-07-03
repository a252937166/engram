# ENGRAM evaluation

Reproducible benchmark of ENGRAM against two baselines on the **same Qwen
model** (`qwen3.7-plus`), exercising exactly what Track 1 asks for: accurate
decisions across sessions, efficient storage/retrieval, timely forgetting,
and recall of critical memories inside a limited context window.

## Method

- **no-memory baseline** — the question alone, no user context (what any
  stateless agent sees).
- **full-history baseline** — every prior conversation message stuffed into
  the prompt (the naive alternative to a memory engine).
- **ENGRAM** — the live engine: extraction → hybrid retrieval under an
  ~800-token budget → belief revision → sleep-cycle consolidation.

All token numbers are read from the `usage` field of real Qwen Cloud API
responses — nothing is estimated. The suite seeds a fresh user, teaches four
facts, then buries them under **13 turns of unrelated small talk** before
testing.

## Results (measured 2026-07-04)

| Scenario | no-memory baseline | full-history baseline | ENGRAM | pass |
|---|---|---|---|---|
| S1 Cross-session recall (13 turns of noise in between) | knows nothing about the user | correct but pays full context | SAFE picnic menu, allergy memory recalled — **182 tk vs 512 tk prompt** | ✅ |
| S2 Belief revision (changed employer) | cannot answer at all | old + new facts coexist in prompt; model must re-reason every turn | supersede op fired; **zero stale-memory leakage** in a fresh session | ✅ |
| S3 Context economy after 32 messages | 0 tk but amnesiac | **849 tk** history, grows without bound | **48 tk** whole memory store (94% smaller); per-turn block ≤ 800 tk by construction | ✅ |
| S4 Sleep cycle (3 related Tokyo fragments) | n/a | history only ever grows | 3 fragments → 1 dense memory; active 8 → 6; store 98 → 74 tk | ✅ |
| S5 Safety-critical recall at low similarity | suggests trail mix (peanuts) blindly | correct but needs the whole transcript | allergy recalled at **semantic = 0.31** via the importance rescue floor | ✅ |

**5/5 scenarios passed.**

Key deltas:

- **2.8× cheaper prompts** than full-history stuffing at message 32 — and the
  gap widens every turn, because history grows linearly while the memory
  store *shrinks* under consolidation (S3, S4).
- **Belief revision is structural, not prompt luck**: the stale employer fact
  is status=`superseded` in the store, so it *cannot* be retrieved (S2). A
  full-history baseline keeps both facts in context forever and bets on the
  model re-reasoning correctly every single turn.
- **The rescue floor works**: "trail snack" and "peanut allergy" score only
  0.31 cosine — below the normal retrieval floor — yet the allergy surfaces
  because importance ≥ 0.85 memories use a lower floor (S5). This is the
  Track 1 "recall critical memories in a limited window" requirement,
  demonstrated with a measured number.

## Reproduce

```bash
export QWEN_API_KEY=sk-...           # home.qwencloud.com/api-keys
ENGRAM_PORT=8791 ENGRAM_DB=/tmp/eval.db python3 backend/server.py &
python3 eval/run_eval.py --base http://127.0.0.1:8791
```

The suite costs roughly 25k tokens per run (well inside the free tier) and
takes ~4 minutes. Judgments are deterministic (memory-store state and
retrieval contents), not LLM-graded.

## Environment

- Engine: this repository @ the commit that ships this file
- Models: `qwen3.7-plus`, `qwen3.6-flash`, `text-embedding-v4` via
  `https://dashscope-intl.aliyuncs.com/compatible-mode/v1`
- Production reference host: Alibaba Cloud ECS `i-2zefhmpp3htrijv7plwr`
  (cn-beijing-c) — live at http://47.93.234.51:8080
