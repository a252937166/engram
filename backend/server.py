"""ENGRAM API server.

Zero-dependency HTTP + SSE server (Python 3.6 stdlib) that fronts the memory
engine. nginx terminates TLS and serves the static frontend; this process
only binds 127.0.0.1 and speaks JSON + Server-Sent Events.

Endpoints
  GET  /api/health                      liveness + model info
  GET  /api/bootstrap?user_id=..        sessions + memory graph + stats
  POST /api/sessions                    {user_id, title} -> {id}
  GET  /api/messages?user_id=..&session_id=..[&before_id=..&limit=..]
                                        one page of history (cursor pagination);
                                        session ownership enforced
  GET  /api/turn_audit?user_id=..&message_id=..
                                        frozen memory decision of one turn
  POST /api/chat                        {user_id, session_id, message} -> SSE
  GET  /api/memories?user_id=..         memory graph (nodes + links)
  POST /api/sleep                       {user_id} -> consolidation report
  POST /api/forget                      {user_id, id}
  GET  /api/stats?user_id=..
"""

import json
import os
import re
import socket
import socketserver
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import qwen_client
from engine import MemoryEngine

PORT = int(os.environ.get("ENGRAM_PORT", "8788"))
DB_PATH = os.environ.get("ENGRAM_DB", os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "engram.db"))
DAILY_CHAT_LIMIT = int(os.environ.get("ENGRAM_DAILY_LIMIT", "400"))
SEED_USER = os.environ.get("ENGRAM_SEED_USER", "")
SEED_USERS = {
    "default": SEED_USER,
    "devops": os.environ.get("ENGRAM_SEED_DEVOPS", "seed-devops-2026"),
}
FRONTEND_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")

USER_RE = re.compile(r"^[A-Za-z0-9_-]{6,40}$")
ID_RE = re.compile(r"^[a-f0-9]{12}$")

ENGINE = MemoryEngine(DB_PATH)

SYSTEM_PROMPT = """You are ENGRAM, an assistant with a persistent long-term memory.

Below is your MEMORY: durable facts recalled from past interactions with this user,
possibly from previous sessions. Treat them as your own remembered knowledge:
- Personalize every answer with relevant memories (diet, style, projects, plans...).
- Never contradict a memory unless the user's current message updates it.
- If the user updates a fact, follow the newest statement.
- Do not recite the memory list or mention "my memory says" unless asked what
  you remember. Just *know* it.
- Be concise, warm and concrete.

MEMORY:
{memory_block}"""

# --------------------------------------------------------------- rate limit
_RL_LOCK = threading.Lock()
_RL = {}


def _rate_limited(ip, limit=12, window=60.0):
    now = time.time()
    with _RL_LOCK:
        hits = [t for t in _RL.get(ip, []) if now - t < window]
        if len(hits) >= limit:
            _RL[ip] = hits
            return True
        hits.append(now)
        _RL[ip] = hits
        if len(_RL) > 2000:
            _RL.clear()
        return False


class ThreadingHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "engram/1.0"

    # ------------------------------------------------------------ helpers
    def _client_ip(self):
        fwd = self.headers.get("X-Forwarded-For")
        return fwd.split(",")[0].strip() if fwd else self.client_address[0]

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 120000:
            return None
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None

    def _sse_start(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()

    def _sse(self, event, data):
        payload = "event: {}\ndata: {}\n\n".format(
            event, json.dumps(data, ensure_ascii=False))
        self.wfile.write(payload.encode("utf-8"))
        self.wfile.flush()

    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), fmt % args))

    # ------------------------------------------------------------- routes
    def do_GET(self):
        parsed = urlparse(self.path)
        route = parsed.path
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        try:
            if route == "/api/health":
                return self._json(200, {
                    "ok": True,
                    "engine": "engram/1.0",
                    "chat_model": qwen_client.CHAT_MODEL,
                    "fast_model": qwen_client.FAST_MODEL,
                    "embed_model": qwen_client.EMBED_MODEL,
                    "provider": "Qwen Cloud (Alibaba Cloud Model Studio)",
                })
            user_id = query.get("user_id", "")
            if route == "/api/bootstrap":
                if not USER_RE.match(user_id):
                    return self._json(400, {"error": "bad user_id"})
                seed_uid = SEED_USERS.get(query.get("seed", "default")) \
                    or SEED_USER
                if seed_uid and not ENGINE.has_memories(user_id):
                    ENGINE.clone_seed(user_id, seed_uid)
                return self._json(200, {
                    "sessions": ENGINE.list_sessions(user_id),
                    "graph": ENGINE.memory_graph(user_id),
                    "stats": ENGINE.stats(user_id),
                    "chat_model": qwen_client.CHAT_MODEL,
                    "daily_used": ENGINE.usage_today(),
                    "daily_limit": DAILY_CHAT_LIMIT,
                })
            if route == "/api/memories":
                if not USER_RE.match(user_id):
                    return self._json(400, {"error": "bad user_id"})
                return self._json(200, ENGINE.memory_graph(user_id))
            if route == "/api/stats":
                if not USER_RE.match(user_id):
                    return self._json(400, {"error": "bad user_id"})
                return self._json(200, ENGINE.stats(user_id))
            if route == "/api/messages":
                sid = query.get("session_id", "")
                if not USER_RE.match(user_id):
                    return self._json(400, {"error": "bad user_id"})
                if not ID_RE.match(sid):
                    return self._json(400, {"error": "bad session_id"})
                if not ENGINE.session_belongs_to(user_id, sid):
                    return self._json(404, {"error": "not found"})
                try:
                    before_id = int(query["before_id"]) if "before_id" in query else None
                    limit = int(query.get("limit", 50))
                except ValueError:
                    return self._json(400, {"error": "bad cursor"})
                return self._json(200, ENGINE.session_messages(
                    user_id, sid, before_id=before_id, limit=limit))
            if route == "/api/turn_audit":
                if not USER_RE.match(user_id):
                    return self._json(400, {"error": "bad user_id"})
                try:
                    mid = int(query.get("message_id", ""))
                except ValueError:
                    return self._json(400, {"error": "bad message_id"})
                audit = ENGINE.turn_audit(user_id, mid)
                if not audit:
                    return self._json(404, {"error": "not found"})
                return self._json(200, audit)
            return self._serve_static(route)
        except Exception as err:  # noqa: broad, boundary of the process
            self.log_message("GET %s failed: %r", route, err)
            try:
                return self._json(500, {"error": "internal error"})
            except OSError:
                return None

    def do_POST(self):
        route = urlparse(self.path).path
        try:
            if route == "/api/chat":
                return self._handle_chat()
            body = self._read_body() or {}
            user_id = str(body.get("user_id", ""))
            if not USER_RE.match(user_id):
                return self._json(400, {"error": "bad user_id"})
            if route == "/api/sessions":
                title = str(body.get("title", "Session"))[:60]
                sid = ENGINE.create_session(user_id, title)
                return self._json(200, {"id": sid})
            if route == "/api/sleep":
                if _rate_limited(self._client_ip() + ":sleep", limit=4):
                    return self._json(429, {"error": "slow down"})
                report = ENGINE.sleep_cycle(user_id)
                return self._json(200, report)
            if route == "/api/forget":
                mid = str(body.get("id", ""))
                if not ID_RE.match(mid):
                    return self._json(400, {"error": "bad id"})
                ENGINE.forget(user_id, mid)
                return self._json(200, {"ok": True})
            return self._json(404, {"error": "not found"})
        except Exception as err:  # noqa
            self.log_message("POST %s failed: %r", route, err)
            try:
                return self._json(500, {"error": "internal error"})
            except OSError:
                return None

    # --------------------------------------------------------------- chat
    def _handle_chat(self):
        body = self._read_body() or {}
        user_id = str(body.get("user_id", ""))
        session_id = str(body.get("session_id", ""))
        message = str(body.get("message", "")).strip()[:4000]
        if not USER_RE.match(user_id) or not ID_RE.match(session_id) or not message:
            return self._json(400, {"error": "bad request"})
        if not ENGINE.session_belongs_to(user_id, session_id):
            return self._json(404, {"error": "not found"})
        ip = self._client_ip()
        # offline fake mode exists for tests/CI - don't rate-limit ourselves
        if _rate_limited(ip, limit=1000 if qwen_client.FAKE else 12):
            return self._json(429, {"error": "rate limit: wait a minute"})
        if ENGINE.usage_today() >= DAILY_CHAT_LIMIT:
            return self._json(429, {"error": "daily demo quota reached"})

        self._sse_start()
        t0 = time.time()
        try:
            # 1) recall
            ret = ENGINE.retrieve(user_id, message, with_rejected=True)
            recalled = ret["picked"]
            self._sse("retrieval", {
                "memories": recalled,
                "rejected": ret["rejected"],
                "budget_tokens": ret["budget_tokens"],
                "spent_tokens": ret["spent_tokens"],
                "query": message[:200],
                "elapsed_ms": int((time.time() - t0) * 1000),
            })

            # 2) short-term window: only the current session's recent turns.
            #    Long-term knowledge arrives through ENGRAM's memory block,
            #    which is what keeps the context small and cross-session.
            history = ENGINE.session_messages(
                user_id, session_id, limit=8)["messages"]
            memory_block = "\n".join(
                "- [{}] {}".format(m["type"], m["content"]) for m in recalled
            ) or "(no relevant memories yet)"
            messages = [{"role": "system",
                         "content": SYSTEM_PROMPT.format(memory_block=memory_block)}]
            for h in history:
                messages.append({"role": h["role"], "content": h["content"][:2000]})
            messages.append({"role": "user", "content": message})

            ENGINE.log_message(session_id, user_id, "user", message)

            # 3) stream the answer
            answer_parts = []
            usage = {}
            for kind, payload in qwen_client.chat_stream(messages):
                if kind == "delta":
                    answer_parts.append(payload)
                    self._sse("delta", {"text": payload})
                else:
                    usage = payload
            answer = "".join(answer_parts)
            answer_msg_id = ENGINE.log_message(
                session_id, user_id, "assistant", answer,
                [m["id"] for m in recalled])
            # the turn succeeded and is persisted - now recall counts as use
            ENGINE.commit_recall_usage(user_id, [m["id"] for m in recalled])

            # 4) memory formation (visible phase in the UI)
            self._sse("phase", {"phase": "memorizing"})
            ops = ENGINE.extract_and_remember(user_id, session_id, message)
            self._sse("memory_ops", {"ops": ops})

            # 5) freeze this turn's memory decision for the audit log
            elapsed_ms = int((time.time() - t0) * 1000)
            ENGINE.log_turn_audit(
                answer_msg_id, user_id, session_id, message, ret,
                memory_block, ops, qwen_client.CHAT_MODEL, usage, elapsed_ms)

            ENGINE.count_chat(usage.get("total_tokens", 0))
            self._sse("done", {
                "usage": usage,
                "recalled": len(recalled),
                "message_id": answer_msg_id,
                "audit": {"recalled": len(recalled),
                          "spent": ret["spent_tokens"],
                          "budget": ret["budget_tokens"],
                          "ops": len(ops)},
                "elapsed_ms": elapsed_ms,
                "model": qwen_client.CHAT_MODEL,
            })
        except qwen_client.QwenError as err:
            try:
                self._sse("error", {"message": str(err)[:300]})
            except OSError:
                pass
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as err:  # noqa
            self.log_message("chat failed: %r", err)
            try:
                self._sse("error", {"message": "internal error"})
            except OSError:
                pass

    # ------------------------------------------------------------- static
    def _serve_static(self, route):
        """Convenience for local development; nginx does this in production."""
        if route == "/":
            route = "/index.html"
        safe = os.path.normpath(route).lstrip("/")
        path = os.path.join(FRONTEND_DIR, safe)
        if not path.startswith(FRONTEND_DIR) or not os.path.isfile(path):
            return self._json(404, {"error": "not found"})
        ctype = "text/html; charset=utf-8"
        if path.endswith(".js"):
            ctype = "text/javascript"
        elif path.endswith(".css"):
            ctype = "text/css"
        elif path.endswith(".svg"):
            ctype = "image/svg+xml"
        elif path.endswith(".png"):
            ctype = "image/png"
        with open(path, "rb") as fh:
            body = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    server.socket.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    sys.stderr.write("ENGRAM listening on 127.0.0.1:%d (db=%s)\n" % (PORT, DB_PATH))
    server.serve_forever()


if __name__ == "__main__":
    main()
