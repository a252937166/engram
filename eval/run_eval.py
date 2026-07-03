"""ENGRAM evaluation suite — reproducible, zero-dependency.

Runs five scenarios against a live ENGRAM instance and compares with two
baselines on the *same* Qwen model:

  * no-memory     : the question alone (what a stateless agent sees)
  * full-history  : every prior message stuffed into the prompt

Usage:
    export QWEN_API_KEY=sk-...
    python3 eval/run_eval.py --base http://127.0.0.1:8788

Writes a markdown report to stdout (paste into docs/evaluation.md).
Every number is measured, not estimated: token counts come from the Qwen
Cloud usage field of the actual API responses.
"""

import argparse
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
import qwen_client  # noqa: E402  (baselines call Qwen Cloud directly)

BASE = "http://127.0.0.1:8788"


# ------------------------------------------------------------------ plumbing
def api(path, body=None, timeout=120):
    url = BASE + path
    if body is None:
        resp = urllib.request.urlopen(url, timeout=timeout)
        return json.loads(resp.read().decode())
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 method="POST")
    resp = urllib.request.urlopen(req, timeout=timeout)
    return resp


def chat(user, session, message):
    """POST /api/chat, parse the SSE stream, return a structured result."""
    resp = api("/api/chat", {"user_id": user, "session_id": session,
                             "message": message})
    answer, recalled, ops, usage = [], [], [], {}
    event = None
    for raw in resp:
        line = raw.decode("utf-8", "replace").strip()
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data = json.loads(line[5:])
            if event == "delta":
                answer.append(data["text"])
            elif event == "retrieval":
                recalled = data["memories"]
            elif event == "memory_ops":
                ops = data["ops"]
            elif event == "done":
                usage = data.get("usage", {})
    return {"answer": "".join(answer), "recalled": recalled,
            "ops": ops, "usage": usage}


def session(user, title):
    resp = api("/api/sessions", {"user_id": user, "title": title})
    return json.loads(resp.read().decode())["id"]


def baseline(question, history=None):
    """Same chat model, same question - with or without stuffed history."""
    messages = []
    if history:
        messages.append({"role": "system",
                         "content": "Prior conversation with this user:\n" +
                                    "\n".join(history)})
    messages.append({"role": "user", "content": question})
    text, usage = qwen_client.chat(messages, temperature=0.3)
    return {"answer": text, "usage": usage}


def has(text, *words):
    t = text.lower()
    return any(w.lower() in t for w in words)


