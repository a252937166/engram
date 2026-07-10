"""Session ownership + pagination, tested over real HTTP.

Boots the actual server (offline fake Qwen, throwaway DB, random port) and
verifies the README's isolation claim at the API boundary:

- another user cannot read your session's messages
- another user cannot chat into your session
- another user cannot read your turn audits
- message pagination walks backwards correctly with before_id

    ENGRAM_FAKE_QWEN=1 python3 tests/test_session_isolation.py
"""

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.error

# Talk to 127.0.0.1 directly - never through a configured system proxy.
urllib.request.install_opener(
    urllib.request.build_opener(urllib.request.ProxyHandler({})))

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _req(method, url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read().decode() or "{}")


def _chat(base, user, session, text):
    """Drive /api/chat and drain the SSE stream; return the done payload."""
    req = urllib.request.Request(
        base + "/api/chat", method="POST",
        data=json.dumps({"user_id": user, "session_id": session,
                         "message": text}).encode(),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode()
    except urllib.error.HTTPError as err:
        return err.code, None
    done = None
    for chunk in raw.split("\n\n"):
        if "event: done" in chunk:
            done = json.loads(chunk.split("data: ", 1)[1])
    return 200, done


def main():
    os.environ["ENGRAM_FAKE_QWEN"] = "1"
    port = _free_port()
    env = dict(os.environ,
               ENGRAM_PORT=str(port),
               ENGRAM_DB=tempfile.mktemp(suffix=".db"),
               ENGRAM_SEED_USER="")
    proc = subprocess.Popen(
        [sys.executable, os.path.join(ROOT, "backend", "server.py")],
        env=env, stderr=subprocess.DEVNULL)
    base = "http://127.0.0.1:%d" % port
    try:
        for _ in range(50):
            try:
                _req("GET", base + "/api/health")
                break
            except OSError:
                time.sleep(0.1)

        alice, mallory = "alice-iso-01", "mallory-iso-01"
        code, s = _req("POST", base + "/api/sessions",
                       {"user_id": alice, "title": "private"})
        assert code == 200
        sid = s["id"]

        # Alice talks; audits + messages exist
        code, done = _chat(base, alice, sid, "I am vegetarian and I work at Acme.")
        assert code == 200 and done and done.get("message_id"), "chat failed"
        msg_id = done["message_id"]

        # 1) owner reads fine
        code, page = _req("GET", base + "/api/messages?user_id=%s&session_id=%s"
                          % (alice, sid))
        assert code == 200 and len(page["messages"]) == 2

        # 2) another user is walled off (messages / chat / audit)
        code, _ = _req("GET", base + "/api/messages?user_id=%s&session_id=%s"
                       % (mallory, sid))
        assert code == 404, "cross-user message read must 404, got %s" % code
        code, _ = _chat(base, mallory, sid, "hijack attempt")
        assert code == 404, "cross-user chat must 404, got %s" % code
        code, _ = _req("GET", base + "/api/turn_audit?user_id=%s&message_id=%s"
                       % (mallory, msg_id))
        assert code == 404, "cross-user audit read must 404, got %s" % code

        # owner audit works and is complete
        code, audit = _req("GET", base + "/api/turn_audit?user_id=%s&message_id=%s"
                           % (alice, msg_id))
        assert code == 200 and "selected" in audit and "memory_context" in audit

        # 3) pagination: 6 more turns, then walk a 3-message window backwards
        for i in range(6):
            _chat(base, alice, sid, "small talk number %d about nothing" % i)
        code, p1 = _req("GET", base + "/api/messages?user_id=%s&session_id=%s&limit=3"
                        % (alice, sid))
        assert code == 200 and len(p1["messages"]) == 3 and p1["has_more"]
        code, p2 = _req("GET", base +
                        "/api/messages?user_id=%s&session_id=%s&limit=3&before_id=%s"
                        % (alice, sid, p1["next_cursor"]))
        assert code == 200 and len(p2["messages"]) == 3
        assert p2["messages"][-1]["id"] < p1["messages"][0]["id"], "pages overlap"
        ids = [m["id"] for m in p2["messages"] + p1["messages"]]
        assert ids == sorted(ids), "pages must stitch oldest-first"

        print("SESSION ISOLATION: all assertions passed")
    finally:
        proc.terminate()


if __name__ == "__main__":
    main()
