"""Ablation runner - measures what each engine mechanism contributes.

Talks to the engine directly (no HTTP), against a throwaway SQLite file,
with one mechanism disabled via ENGRAM_ABLATION:

    python3 eval/run_ablation.py full           # everything on (control)
    python3 eval/run_ablation.py semantic_only  # score = cosine, no rescue floor
    python3 eval/run_ablation.py no_arbiter     # store facts without belief revision

Uses the real Qwen Cloud API (needs QWEN_API_KEY), a few hundred tokens per
run. Results feed the ablation table in docs/evaluation.md.
"""

import os
import sys
import tempfile

MODE = sys.argv[1] if len(sys.argv) > 1 else "full"
os.environ["ENGRAM_ABLATION"] = MODE if MODE != "full" else ""
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "backend"))

from engine import MemoryEngine  # noqa: E402


def main():
    eng = MemoryEngine(tempfile.mktemp(suffix=".db"))
    uid = "abl-" + MODE

    # S5: safety-critical recall at low semantic similarity.
    # "trail snack" vs the allergy memory measures ~0.25 cosine - below the
    # normal floor. Only the importance-rescue floor can admit it.
    if MODE in ("full", "semantic_only"):
        eng.extract_and_remember(
            uid, "s1",
            "Hi, I'm Dora. I'm vegetarian and severely allergic to peanuts. "
            "I work as a platform engineer at Acme.")
        rec = eng.retrieve(uid, "Recommend a quick trail snack for my hike tomorrow.")
        hit = [m for m in rec if "peanut" in m["content"].lower()]
        sim = round(hit[0]["components"]["semantic"], 4) if hit else None
        print("%s | S5 allergy-at-low-sim: %s (semantic=%s)"
              % (MODE, "RECALLED" if hit else "MISSED", sim))

    # S2: belief revision. Without the arbiter the old employer fact stays
    # active and gets recalled next to the new one.
    if MODE in ("full", "no_arbiter"):
        eng.extract_and_remember(
            uid, "s1",
            "I work as a platform engineer at Acme migrating services to Kubernetes.")
        ops = eng.extract_and_remember(
            uid, "s1",
            "Update: I left Acme last week - I'm now head of infrastructure "
            "at Nova Robotics.")
        rec = eng.retrieve(uid, "Where do I work right now and in what role?")
        stale = [m for m in rec if "acme" in m["content"].lower()
                 and "left" not in m["content"].lower()]
        active = [n for n in eng.memory_graph(uid)["nodes"]
                  if n["status"] == "active"
                  and ("acme" in n["content"].lower()
                       or "nova" in n["content"].lower())]
        print("%s | S2 update ops: %s | stale Acme recalled: %d | "
              "employer-facts active: %d"
              % (MODE, [o["op"] for o in ops], len(stale), len(active)))


if __name__ == "__main__":
    main()
