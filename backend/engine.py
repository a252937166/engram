"""ENGRAM memory engine.

The cognitive core: a biologically-inspired long-term memory for LLM agents.

Write path   extract() -> remember()   new facts are distilled from dialogue,
                                       deduplicated, or made to supersede
                                       contradicted beliefs.
Read path    retrieve()                hybrid scoring (semantic x recency x
                                       importance x reinforcement) under a
                                       strict context-token budget.
Maintenance  sleep_cycle()             consolidation (episodic fragments merge
                                       into semantic knowledge, like
                                       hippocampus -> cortex replay) and
                                       retention-based forgetting.

Storage is SQLite (WAL). Embeddings are 256-dim float32 blobs from Qwen
text-embedding-v4. All scoring is transparent: every retrieval returns its
score components so the UI can visualize *why* a memory was recalled.
"""

import json
import math
import re
import sqlite3
import threading
import time
import uuid
from array import array

import qwen_client

# ---------------------------------------------------------------- tuning ---
# Half-life (days) of the recency component, per memory type. Preferences
# should survive months of silence; episodic details should fade in weeks.
HALF_LIFE_DAYS = {
    "preference": 270.0,
    "semantic": 180.0,
    "procedural": 120.0,
    "episodic": 21.0,
}
WEIGHTS = {"semantic": 0.55, "recency": 0.18, "importance": 0.17, "usage": 0.10}

# Ablation switch for the evaluation suite (docs/evaluation.md):
#   ENGRAM_ABLATION=semantic_only  cosine-only scoring, no rescue floor
#   ENGRAM_ABLATION=no_arbiter     skip LLM arbitration (threshold-only memory)
#   ENGRAM_ABLATION=no_sleep       sleep_cycle becomes a no-op
import os as _os
ABLATION = _os.environ.get("ENGRAM_ABLATION", "")
SEMANTIC_FLOOR = 0.25       # below this similarity a memory is never recalled
CRITICAL_FLOOR = 0.15       # ...unless it is safety-critical (importance>=.85)
DUPLICATE_SIM = 0.90        # >= : same fact, reinforce instead of insert
CONFLICT_SIM = 0.45         # >= : related enough to consult the arbiter model
CLUSTER_SIM = 0.58          # >= : sleep-cycle consolidation clustering
                            # (256-d qwen-v4 space: same-topic pairs measure
                            #  ~0.50-0.75, unrelated facts < ~0.48)
RETENTION_FLOOR = 0.28      # below this a mature memory is forgotten
RETENTION_GRACE_DAYS = 7.0  # young memories are safe from forgetting
MAX_MEMORIES_PER_USER = 400

DAY = 86400.0


def _now():
    return time.time()


def _uuid():
    return uuid.uuid4().hex[:12]


def _pack(vec):
    return array("f", vec).tobytes()


def _unpack(blob):
    vec = array("f")
    vec.frombytes(blob)
    return vec


def _cosine(a, b):
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0 or nb <= 0:
        return 0.0
    return dot / math.sqrt(na * nb)


def _est_tokens(text):
    """Rough token estimate that behaves for both English and CJK."""
    ascii_chars = sum(1 for c in text if ord(c) < 128)
    return int(ascii_chars / 4 + (len(text) - ascii_chars) / 1.5) + 1


# ---------------------------------------------------------------- prompts ---
EXTRACT_PROMPT = """You are the memory-formation cortex of an AI assistant.
From the conversation turn below, extract durable long-term memories about the USER.

Extract only information worth remembering across future sessions:
- preference: tastes, constraints, style wishes (diet, tone, formats, likes/dislikes)
- semantic:   stable facts (name, job, projects, relationships, health, plans)
- procedural: standing instructions for how the assistant should behave
- episodic:   notable one-time events with lasting relevance

Rules:
- Each memory must be a standalone third-person sentence starting with "User", <= 140 chars.
- No small talk, no transient chit-chat, no questions, nothing about the assistant.
- If the user corrects or updates earlier information, extract the NEW fact.
- importance: 0.9+ safety-critical (allergies, medical), 0.7-0.8 identity/work/strong prefs,
  0.5-0.6 useful context, 0.3-0.4 minor. Return [] if nothing qualifies.

Respond with ONLY a JSON array:
[{"type":"preference|semantic|procedural|episodic","content":"User ...","importance":0.0}]"""