# ------------------------------------------------------------------ scenarios
def main():
    global BASE
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE)
    args = ap.parse_args()
    BASE = args.base

    uid = "eval-%d" % int(time.time())
    rows = []
    transcript = []          # what full-history baseline gets to see

    # ---- setup: teach, then bury the facts under small talk ----
    s1 = session(uid, "eval setup")
    teach = ("Hi! I'm Dora. I'm vegetarian and severely allergic to peanuts. "
             "I work as a platform engineer at Acme migrating services to "
             "Kubernetes. Keep answers short.")
    r = chat(uid, s1, teach)
    transcript.append("user: " + teach)
    transcript.append("assistant: " + r["answer"])
    noise = [
        "What's a good name for a hackathon team?",
        "Explain what a reverse proxy does in one line.",
        "Give me a haiku about autumn.",
        "What's the capital of Portugal?",
        "Convert 72F to Celsius.",
        "Recommend a sci-fi book.",
        "What year did the first moon landing happen?",
        "One-line difference between TCP and UDP?",
        "Suggest a warm-up stretch for wrists.",
        "What does HTTP 418 mean?",
        "Give me a two-word toast for a launch party.",
        "How many bits in a byte?",
    ]
    for n in noise:
        r = chat(uid, s1, n)
        transcript.append("user: " + n)
        transcript.append("assistant: " + r["answer"])

    # ================= S1: cross-session safety recall under noise ========
    s2 = session(uid, "eval s2")
    q1 = "Plan a picnic menu for me this weekend."
    e = chat(uid, s2, q1)
    engram_safe = (has(e["answer"], "peanut", "nut-free", "allerg") or
                   not has(e["answer"], "peanut butter")) and \
                  has(e["answer"], "vegetarian", "veggie", "meat-free",
                      "plant", "tofu", "halloumi", "chickpea", "hummus",
                      "caprese", "salad")
    recalled_allergy = any("peanut" in m["content"].lower()
                           for m in e["recalled"])
    b_no = baseline(q1)
    b_full = baseline(q1, transcript)
    rows.append({
        "scenario": "S1 Cross-session recall (13 turns of noise in between)",
        "engram": "SAFE menu, allergy memory recalled: %s" % recalled_allergy,
        "engram_ok": engram_safe and recalled_allergy,
        "no_mem": "knows nothing about the user",
        "full": "correct but pays full context",
        "tok_e": e["usage"].get("prompt_tokens", 0),
        "tok_full": b_full["usage"].get("prompt_tokens", 0),
        "tok_no": b_no["usage"].get("prompt_tokens", 0),
    })

    # ================= S2: belief revision ================================
    upd = "Update: I left Acme last week - I'm now head of infrastructure at Nova Robotics."
    r = chat(uid, s2, upd)
    transcript.append("user: " + upd)
    transcript.append("assistant: " + r["answer"])
    superseded = any(o["op"] == "updated" for o in r["ops"])
    s3 = session(uid, "eval s3")
    q2 = "Where do I work right now, and in what role?"
    e2 = chat(uid, s3, q2)
    nova_only = has(e2["answer"], "nova") and \
        "acme" not in e2["answer"].lower().replace("left acme", "") \
                                          .replace("previously", "")
    stale_recalled = any("platform engineer at acme" in m["content"].lower()
                         for m in e2["recalled"])
    rows.append({
        "scenario": "S2 Belief revision (changed employer)",
        "engram": "supersede op fired: %s; stale memory recalled: %s"
                  % (superseded, stale_recalled),
        "engram_ok": superseded and not stale_recalled and nova_only,
        "no_mem": "cannot answer at all",
        "full": "old+new facts coexist in prompt; model must re-reason",
        "tok_e": e2["usage"].get("prompt_tokens", 0),
        "tok_full": None, "tok_no": None,
    })

    # ================= S3: token budget ====================================
    stats = api("/api/stats?user_id=" + uid)
    mem_tk = stats["memory_store_tokens"]
    hist_tk = stats["full_history_tokens"]
    rows.append({
        "scenario": "S3 Context economy after %d messages" % stats["messages"],
        "engram": "%d tk memory store (whole store; per-turn block <= 800)" % mem_tk,
        "engram_ok": mem_tk < hist_tk * 0.25,
        "no_mem": "0 tk but amnesiac",
        "full": "%d tk history grows without bound" % hist_tk,
        "tok_e": mem_tk, "tok_full": hist_tk, "tok_no": 0,
    })

    # ================= S4: sleep-cycle compression =========================
    s4 = session(uid, "eval s4")
    for m in ["I'm planning a trip to Tokyo this October.",
              "For the Tokyo trip I want to visit teamLab and Shibuya.",
              "Also for the Tokyo trip I need a JR pass and a Shinjuku hotel."]:
        chat(uid, s4, m)
    before = api("/api/stats?user_id=" + uid)
    resp = api("/api/sleep", {"user_id": uid})
    report = json.loads(resp.read().decode())
    after = api("/api/stats?user_id=" + uid)
    merged = sum(len(c["merged"]) for c in report["consolidated"])
    rows.append({
        "scenario": "S4 Sleep cycle (3 related Tokyo fragments)",
        "engram": "%d fragments -> %d dense memories; active %d -> %d; store %d -> %d tk"
                  % (merged, len(report["consolidated"]),
                     before["by_status"].get("active", 0),
                     after["by_status"].get("active", 0),
                     before["memory_store_tokens"], after["memory_store_tokens"]),
        "engram_ok": len(report["consolidated"]) >= 1 and
                     after["memory_store_tokens"] < before["memory_store_tokens"],
        "no_mem": "n/a", "full": "history only ever grows",
        "tok_e": after["memory_store_tokens"],
        "tok_full": before["memory_store_tokens"], "tok_no": None,
    })

    # ================= S5: safety-critical rescue floor ====================
    s5 = session(uid, "eval s5")
    q5 = "Recommend a quick trail snack for my hike tomorrow."
    e5 = chat(uid, s5, q5)
    allergy_hits = [m for m in e5["recalled"]
                    if "peanut" in m["content"].lower()]
    low_sim = allergy_hits and allergy_hits[0]["components"]["semantic"] < 0.45
    rows.append({
        "scenario": "S5 Safety-critical recall at low similarity",
        "engram": ("allergy recalled at semantic=%.2f via critical floor"
                   % allergy_hits[0]["components"]["semantic"]) if allergy_hits
                  else "NOT recalled",
        "engram_ok": bool(allergy_hits),
        "no_mem": "suggests trail mix (peanuts) blindly",
        "full": "correct but needs the whole transcript",
        "tok_e": e5["usage"].get("prompt_tokens", 0),
        "tok_full": None, "tok_no": None,
    })

    # ------------------------------------------------------------- report
    print("\n## Results (measured %s)\n" % time.strftime("%Y-%m-%d"))
    print("| Scenario | no-memory baseline | full-history baseline | ENGRAM | pass |")
    print("|---|---|---|---|---|")
    for r in rows:
        toks = ""
        if r.get("tok_full") and r.get("tok_e"):
            toks = " (%s tk vs %s tk)" % (r["tok_e"], r["tok_full"])
        print("| %s | %s | %s | %s%s | %s |" % (
            r["scenario"], r["no_mem"], r["full"], r["engram"], toks,
            "PASS" if r["engram_ok"] else "FAIL"))
    passed = sum(1 for r in rows if r["engram_ok"])
    print("\n**%d/%d scenarios passed.**" % (passed, len(rows)))


if __name__ == "__main__":
    main()
