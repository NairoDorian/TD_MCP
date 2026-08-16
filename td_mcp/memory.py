"""Session memory (tdmcp memory vault / cross-session continuity, ai-brain/mcp_touch).

SQLite-backed store (stdlib ``sqlite3`` + FTS5) so sessions are durable, queryable,
and survive restarts — an agent never loses context between turns or processes.
The tool surface (td_memory_save / td_memory_recall) is unchanged from the
JSONL implementation; only the storage engine moved from hand-rolled JSONL to
SQLite, which also gives SQL-level inspection/debugging.

Trust tiers (TDPilot v2.5.4 influence): ``official > bundled > personal >
community > experimental``.  Each saved turn carries a tier that weights recall
so authoritative hits surface before community/transcript ones.  The agent can
then decide whether to trust a hit or validate it before acting.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# Lower index = higher trust.
TIERS = ("official", "bundled", "personal", "community", "experimental")
_TIER_ORDER = {t: i for i, t in enumerate(TIERS)}
_DEFAULT_TIER = "personal"

_DEFAULT_PATH = Path.home() / ".td_mcp" / "memory" / "session.db"


def _resolve_path(path: Optional[str]) -> Path:
    """Resolve the DB path.

    Honors ``TD_MCP_MEMORY_DB`` so callers/tests can override the store location.
    """
    env = os.environ.get("TD_MCP_MEMORY_DB")
    if env:
        return Path(env)
    return Path(path) if path else _DEFAULT_PATH


_STOP = set("the a an of to in on for and or is are be as by from with".split())
_TOKEN = re.compile(r"[a-z0-9_]+")


class SessionMemory:
    def __init__(self, path: Optional[str] = None):
        self.path = _resolve_path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._ensure_schema()

    # -- schema -------------------------------------------------------------
    def _ensure_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS memory USING fts5(
                ts           UNINDEXED,
                role         UNINDEXED,
                text,
                tags         UNINDEXED,
                trust_tier   UNINDEXED,
                tokenize = 'porter unicode61'
            );
            """
        )
        self._conn.commit()

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass

    # -- mutation -----------------------------------------------------------
    def save(self, role: str, text: str, tags: Optional[List[str]] = None,
             trust_tier: str = _DEFAULT_TIER) -> Dict[str, Any]:
        """Persist a turn. ``trust_tier`` weights recall ordering."""
        tier = trust_tier if trust_tier in _TIER_ORDER else _DEFAULT_TIER
        ts = time.time()
        self._conn.execute(
            "INSERT INTO memory(ts, role, text, tags, trust_tier) VALUES (?, ?, ?, ?, ?)",
            (ts, role, text, json.dumps(tags or []), tier),
        )
        self._conn.commit()
        return {"ts": ts, "role": role, "text": text,
                "tags": tags or [], "trust_tier": tier}

    def learn(self, text: str, source: str = "community", tags: Optional[List[str]] = None) -> Dict[str, Any]:
        """Record a learned fact with an inferred trust tier from its source.

        Map a source label to a tier: official docs/bundled recipes -> high trust,
        community/external -> lower.  Returns the stored entry.
        """
        tier = _SOURCE_TIER.get(source, "community")
        return self.save("learned", text, tags=tags or [source], trust_tier=tier)

    def clear(self) -> None:
        self._conn.execute("DELETE FROM memory")
        self._conn.commit()

    # -- query --------------------------------------------------------------
    @staticmethod
    def _query_terms(query: str) -> List[str]:
        return [t for t in _TOKEN.findall(query.lower()) if t not in _STOP]

    def recall(self, query: str, k: int = 5) -> List[Dict[str, Any]]:
        """Recall past entries by keyword overlap, weighted by trust tier.

        FTS5 narrows the candidate set fast; final ranking prefers higher-trust
        tiers first, then keyword-overlap count, so a verified "bundled" technique
        beats a raw transcript snippet for the same query.
        """
        terms = self._query_terms(query)
        if not terms:
            return []
        fts_q = " OR ".join(f'"{t}"' for t in terms)
        try:
            rows = self._conn.execute(
                "SELECT ts, role, text, tags, trust_tier FROM memory WHERE memory MATCH ?",
                (fts_q,),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []

        scored: List[tuple] = []  # (tier_order, overlap, ts, row)
        for r in rows:
            body = ((r["text"] or "") + " " + (r["tags"] or "")).lower()
            toks = set(_TOKEN.findall(body))
            overlap = len(set(terms) & toks)
            if overlap:
                weighted = _TIER_ORDER.get(r["trust_tier"], len(TIERS))
                scored.append((weighted, -overlap, r["ts"], r))
        # tier asc, overlap desc (negated), then recency desc
        scored.sort()
        out = []
        for _, _, _, r in scored[:k]:
            out.append({
                "ts": r["ts"], "role": r["role"],
                "text": r["text"], "tags": _parse_tags(r["tags"]),
                "trust_tier": r["trust_tier"],
            })
        return out

    def summary(self, last: int = 10) -> List[Dict[str, Any]]:
        """Most recent ``last`` entries (newest first), for conversation compaction."""
        rows = self._conn.execute(
            "SELECT ts, role, text, tags, trust_tier FROM memory ORDER BY ts DESC LIMIT ?",
            (last,),
        ).fetchall()
        return [{
            "ts": r["ts"], "role": r["role"],
            "text": r["text"], "tags": _parse_tags(r["tags"]),
            "trust_tier": r["trust_tier"],
        } for r in rows]

    def summarize(self, last: int = 10) -> str:
        recent = self.summary(last)
        if not recent:
            return "(no memory yet)"
        lines = [f"[{e['role']}] {e['text'][:160]}" for e in recent]
        return "\n".join(lines)

    def __len__(self) -> int:
        try:
            row = self._conn.execute("SELECT count(*) AS n FROM memory").fetchone()
            return row["n"] if row else 0
        except sqlite3.Error:
            return 0

    def __iter__(self):
        rows = self._conn.execute(
            "SELECT ts, role, text, tags, trust_tier FROM memory ORDER BY ts"
        ).fetchall()
        for r in rows:
            yield {"ts": r["ts"], "role": r["role"],
                   "text": r["text"], "tags": _parse_tags(r["tags"]),
                   "trust_tier": r["trust_tier"]}


def _parse_tags(raw: str) -> List[str]:
    if not raw:
        return []
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return [t for t in raw.split(",") if t]


COMPACTION_MARKER = "[[TD_MCP_COMPACTED_HISTORY]]"


def compact_messages(messages, threshold=20, keep_recent=10):
    """Conversation compaction (TDPilot technique, MIT-safe reimpl).

    When ``len(messages) >= threshold``, summarize the *older* turns into one
    text-only synthetic assistant message and keep the most recent
    ``keep_recent`` messages verbatim. Pure local heuristics — no LLM call.

    The synthetic message records user-prompt count, first/latest user goals,
    deduped tool usage counts, and tool-error count. It is safe to prepend to
    the retained recent slice.
    """
    if threshold <= 0 or len(messages) < threshold:
        return messages, None
    cut = len(messages) - keep_recent
    older = messages[:cut]
    recent = messages[cut:]
    summary = _summarize_turns(older)
    synthetic = {
        "role": "assistant",
        "content": [{"type": "text", "text": COMPACTION_MARKER + "\n\n" + summary}],
    }
    compacted = {
        "message_count": len(older),
        "summary": summary,
        "kept_recent": len(recent),
    }
    return [synthetic, *recent], compacted


def _summarize_turns(messages):
    """Local-heuristic summary of a list of Anthropic-style messages."""
    user_prompts = []
    tool_counts = {}
    tool_errors = 0
    for m in messages:
        role = m.get("role")
        content = m.get("content") or []
        if isinstance(content, str):
            text = content
        else:
            text = " ".join(
                b.get("text", "")
                for b in content
                if isinstance(b, dict) and b.get("type") in ("text", "tool_result")
            )
        if role == "user":
            user_prompts.append(text.strip())
        elif role == "assistant":
            for b in (content if isinstance(content, list) else []):
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    name = b.get("name", "?")
                    tool_counts[name] = tool_counts.get(name, 0) + 1
                if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("is_error"):
                    tool_errors += 1
    lines = [
        f"This summary replaces {len(messages)} compacted messages.",
        f"User prompts: {len(user_prompts)}.",
    ]
    if user_prompts:
        lines.append(f"First user goal: {user_prompts[0][:200]!r}")
        lines.append(f"Latest user goal before this point: {user_prompts[-1][:200]!r}")
    if tool_counts:
        tools = ", ".join(f"{k}×{v}" for k, v in sorted(tool_counts.items(), key=lambda kv: -kv[1]))
        lines.append(f"Tools used (deduped): {tools}.")
    lines.append(f"Tool errors encountered: {tool_errors}.")
    lines.append("Use td_history_recall for the full history if needed.")
    return "\n".join(lines)


class TechniqueStore:
    """Technique memory cycle (TDPilot technique, MIT-safe reimpl).

    Stores named network techniques with a validation ladder:
    ``candidate -> validated_local -> validated_portable`` (deprecated is a
    terminal state). Replay success auto-promotes; failure auto-demotes one
    rung. ``promote`` copies a project-scoped technique into the global store.

    Pure stdlib sqlite3 — same storage philosophy as SessionMemory.
    """

    _VALID_STATES = ("candidate", "validated_local", "validated_portable", "deprecated")

    def __init__(self, path=None):
        env = os.environ.get("TD_MCP_TECHNIQUE_DB")
        self.path = Path(path or env or (Path.home() / ".td_mcp" / "memory" / "techniques.db"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._ensure_schema()

    def _ensure_schema(self):
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS techniques (
                id          TEXT PRIMARY KEY,
                scope       TEXT NOT NULL DEFAULT 'project',
                name        TEXT NOT NULL,
                description TEXT,
                tags        TEXT,
                state       TEXT NOT NULL DEFAULT 'candidate',
                recipe      TEXT,
                validation  TEXT,
                replay_count INTEGER DEFAULT 0,
                created_at  REAL,
                updated_at  REAL,
                promoted_from TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_techniques_state ON techniques(state);
            CREATE INDEX IF NOT EXISTS idx_techniques_scope ON techniques(scope);
            """
        )
        self._conn.commit()

    def _now(self):
        return time.time()

    def add(self, name, recipe, scope="project", description="", tags=None,
            state="candidate"):
        import uuid as _uuid
        tid = str(_uuid.uuid4())
        now = self._now()
        self._conn.execute(
            "INSERT INTO techniques(id, scope, name, description, tags, state, recipe, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (tid, scope, name, description, json.dumps(tags or []), state,
             json.dumps(recipe, default=str), now, now),
        )
        self._conn.commit()
        return self.get(tid)

    def get(self, technique_id):
        row = self._conn.execute(
            "SELECT * FROM techniques WHERE id = ?", (technique_id,)
        ).fetchone()
        return dict(row) if row else None

    def list(self, scope=None, state=None, limit=100):
        q = "SELECT * FROM techniques"
        clauses, params = [], []
        if scope:
            clauses.append("scope = ?")
            params.append(scope)
        if state:
            clauses.append("state = ?")
            params.append(state)
        if clauses:
            q += " WHERE " + " AND ".join(clauses)
        q += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(q, params).fetchall()
        return [dict(r) for r in rows]

    def update_state(self, technique_id, state):
        if state not in self._VALID_STATES:
            return None
        self._conn.execute(
            "UPDATE techniques SET state = ?, updated_at = ? WHERE id = ?",
            (state, self._now(), technique_id),
        )
        self._conn.commit()
        return self.get(technique_id)

    def record_replay(self, technique_id, ok):
        """Bump replay_count and adjust state via the validation ladder."""
        row = self.get(technique_id)
        if row is None:
            return None
        state = row["state"]
        if ok:
            if state == "candidate":
                state = "validated_local"
            elif state == "validated_local":
                state = "validated_portable"
        else:
            if state == "validated_portable":
                state = "validated_local"
            elif state == "validated_local":
                state = "candidate"
        self._conn.execute(
            "UPDATE techniques SET replay_count = replay_count + 1, state = ?, "
            "updated_at = ? WHERE id = ?",
            (state, self._now(), technique_id),
        )
        self._conn.commit()
        return self.get(technique_id)

    def promote(self, technique_id):
        """Copy a project-scoped technique into the global store."""
        row = self.get(technique_id)
        if row is None:
            return None
        import uuid as _uuid
        new_id = str(_uuid.uuid4())
        now = self._now()
        self._conn.execute(
            "INSERT INTO techniques(id, scope, name, description, tags, state, recipe, "
            "validation, replay_count, created_at, updated_at, promoted_from) "
            "VALUES (?, 'global', ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)",
            (new_id, row["name"], row["description"], row["tags"], row["state"],
             row["recipe"], row["validation"], now, now, technique_id),
        )
        self._conn.commit()
        return self.get(new_id)

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass


def pre_turn_injection(query, k=5):
    """Ambient pre-turn context injection (TDPilot/DeepSeek technique).

    Runs cheap local retrieval over session memory and technique store and
    returns a compact string of top hits suitable for prepending to the agent
    context. Not persisted; a no-op when stores are empty or ``query`` is too
    short to be a useful retrieval key.
    """
    if not query or len(query.strip()) < 4:
        return ""
    from td_mcp.activity import history_recall
    hits = history_recall(query, k=k)
    if not hits:
        return ""
    lines = ["# Pre-turn context (local memory)"]
    for h in hits:
        if h.get("source") == "session":
            lines.append(f"- [{h.get('trust_tier', 'personal')}] "
                         f"{h.get('text', '')[:160]}")
        else:
            lines.append(f"- [technique/{h.get('state', 'candidate')}] "
                         f"{h.get('name', '')}: {h.get('description', '')[:160]}")
    return "\n".join(lines)


# Sources a learned fact can cite; higher entries are more authoritative.
_SOURCE_TIER = {
    "official": "official",    # docs.derivative.ca, TD release notes
    "bundled": "bundled",      # td-mcp curated corpus / verified recipes
    "personal": "personal",    # the agent's own confirmed actions
    "community": "community",  # forums, community posts
    "experimental": "experimental",  # untested / draft
}