_UPDATE_SIGNAL = re.compile(
    r"\b(update[d]?|chang(?:e[ds]?|ing)|correction|instead of|no longer|"
    r"not\s+\w+\s+anymore|switch(?:ed)?\s+to|moved\s+to|now\s+(?:on|use[s]?|is|at))\b",
    re.I)

ARBITER_PROMPT = """You maintain a long-term memory store about one user.
A NEW memory has just been extracted:
NEW: {new}

EXISTING related memories:
{existing}

Decide how NEW relates to the EXISTING entries:
- duplicate_of: N   -> NEW carries the same information as entry N (nothing new)
- replaces: [N,...] -> NEW corrects, updates or outdates those entries
                       (changed job/city/status, reversed preference, new value
                       for the same attribute...)
- both empty        -> NEW is a genuinely distinct fact; keep everything

THE DECISIVE TEST: if NEW and an EXISTING entry describe the SAME attribute of
the user's life (a schedule, a version, a location, an owner, a tool...) but
with DIFFERENT values, that is an update -> replaces, never duplicate_of.
e.g. existing "deploy window is Tue/Thu 14:00" + new "deploy window is Mon/Wed
10:00" -> replaces. Only answer duplicate_of when the VALUES genuinely match.

Facts that can coexist (e.g. two different hobbies, a diet plus an allergy)
are NOT replacements.

Respond with ONLY this JSON: {{"duplicate_of": null_or_N, "replaces": [..]}}"""

CONSOLIDATE_PROMPT = """Merge these related memories about one user into ONE dense summary.
Keep every distinct fact, drop repetition. Third person, starting "User", <= 200 chars.
Memories:
{items}

Respond with ONLY the merged sentence."""


