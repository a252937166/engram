"""Seed the DevOps vertical demo user (seed-devops-2026).

Run ON the production host (needs QWEN_API_KEY in env, e.g.
`set -a; . /etc/engram/engram.env; set +a`), pointing at the live DB:

    ENGRAM_DB=/opt/engram/backend/engram.db python3 deploy/seed_devops.py

Builds an ops-agent memory: hard prohibitions, incident post-mortems, a
superseded driver fact, and an ALB-migration cluster that the sleep cycle
consolidates. Fresh visitors on /?seed=devops get a clone of this user.
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "backend"))

from engine import MemoryEngine  # noqa: E402

UID = os.environ.get("ENGRAM_SEED_DEVOPS", "seed-devops-2026")

TURNS = [
    "Hard rule from the payments team: never restart the billing pods "
    "directly. Always drain traffic at the gateway first, then roll them "
    "one by one. This is a compliance requirement, treat it as critical.",

    "Our production cluster is Kubernetes 1.27 on Alibaba Cloud ACK, "
    "3 worker nodes in cn-beijing.",

    "The checkout service depends on redis-cart. When redis-cart degrades, "
    "checkout p99 latency is always the first symptom.",

    "Incident 2026-06-12: payments-worker was stuck in an OOMKilled loop. "
    "Root cause was the 512Mi memory limit; we fixed it by raising the "
    "limit to 1Gi and adding a JVM heap cap.",

    "Incident 2026-06-20: cert-manager TLS renewal was stuck pending. "
    "Deleting the stale challenge pod unblocked the renewal.",

    "Our deploy window is Tuesday and Thursday 14:00-17:00 CST. "
    "Friday is a hard deploy freeze.",

    "GPU inference nodes run NVIDIA driver 535 with CUDA 12.2.",

    "Alert routing preference: page #oncall-infra on Slack. Never use "
    "email for alerts.",

    "The Grafana dashboard 'svc-gold' is our source of truth for SLOs.",

    # supersede: driver upgrade replaces the 535 fact
    "Update: we upgraded the GPU inference nodes to NVIDIA driver 550 "
    "with CUDA 12.4 last week. The old 535 setup is gone.",

    # three fragments for the sleep cycle to consolidate
    "We started migrating ingress from the nginx controller to Alibaba "
    "Cloud ALB.",
    "ALB migration note: canary is at 10% of traffic since June 28.",
    "ALB migration finished on July 1; the old nginx ingress is removed.",
]

RECALL_PROBES = [
    "billing pods are unhealthy, what should I do?",
    "checkout latency is spiking, where do I look first?",
    "can I deploy on Friday?",
    "which GPU driver are we on?",
    "how do we get alerted during an incident?",
]


def main():
    eng = MemoryEngine(os.environ.get("ENGRAM_DB", os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "backend",
        "engram.db")))
    sid = eng.create_session(UID, "DevOps runbook intake")
    for i, text in enumerate(TURNS, 1):
        ops = eng.extract_and_remember(UID, sid, text)
        print("[%2d/%d] %s" % (i, len(TURNS),
                               [(o["op"], o.get("type", "")) for o in ops]))
        time.sleep(0.4)

    print("sleep:", eng.sleep_cycle(UID))

    # reinforce the memories a judge is most likely to probe
    for q in RECALL_PROBES:
        got = eng.retrieve(UID, q)
        print("probe %-55r -> %d recalled" % (q[:52], len(got)))

    stats = eng.stats(UID)
    print("DONE:", stats)


if __name__ == "__main__":
    main()
