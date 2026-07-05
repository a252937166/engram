"""Offline smoke test - no network, no API key.

Runs the full memory pipeline (extract -> embed -> store -> retrieve ->
sleep) against the deterministic fake Qwen client (ENGRAM_FAKE_QWEN=1).
Validates plumbing and storage semantics; model quality is covered by the
real suite in eval/run_eval.py.

    ENGRAM_FAKE_QWEN=1 python3 tests/smoke_offline.py
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
    uid = "smoke-user-01"

    # extract + store
    ops = eng.extract_and_remember(
        uid, "s1",
        "Hi! I'm Dora, I'm vegetarian and severely allergic to peanuts. "
        "I work as an engineer at Acme.")
    assert any(o["op"] == "created" for o in ops), "extraction stored nothing"

    # dedupe: repeating the same facts must reinforce, not duplicate
    before = len([n for n in eng.memory_graph(uid)["nodes"]
                  if n["status"] == "active"])
    eng.extract_and_remember(
        uid, "s1",
        "As I said, I'm vegetarian and severely allergic to peanuts. "
        "I work as an engineer at Acme.")
    after_nodes = [n for n in eng.memory_graph(uid)["nodes"]
                   if n["status"] == "active"]
    assert len(after_nodes) <= before + 1, "dedupe failed, store ballooned"

    # retrieval returns scored, budgeted memories with components
    rec = eng.retrieve(uid, "what should I cook for dinner, any allergies?")
    assert rec, "retrieval returned nothing"
    assert all("components" in m and "score" in m for m in rec)
    assert sum(m["tokens"] for m in rec) <= 800, "token budget violated"

    # explicit forget works and is reflected in the graph
    victim = rec[0]["id"]
    eng.forget(uid, victim)
    assert all(n["id"] != victim or n["status"] == "forgotten"
               for n in eng.memory_graph(uid)["nodes"])

    # sleep cycle runs end-to-end
    report = eng.sleep_cycle(uid)
    assert "consolidated" in report and "forgotten" in report

    # seed cloning gives a fresh user the donor's graph
    eng2_uid = "smoke-user-02"
    copied = eng.clone_seed(eng2_uid, uid)
    assert copied > 0 and eng.has_memories(eng2_uid)

    print("OFFLINE SMOKE: all assertions passed")


if __name__ == "__main__":
    main()
