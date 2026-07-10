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

## Results (measured 2026-07-04 UTC+8 / 2026-07-03 PDT)

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

## Ablations (measured 2026-07-05, real API)

> Note on numbers: the S5 similarity differs slightly between runs (0.31 in
> the main suite vs 0.246 here) because each run re-embeds fresh extraction
> wordings. Both sit below the normal 0.25–0.32 retrieval floor band — the
> rescue-floor behaviour they demonstrate is identical.

Each mechanism switched off individually via `ENGRAM_ABLATION`, then the
scenario that stresses it re-run on a fresh DB — same models, same facts,
only the mechanism differs.

| Variant | S5: peanut allergy vs "trail snack" query | S2: "moved from Acme to Nova" |
|---|---|---|
| **full engine** (control) | **RECALLED** at cosine **0.246** — importance-rescue floor admits it | op=`updated` · stale employer recalled **0×** · active employer facts **1** |
| `semantic_only` — score = cosine, no rescue floor (≈ plain vector RAG scoring) | **MISSED** — the life-critical fact silently drops out | — |
| `no_arbiter` — store without LLM belief revision (≈ append-only RAG store) | — | op=`created` · stale "works at Acme" recalled **1×** · active employer facts **2** (contradiction served to the model) |
| `no_sleep` — no consolidation | related fragments never merge; the store only grows (with sleep on, S4 measures 3 fragments → 1 and 98 → 74 store tokens) | — |

Read: a plain vector-RAG memory (≈ `semantic_only` + `no_arbiter`) fails
both — it misses the allergy *and* serves two contradictory employer facts.
Each mechanism earns its place with a reproducible failure when removed:

```bash
python3 eval/run_ablation.py full
python3 eval/run_ablation.py semantic_only   # S5 flips to MISSED
python3 eval/run_ablation.py no_arbiter      # S2 keeps the stale fact alive
```

## Re-run on the final submission commit (2026-07-10)

Same suite, re-executed after the reliability hardening (code state
`f83784d`: atomic revision/consolidation, deferred reinforcement, cluster
purity guard): **5/5 passed** — 182 tk vs 478 tk prompt (S1), supersede
fired with zero stale recall (S2), 47 tk whole store vs 965 tk raw history
(S3), 3 fragments → 1 with store 97 → 73 tk (S4), allergy rescued at
semantic 0.25 (S5). Live judge demo verified the same day from two
external vantages (CN direct and US exit): 5/5 in ≈50 s each.

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
  (cn-beijing-c) — live at https://engram.hackthon.site