class MemoryEngine(object):
    def __init__(self, db_path):
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._lock = threading.RLock()
        self._init_schema()

    # ------------------------------------------------------------ schema ---
    def _init_schema(self):
        with self._lock:
            self._db.executescript(
                """
                CREATE TABLE IF NOT EXISTS memories(
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    embedding BLOB,
                    importance REAL DEFAULT 0.5,
                    strength REAL DEFAULT 1.0,
                    created_at REAL,
                    last_accessed REAL,
                    access_count INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'active',
                    source_session TEXT,
                    superseded_by TEXT,
                    consolidated_into TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_mem_user
                    ON memories(user_id, status);
                CREATE TABLE IF NOT EXISTS sessions(
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    title TEXT,
                    created_at REAL
                );
                CREATE TABLE IF NOT EXISTS messages(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at REAL,
                    recalled_ids TEXT
                );
                CREATE TABLE IF NOT EXISTS usage_log(
                    day TEXT PRIMARY KEY,
                    chats INTEGER DEFAULT 0,
                    tokens INTEGER DEFAULT 0
                );
                """
            )
            self._db.commit()

    # ------------------------------------------------------------- rows ----
    def _rows(self, sql, args=()):
        with self._lock:
            cur = self._db.execute(sql, args)
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    def _exec(self, sql, args=()):
        with self._lock:
            self._db.execute(sql, args)
            self._db.commit()

    def _active(self, user_id, with_vectors=True):
        rows = self._rows(
            "SELECT * FROM memories WHERE user_id=? AND status='active'",
            (user_id,),
        )
        if with_vectors:
            for row in rows:
                row["vec"] = _unpack(row["embedding"]) if row["embedding"] else None
        return rows

    # --------------------------------------------------------- retrieval ---
    def retrieve(self, user_id, query, budget_tokens=800, k_max=8):
        """Recall the most relevant memories for `query` under a token budget.

        Returns a list of memory dicts, each with a `score` and a
        `components` breakdown - full transparency for the UI.
        """
        memories = self._active(user_id)
        if not memories or not query.strip():
            return []
        qvec = qwen_client.embed([query])[0]
        now = _now()
        scored = []
        for m in memories:
            if m["vec"] is None:
                continue
            sim = _cosine(qvec, m["vec"])
            if ABLATION == "semantic_only":
                floor = SEMANTIC_FLOOR          # no safety rescue floor
            else:
                floor = CRITICAL_FLOOR if m["importance"] >= 0.85 else SEMANTIC_FLOOR
            if sim < floor:
                continue
            age_days = (now - (m["last_accessed"] or m["created_at"])) / DAY
            half_life = HALF_LIFE_DAYS.get(m["type"], 90.0)
            recency = math.exp(-age_days * math.log(2.0) / half_life)
            usage = min(m["strength"], 5.0) / 5.0
            comp = {
                "semantic": round(sim, 4),
                "recency": round(recency, 4),
                "importance": round(m["importance"], 4),
                "usage": round(usage, 4),
            }
            if ABLATION == "semantic_only":
                score = comp["semantic"]
            else:
                score = sum(WEIGHTS[k] * comp[k] for k in WEIGHTS)
            scored.append((score, comp, m))
        scored.sort(key=lambda t: -t[0])

        picked = []
        spent = 0
        for score, comp, m in scored[: k_max * 3]:
            cost = _est_tokens(m["content"])
            if spent + cost > budget_tokens or len(picked) >= k_max:
                break
            spent += cost
            picked.append(
                {
                    "id": m["id"],
                    "type": m["type"],
                    "content": m["content"],
                    "score": round(score, 4),
                    "components": comp,
                    # recalled below the normal floor => the critical-memory
                    # rescue kept it alive (safety-grade importance)
                    "rescued": comp["semantic"] < SEMANTIC_FLOOR,
                    "strength": m["strength"],
                    "tokens": cost,
                }
            )
        # Reinforcement: recalling a memory strengthens its trace.
        now = _now()
        for p in picked:
            self._exec(
                "UPDATE memories SET access_count=access_count+1, "
                "last_accessed=?, strength=MIN(strength+0.25, 8.0) WHERE id=?",
                (now, p["id"]),
            )
        return picked

    # ------------------------------------------------------------ writes ---
    def extract_and_remember(self, user_id, session_id, user_msg):
        """Distill durable memories out of a user turn and store them."""
        if len(user_msg.strip()) < 8:
            return []
        candidates = qwen_client.extract_json(
            [
                {"role": "system", "content": EXTRACT_PROMPT},
                {"role": "user", "content": user_msg[:2000]},
            ]
        )
        if not isinstance(candidates, list):
            return []
        # Explicit update phrasing in the raw turn ("update:", "changed",
        # "no longer", "now uses"...) means same-attribute collisions below
        # should supersede, even if the arbiter under-calls them as duplicates.
        update_hint = bool(_UPDATE_SIGNAL.search(user_msg))
        ops = []
        for cand in candidates[:6]:
            if not isinstance(cand, dict):
                continue
            content = str(cand.get("content", "")).strip()[:200]
            mtype = str(cand.get("type", "semantic"))
            if mtype not in HALF_LIFE_DAYS:
                mtype = "semantic"
            try:
                importance = max(0.1, min(1.0, float(cand.get("importance", 0.5))))
            except (TypeError, ValueError):
                importance = 0.5
            if len(content) < 8:
                continue
            op = self._remember_one(user_id, session_id, mtype, content,
                                    importance, update_hint=update_hint)
            if op:
                ops.append(op)
        return ops

    def _remember_one(self, user_id, session_id, mtype, content, importance,
                      update_hint=False):
        vec = qwen_client.embed([content])[0]
        neighbors = []
        for m in self._active(user_id):
            if m["vec"] is None:
                continue
            sim = _cosine(vec, m["vec"])
            if sim >= CONFLICT_SIM:
                neighbors.append((sim, m))
        neighbors.sort(key=lambda t: -t[0])

        # Trivially identical -> reinforce the existing trace, no LLM needed.
        if neighbors and neighbors[0][0] >= DUPLICATE_SIM:
            best_sim, best = neighbors[0]
            self._exec(
                "UPDATE memories SET strength=MIN(strength+1.0, 8.0), "
                "importance=MAX(importance, ?), last_accessed=? WHERE id=?",
                (importance, _now(), best["id"]),
            )
            return {"op": "reinforced", "id": best["id"], "content": best["content"],
                    "similarity": round(best_sim, 3)}

        # Semantically nearby memories: embeddings cannot tell "update" from
        # "compatible fact" (a diet and an allergy sit as close as a diet and
        # its reversal), so the arbiter model decides.
        if neighbors and ABLATION != "no_arbiter":
            verdict = self._arbitrate(content, [m for _s, m in neighbors[:4]])
            dup = verdict.get("duplicate_of")
            if dup is not None and update_hint:
                # The user explicitly said something changed. If the arbiter
                # still calls it a duplicate, supersede anyway: lossless when
                # the values truly match, a fix when it under-called an update.
                verdict["replaces"] = [dup] + [
                    m for m in verdict.get("replaces", []) if m is not dup]
                dup = None
            if dup is not None:
                self._exec(
                    "UPDATE memories SET strength=MIN(strength+1.0, 8.0), "
                    "last_accessed=? WHERE id=?",
                    (_now(), dup["id"]),
                )
                return {"op": "reinforced", "id": dup["id"],
                        "content": dup["content"], "similarity": None}
            if verdict.get("replaces"):
                new_id = self._insert(user_id, session_id, mtype, content,
                                      importance, vec)
                superseded = []
                for old in verdict["replaces"]:
                    self._exec(
                        "UPDATE memories SET status='superseded', superseded_by=? "
                        "WHERE id=?",
                        (new_id, old["id"]),
                    )
                    superseded.append({"id": old["id"], "content": old["content"]})
                return {"op": "updated", "id": new_id, "type": mtype,
                        "content": content, "superseded": superseded[0],
                        "superseded_all": superseded}

        new_id = self._insert(user_id, session_id, mtype, content, importance, vec)
        return {"op": "created", "id": new_id, "type": mtype, "content": content,
                "importance": importance}

    def _arbitrate(self, new_content, candidates):
        """Ask the fast model how a new memory relates to nearby existing ones.

        Returns {"duplicate_of": mem|None, "replaces": [mem, ...]}.
        Fails open to "distinct" - worst case we keep an extra memory.
        """
        listing = "\n".join(
            "%d. %s" % (i + 1, m["content"]) for i, m in enumerate(candidates)
        )
        result = None
        try:
            result = qwen_client.extract_json(
                [{"role": "user", "content": ARBITER_PROMPT.format(
                    new=new_content, existing=listing)}],
                max_tokens=80,
            )
        except qwen_client.QwenError:
            pass
        out = {"duplicate_of": None, "replaces": []}
        if not isinstance(result, dict):
            return out
        dup = result.get("duplicate_of")
        if isinstance(dup, (int, float)) and 1 <= int(dup) <= len(candidates):
            out["duplicate_of"] = candidates[int(dup) - 1]
        reps = result.get("replaces")
        if isinstance(reps, list):
            for n in reps:
                if isinstance(n, (int, float)) and 1 <= int(n) <= len(candidates):
                    mem = candidates[int(n) - 1]
                    if mem is not out["duplicate_of"]:
                        out["replaces"].append(mem)
        return out

    def _insert(self, user_id, session_id, mtype, content, importance, vec):
        mid = _uuid()
        now = _now()
        self._exec(
            "INSERT INTO memories(id, user_id, type, content, embedding, "
            "importance, strength, created_at, last_accessed, source_session) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (mid, user_id, mtype, content, _pack(vec), importance, 1.0,
             now, now, session_id),
        )
        self._enforce_capacity(user_id)
        return mid

    def _enforce_capacity(self, user_id):
        rows = self._rows(
            "SELECT COUNT(*) AS n FROM memories WHERE user_id=? AND status='active'",
            (user_id,),
        )
        overflow = rows[0]["n"] - MAX_MEMORIES_PER_USER
        if overflow > 0:
            victims = self._weakest(user_id, overflow)
            for v in victims:
                self._exec(
                    "UPDATE memories SET status='forgotten' WHERE id=?", (v["id"],)
                )

    # ------------------------------------------------------- maintenance ---
    def _retention(self, m, now):
        age_days = (now - (m["last_accessed"] or m["created_at"])) / DAY
        half_life = HALF_LIFE_DAYS.get(m["type"], 90.0)
        recency = math.exp(-age_days * math.log(2.0) / half_life)
        usage = min(m["strength"], 5.0) / 5.0
        return 0.5 * m["importance"] + 0.3 * usage + 0.2 * recency

    def _weakest(self, user_id, n):
        now = _now()
        mems = self._active(user_id, with_vectors=False)
        mems.sort(key=lambda m: self._retention(m, now))
        return mems[:n]

    def sleep_cycle(self, user_id):
        """Consolidation + forgetting pass ("what the brain does at night").

        1. Forget: mature memories whose retention dropped below the floor.
        2. Consolidate: cluster highly-similar memories and merge each cluster
           into one dense semantic memory (children keep an audit trail).
        """
        now = _now()
        report = {"forgotten": [], "consolidated": []}
        if ABLATION == "no_sleep":
            return report

        for m in self._active(user_id, with_vectors=False):
            age = (now - m["created_at"]) / DAY
            if age >= RETENTION_GRACE_DAYS and self._retention(m, now) < RETENTION_FLOOR:
                self._exec(
                    "UPDATE memories SET status='forgotten' WHERE id=?", (m["id"],)
                )
                report["forgotten"].append(
                    {"id": m["id"], "content": m["content"]}
                )

        # Cluster within each memory type: a diet preference must not be
        # vacuumed into an episodic travel plan just because both mention food.
        by_type = {}
        for m in self._active(user_id):
            if m["vec"] is not None:
                by_type.setdefault(m["type"], []).append(m)

        for mtype, mems in by_type.items():
            parent = list(range(len(mems)))

            def find(i):
                while parent[i] != i:
                    parent[i] = parent[parent[i]]
                    i = parent[i]
                return i

            for i in range(len(mems)):
                for j in range(i + 1, len(mems)):
                    if _cosine(mems[i]["vec"], mems[j]["vec"]) >= CLUSTER_SIM:
                        parent[find(i)] = find(j)

            clusters = {}
            for i in range(len(mems)):
                clusters.setdefault(find(i), []).append(mems[i])

            for group in clusters.values():
                if len(group) < 3:
                    continue
                merged = self._merge_cluster(user_id, group, mtype)
                if merged:
                    report["consolidated"].append(merged)
        return report

    def _merge_cluster(self, user_id, group, mtype="semantic"):
        items = "\n".join("- " + m["content"] for m in group)
        try:
            text, _ = qwen_client.chat(
                [{"role": "user", "content": CONSOLIDATE_PROMPT.format(items=items)}],
                model=qwen_client.FAST_MODEL,
                temperature=0.2,
                max_tokens=120,
                enable_thinking=False,
            )
        except qwen_client.QwenError:
            return None
        summary = text.strip().strip('"')[:220]
        if len(summary) < 10:
            return None
        vec = qwen_client.embed([summary])[0]
        new_id = _uuid()
        now = _now()
        importance = max(m["importance"] for m in group)
        strength = min(sum(m["strength"] for m in group), 8.0)
        self._exec(
            "INSERT INTO memories(id, user_id, type, content, embedding, "
            "importance, strength, created_at, last_accessed, source_session) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (new_id, user_id, mtype, summary, _pack(vec), importance,
             strength, now, now, "sleep-cycle"),
        )
        for m in group:
            self._exec(
                "UPDATE memories SET status='consolidated', consolidated_into=? "
                "WHERE id=?",
                (new_id, m["id"]),
            )
        return {
            "id": new_id,
            "content": summary,
            "merged": [{"id": m["id"], "content": m["content"]} for m in group],
        }

    def has_memories(self, user_id):
        rows = self._rows(
            "SELECT COUNT(*) AS n FROM memories WHERE user_id=?", (user_id,)
        )
        return rows[0]["n"] > 0

    def clone_seed(self, user_id, seed_uid):
        """Copy the seed user's memory graph into a fresh user.

        First-time visitors land on a living constellation (with its
        supersede/consolidation audit trail) instead of an empty page,
        and still get their own isolated space to play in.
        """
        if not seed_uid or user_id == seed_uid:
            return 0
        rows = self._rows("SELECT * FROM memories WHERE user_id=?", (seed_uid,))
        if not rows:
            return 0
        idmap = {m["id"]: _uuid() for m in rows}
        with self._lock:
            for m in rows:
                self._db.execute(
                    "INSERT INTO memories(id, user_id, type, content, embedding, "
                    "importance, strength, created_at, last_accessed, access_count, "
                    "status, source_session, superseded_by, consolidated_into) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (idmap[m["id"]], user_id, m["type"], m["content"],
                     m["embedding"], m["importance"], m["strength"],
                     m["created_at"], m["last_accessed"], m["access_count"],
                     m["status"], "seed",
                     idmap.get(m["superseded_by"]),
                     idmap.get(m["consolidated_into"])),
                )
            self._db.commit()
        return len(rows)

    def forget(self, user_id, memory_id):
        self._exec(
            "UPDATE memories SET status='forgotten' "
            "WHERE id=? AND user_id=?",
            (memory_id, user_id),
        )

    # ------------------------------------------------------------- views ---
    def memory_graph(self, user_id):
        """All memories + similarity links, ready for the constellation UI."""
        now = _now()
        rows = self._rows(
            "SELECT * FROM memories WHERE user_id=? ORDER BY created_at",
            (user_id,),
        )
        nodes = []
        vecs = {}
        for m in rows:
            if m["status"] == "active" and m["embedding"]:
                vecs[m["id"]] = _unpack(m["embedding"])
            nodes.append(
                {
                    "id": m["id"],
                    "type": m["type"],
                    "content": m["content"],
                    "importance": m["importance"],
                    "strength": m["strength"],
                    "status": m["status"],
                    "access_count": m["access_count"],
                    "created_at": m["created_at"],
                    "last_accessed": m["last_accessed"],
                    "retention": round(self._retention(m, now), 3),
                    "superseded_by": m["superseded_by"],
                    "consolidated_into": m["consolidated_into"],
                }
            )
        links = []
        ids = list(vecs.keys())[:220]
        for i, a in enumerate(ids):
            best = []
            for b in ids:
                if a == b:
                    continue
                sim = _cosine(vecs[a], vecs[b])
                if sim > 0.50:
                    best.append((sim, b))
            best.sort(reverse=True)
            for sim, b in best[:2]:
                if a < b:
                    links.append({"a": a, "b": b, "w": round(sim, 3)})
        seen = set()
        uniq = []
        for l in links:
            key = (l["a"], l["b"])
            if key not in seen:
                seen.add(key)
                uniq.append(l)
        return {"nodes": nodes, "links": uniq}

    def stats(self, user_id):
        rows = self._rows(
            "SELECT status, COUNT(*) AS n FROM memories WHERE user_id=? "
            "GROUP BY status",
            (user_id,),
        )
        by_status = {r["status"]: r["n"] for r in rows}
        type_rows = self._rows(
            "SELECT type, COUNT(*) AS n FROM memories "
            "WHERE user_id=? AND status='active' GROUP BY type",
            (user_id,),
        )
        reinforced = self._rows(
            "SELECT COALESCE(SUM(access_count),0) AS n FROM memories WHERE user_id=?",
            (user_id,),
        )[0]["n"]
        hist = self._rows(
            "SELECT COALESCE(SUM(LENGTH(content)),0) AS chars, COUNT(*) AS n "
            "FROM messages WHERE user_id=?",
            (user_id,),
        )[0]
        active_mem_tokens = sum(
            _est_tokens(m["content"])
            for m in self._active(user_id, with_vectors=False)
        )
        return {
            "by_status": by_status,
            "by_type": {r["type"]: r["n"] for r in type_rows},
            "reinforcements": reinforced,
            "messages": hist["n"],
            "full_history_tokens": int(hist["chars"] / 4),
            "memory_store_tokens": active_mem_tokens,
        }

    # ---------------------------------------------------------- sessions ---
    def create_session(self, user_id, title=None):
        sid = _uuid()
        self._exec(
            "INSERT INTO sessions(id, user_id, title, created_at) VALUES(?,?,?,?)",
            (sid, user_id, title or "Session", _now()),
        )
        return sid

    def list_sessions(self, user_id):
        return self._rows(
            "SELECT s.*, (SELECT COUNT(*) FROM messages m WHERE m.session_id=s.id) "
            "AS message_count FROM sessions s WHERE s.user_id=? "
            "ORDER BY s.created_at DESC LIMIT 30",
            (user_id,),
        )

    def log_message(self, session_id, user_id, role, content, recalled_ids=None):
        self._exec(
            "INSERT INTO messages(session_id, user_id, role, content, created_at, "
            "recalled_ids) VALUES(?,?,?,?,?,?)",
            (session_id, user_id, role, content, _now(),
             json.dumps(recalled_ids or [])),
        )

    def session_messages(self, session_id, limit=40):
        rows = self._rows(
            "SELECT role, content, created_at, recalled_ids FROM messages "
            "WHERE session_id=? ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        )
        return list(reversed(rows))

    # -------------------------------------------------------------- quota ---
    def usage_today(self):
        day = time.strftime("%Y-%m-%d", time.gmtime())
        rows = self._rows("SELECT chats FROM usage_log WHERE day=?", (day,))
        return rows[0]["chats"] if rows else 0

    def count_chat(self, tokens=0):
        # No UPSERT: the production host's SQLite (CentOS 7) predates 3.24.
        day = time.strftime("%Y-%m-%d", time.gmtime())
        with self._lock:
            self._db.execute(
                "INSERT OR IGNORE INTO usage_log(day, chats, tokens) VALUES(?,0,0)",
                (day,),
            )
            self._db.execute(
                "UPDATE usage_log SET chats=chats+1, tokens=tokens+? WHERE day=?",
                (tokens, day),
            )
            self._db.commit()
