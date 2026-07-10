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


def transitivity_bridge():
    """Same-type A~B~C chain must NOT merge the unrelated ends.

    Hand-crafted embeddings pin the geometry exactly:
        cos(A,B) = 0.80   cos(B,C) = 0.60   cos(A,C) = 0.00
    Union-find alone would chain all three through the bridge B; the
    complete-link purity guard has to keep C (or A) out of the merge.
    """
    import math
    import time
    import engine as eng_mod
    from engine import MemoryEngine, _pack

    eng = MemoryEngine(tempfile.mktemp(suffix=".db"))
    uid = "bridge-01"
    thr = eng_mod.CLUSTER_SIM
    a, b = math.sqrt(1 - thr ** 2), thr  # cos(A,B)=cos(B,C)=thr+eps > thr
    dim = 8
    vecs = {
        "A": [1, 0, 0] + [0] * (dim - 3),
        "B": [b + 0.05, a - 0.05, 0] + [0] * (dim - 3),
        "C": [0, 1, 0] + [0] * (dim - 3),
        "D": [0.9, 0.43, 0] + [0] * (dim - 3),   # close to both A and B
    }
    now = time.time()
    with eng._lock:
        for name, v in vecs.items():
            n = math.sqrt(sum(x * x for x in v))
            v = [x / n for x in v]
            eng._db.execute(
                "INSERT INTO memories(id, user_id, type, content, embedding, "
                "importance, strength, created_at, last_accessed, "
                "source_session) VALUES(?,?,?,?,?,?,?,?,?,?)",
                ("bridge-mem-" + name.lower(), uid, "semantic",
                 "Synthetic fact %s about one theme" % name, _pack(v),
                 0.5, 1.0, now - 90 * 86400, now, "s1"),
            )
        eng._db.commit()

    report = eng.sleep_cycle(uid)
    merged = {m["id"] for c in report["consolidated"] for m in c["merged"]}
    in_a, in_c = "bridge-mem-a" in merged, "bridge-mem-c" in merged
    assert not (in_a and in_c), (
        "transitive bridge merged unrelated A and C into one cluster")
    nodes = {n["id"]: n for n in eng.memory_graph(uid)["nodes"]}
    dropped = "bridge-mem-a" if in_c else "bridge-mem-c"
    assert nodes[dropped]["status"] == "active", (
        "the excluded end of the chain must stay active")
    print("TRANSITIVITY BRIDGE: guard held (merged=%s)" % sorted(merged))


if __name__ == "__main__":
    main()
    transitivity_bridge()
