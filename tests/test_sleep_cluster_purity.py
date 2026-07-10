"""Sleep-cycle cluster purity (offline fake Qwen).

Consolidation must merge same-theme fragments WITHOUT swallowing unrelated
facts through union-find transitivity:

    3 Tokyo-trip fragments  -> merge into one dense memory
    peanut allergy + employer facts -> stay separate and active

    ENGRAM_FAKE_QWEN=1 python3 tests/test_sleep_cluster_purity.py
"""

import os
import sys
import tempfile

os.environ["ENGRAM_FAKE_QWEN"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "backend"))

from engine import MemoryEngine  # noqa: E402


def main():
    eng = MemoryEngine(tempfile.mktemp(suffix=".db"))
    uid = "cluster-purity-01"

    theme = ["I'm planning a trip to Tokyo this October.",
             "For the Tokyo trip I want to visit teamLab and Shibuya.",
             "Also for the Tokyo trip I need a JR pass and a Shinjuku hotel."]
    unrelated = ["I have a severe peanut allergy.",
                 "I work as a platform engineer at Acme Robotics."]
    for t in theme + unrelated:
        eng.extract_and_remember(uid, "s1", t)

    before = {n["id"]: n for n in eng.memory_graph(uid)["nodes"]
              if n["status"] == "active"}
    report = eng.sleep_cycle(uid)

    after = {n["id"]: n for n in eng.memory_graph(uid)["nodes"]}
    active = [n for n in after.values() if n["status"] == "active"]

    # the theme cluster consolidated into one memory
    assert report["consolidated"], "same-theme fragments did not consolidate"
    merged_sources = {m["id"] for c in report["consolidated"] for m in c["merged"]}

    # purity: unrelated facts never get pulled into a cluster
    for n in before.values():
        low = n["content"].lower()
        if "peanut" in low or "acme" in low:
            assert n["id"] not in merged_sources, (
                "unrelated fact consolidated away: %s" % n["content"])
            assert after[n["id"]]["status"] == "active", (
                "unrelated fact lost active status: %s" % n["content"])

    # merged summary holds the theme, store got denser
    summary = " ".join(c["content"].lower() for c in report["consolidated"])
    assert "tokyo" in summary, "merged memory lost its theme"
    assert len(active) < len(before), "store did not get denser"

    print("SLEEP CLUSTER PURITY: all assertions passed "
          "(%d -> %d active, %d merged)" % (
              len(before), len(active), len(merged_sources)))


if __name__ == "__main__":
    main()
