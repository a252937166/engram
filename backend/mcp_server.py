"""ENGRAM as an MCP server.

Exposes the memory engine over the Model Context Protocol (stdio transport,
newline-delimited JSON-RPC 2.0), so ANY MCP-capable agent - Claude Code,
Qwen agents, custom orchestrators - can mount ENGRAM as its long-term memory.

    {"mcpServers": {"engram": {
        "command": "python3",
        "args": ["backend/mcp_server.py"],
        "env": {"QWEN_API_KEY": "sk-..."}}}}

Tools: engram_remember, engram_recall, engram_forget, engram_sleep, engram_stats
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engine import MemoryEngine

DB_PATH = os.environ.get("ENGRAM_DB", os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "engram.db"))
ENGINE = MemoryEngine(DB_PATH)

PROTOCOL_VERSION = "2024-11-05"

TOOLS = [
    {
        "name": "engram_remember",
        "description": "Store a durable memory about the user. ENGRAM will "
                       "deduplicate it, reinforce an existing trace, or "
                       "supersede a contradicted belief automatically.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "Stable user handle (6-40 chars)"},
                "content": {"type": "string", "description": "Third-person fact, e.g. 'User is allergic to peanuts'"},
                "type": {"type": "string", "enum": ["preference", "semantic", "procedural", "episodic"]},
                "importance": {"type": "number", "minimum": 0.1, "maximum": 1.0},
            },
            "required": ["user_id", "content"],
        },
    },
    {
        "name": "engram_recall",
        "description": "Retrieve the most relevant memories for a query using "
                       "hybrid scoring (semantic x recency x importance x "
                       "reinforcement) under a token budget.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string"},
                "query": {"type": "string"},
                "budget_tokens": {"type": "integer", "default": 800},
            },
            "required": ["user_id", "query"],
        },
    },
    {
        "name": "engram_forget",
        "description": "Explicitly forget one memory by id.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string"},
                "memory_id": {"type": "string"},
            },
            "required": ["user_id", "memory_id"],
        },
    },
    {
        "name": "engram_sleep",
        "description": "Run a sleep cycle: consolidate similar memories into "
                       "dense semantic knowledge and forget low-retention traces.",
        "inputSchema": {
            "type": "object",
            "properties": {"user_id": {"type": "string"}},
            "required": ["user_id"],
        },
    },
    {
        "name": "engram_stats",
        "description": "Memory store statistics for a user.",
        "inputSchema": {
            "type": "object",
            "properties": {"user_id": {"type": "string"}},
            "required": ["user_id"],
        },
    },
]


def _text_result(obj):
    return {"content": [{"type": "text",
                         "text": json.dumps(obj, ensure_ascii=False, indent=2)}]}


def _call_tool(name, args):
    user_id = str(args.get("user_id", ""))[:40]
    if len(user_id) < 6:
        return _text_result({"error": "user_id must be 6-40 chars"})
    if name == "engram_remember":
        op = ENGINE._remember_one(  # noqa: intentional internal reuse
            user_id, "mcp",
            str(args.get("type", "semantic")),
            str(args.get("content", ""))[:200],
            float(args.get("importance", 0.6)),
        )
        return _text_result(op)
    if name == "engram_recall":
        memories = ENGINE.retrieve(
            user_id, str(args.get("query", "")),
            budget_tokens=int(args.get("budget_tokens", 800)))
        return _text_result({"memories": memories})
    if name == "engram_forget":
        ENGINE.forget(user_id, str(args.get("memory_id", "")))
        return _text_result({"ok": True})
    if name == "engram_sleep":
        return _text_result(ENGINE.sleep_cycle(user_id))
    if name == "engram_stats":
        return _text_result(ENGINE.stats(user_id))
    return _text_result({"error": "unknown tool " + name})


def _handle(msg):
    method = msg.get("method")
    if method == "initialize":
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "engram-memory", "version": "1.0.0"},
        }
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        params = msg.get("params") or {}
        try:
            return _call_tool(params.get("name", ""),
                              params.get("arguments") or {})
        except Exception as err:  # noqa: protocol boundary
            return {"content": [{"type": "text", "text": "error: %r" % err}],
                    "isError": True}
    if method == "ping":
        return {}
    return None


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if "id" not in msg:          # notification, nothing to answer
            continue
        result = _handle(msg)
        if result is None:
            reply = {"jsonrpc": "2.0", "id": msg["id"],
                     "error": {"code": -32601, "message": "method not found"}}
        else:
            reply = {"jsonrpc": "2.0", "id": msg["id"], "result": result}
        sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
