"""Belief revision end-to-end at the engine layer (offline fake Qwen).

The Track-1 core claim, asserted against the store itself:

    "I work at Acme"  +  "I left Acme - now at Nova"
      -> op == updated
      -> Acme row: status=superseded, superseded_by=Nova row
      -> exactly ONE active employer fact
      -> retrieval never returns the stale fact

    ENGRAM_FAKE_QWEN=1 python3 tests/test_belief_revision.py
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
    uid = "belief-rev-01"

    eng.extract_and_remember(
        uid, "s1", "I work as a platform engineer at Acme Robotics.")
    ops = eng.extract_and_remember(
        uid, "s1", "Update: I left Acme Robotics last week — I now work at "
                   "Nova Robotics.")

    kinds = [o["op"] for o in ops]
    assert "updated" in kinds, "employer change must fire an update, got %s" % kinds
    upd = next(o for o in ops if o["op"] == "updated")

    nodes = {n["id"]: n for n in eng.memory_graph(uid)["nodes"]}
    old = nodes[upd["superseded"]["id"]]
    new = nodes[upd["id"]]
    assert old["status"] == "superseded", "old belief must be superseded"
    assert new["status"] == "active", "new belief must be active"
    assert old.get("superseded_by") == new["id"], "audit link must point at successor"

    employer_active = [n for n in nodes.values() if n["status"] == "active"
                       and ("acme" in n["content"].lower()
                            or "nova" in n["content"].lower())]
    assert len(employer_active) == 1, (
        "exactly one active employer fact, got %d" % len(employer_active))

    rec = eng.retrieve(uid, "Where do I work right now and in what role?")
    stale = [m for m in rec if "acme" in m["content"].lower()
             and "left" not in m["content"].lower()
             and "nova" not in m["content"].lower()]
    assert not stale, "stale employer leaked into retrieval: %s" % stale

    print("BELIEF REVISION: all assertions passed")


if __name__ == "__main__":
    main()
