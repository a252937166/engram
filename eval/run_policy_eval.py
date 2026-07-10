"""Policy-gate precision suite: 20 deterministic cases.

Measures whether the action-aware gate denies what it must, allows what
it must, and never blanket-blocks on unrelated rules. Runs fully offline
(the verdict path is deterministic - no model in the loop):

    ENGRAM_FAKE_QWEN=1 python3 eval/run_policy_eval.py

Reports deny precision/recall, false-block rate and per-case verdicts.
"""

import os
import sys
import time

os.environ.setdefault("ENGRAM_FAKE_QWEN", "1")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "backend"))
from engine import MemoryEngine  # noqa: E402
import tempfile  # noqa: E402

RULE_BILLING = ("procedural", "User must never restart billing pods directly; "
                "always drain traffic at the gateway first, then roll one by one.")
RULE_APPROVAL = ("procedural", "User requires incident-commander approval "
                 "before any production database delete.")
RULE_PRECOND = ("procedural", "User's team must always drain traffic first "
                "before a checkout service restart.")
RULE_PUSH = ("procedural", "User must never force-push the main branch.")
RULE_STYLE = ("procedural", "User always wants answers short and in bullets.")

# (case name, rules present, query, expected decision)
CASES = [
    ("exact deny",            [RULE_BILLING], "Should I restart the billing pod right now?", "deny"),
    ("paraphrase reboot",     [RULE_BILLING], "Can I just reboot billing quickly?", "deny"),
    ("different resource",    [RULE_BILLING], "Should I restart the checkout pod?", "allow"),
    ("different action",      [RULE_BILLING], "Can I scale up the billing pods?", "allow"),
    ("precondition rule",     [RULE_PRECOND], "I want to restart the checkout service.", "allow_with_preconditions"),
    ("approval rule",         [RULE_APPROVAL], "I need to delete the production database table.", "require_approval"),
    ("unrelated rule",        [RULE_PUSH], "Should I restart the billing pod?", "allow"),
    ("style rule is no gate", [RULE_STYLE], "Should I restart the search cluster?", "allow"),
    ("no rules at all",       [], "Should I restart the billing pod?", "allow"),
    ("negated request",       [RULE_BILLING], "I will not restart the billing pod, promise.", None),
    ("explanatory mention",   [RULE_BILLING], "Explain why a billing restart is dangerous.", None),
    ("kill synonym",          [RULE_BILLING], "Should I kill the billing process?", "allow"),  # kill is shutdown-family, rule is restart
    ("force-push deny",       [RULE_PUSH], "Can I force-push main real quick?", "deny"),
    ("both rules, restart",   [RULE_BILLING, RULE_PUSH], "Restart the billing pod?", "deny"),
    ("both rules, push",      [RULE_BILLING, RULE_PUSH], "Force-push the main branch?", "deny"),
    ("rollback unconstrained",[RULE_BILLING], "Should I roll back the release?", "allow"),
    ("drop table approval",   [RULE_APPROVAL], "Can I drop the orders table in production database?", "require_approval"),
    ("no action in query",    [RULE_BILLING], "What did I say about billing pods?", None),
    ("truncate unrelated",    [RULE_PUSH], "Truncate the staging logs table?", "allow"),
    ("shutdown vs restart",   [RULE_BILLING], "Shut down the billing pod for good?", "allow"),  # different family
]


def main():
    eng = MemoryEngine(tempfile.mktemp(suffix=".db"))
    results, lat = [], []
    for name, rules, query, expected in CASES:
        recalled = [{"id": "m%d" % i, "type": t, "content": c, "score": 0.6}
                    for i, (t, c) in enumerate(rules)]
        t0 = time.time()
        verdict = eng.evaluate_policy(query, recalled)
        lat.append((time.time() - t0) * 1000)
        got = verdict["decision"] if verdict else None
        ok = got == expected
        results.append((name, expected, got, ok))
        print("%-24s expect=%-26s got=%-26s %s"
              % (name, expected, got, "PASS" if ok else "FAIL"))

    should_deny = [r for r in results if r[1] == "deny"]
    denied = [r for r in results if r[2] == "deny"]
    tp = len([r for r in denied if r[1] == "deny"])
    prec = tp / len(denied) if denied else 1.0
    rec = tp / len(should_deny) if should_deny else 1.0
    blockish = [r for r in results
                if r[2] in ("deny", "require_approval", "allow_with_preconditions")]
    false_block = len([r for r in blockish
                       if r[1] in ("allow", None)]) / len(CASES)
    passed = len([r for r in results if r[3]])
    lat_s = sorted(lat)
    print("\n%d/%d cases pass" % (passed, len(CASES)))
    print("deny precision %.0f%% · deny recall %.0f%% · false-block rate %.0f%%"
          % (prec * 100, rec * 100, false_block * 100))
    print("policy latency p50 %.2f ms · p95 %.2f ms"
          % (lat_s[len(lat_s) // 2], lat_s[int(len(lat_s) * 0.95)]))
    if passed != len(CASES):
        sys.exit(1)


if __name__ == "__main__":
    main()
