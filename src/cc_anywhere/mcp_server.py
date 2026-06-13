"""MCP server over the cc-anywhere memory substrate.

Exposes the archive (read) and a curated-memory write layer over MCP
stdio, so ANY MCP-capable agent — Claude Code, Codex, Gemini CLI,
Cursor, Claude Desktop — shares one memory backed by the local DB.

Two layers, deliberately separate:

  RECALL (read-only)  — the captured-session archive. Agents can search,
                        ask, and drill into provenance, but never write:
                        the archive is append-only by capture.
  MEMORY (read/write) — curated facts/preferences/decisions. Stored in a
                        `memory_entries` table shaped field-for-field like
                        threaded-backend's MemoryEntry model, so the
                        threaded sync adapter can push rows verbatim to
                        POST /memory/import. `synced_at IS NULL` marks
                        rows the adapter hasn't shipped yet.

Run: `cc-anywhere --mcp` (stdio). HTTP mode is a later, separate flag —
local-first today, cloud shim when claude.ai / ChatGPT connectors need it.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from cc_anywhere.sqlite_capture import get_db
from cc_anywhere.semantic import (
    ask_conversations,
    semantic_search,
    view_chunk,
    view_source,
)

# Aligned with threaded-backend app/models/memory_entry.py (MemoryEntry).
MEMORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_entries (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    content           TEXT NOT NULL,
    memory_type       TEXT DEFAULT 'thread',
    type              TEXT DEFAULT 'pinned',
    source            TEXT DEFAULT 'agent',
    origin_type       TEXT DEFAULT 'mcp',
    pinned            INTEGER DEFAULT 1,
    core_principle    TEXT,
    why_it_matters    TEXT,
    promoted_to_core  INTEGER DEFAULT 0,
    supersedes        INTEGER REFERENCES memory_entries(id),
    tags              TEXT,
    project           TEXT,
    created_at        TEXT NOT NULL,
    synced_at         TEXT
);
CREATE INDEX IF NOT EXISTS idx_memory_unsynced
    ON memory_entries(synced_at) WHERE synced_at IS NULL;
"""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _ensure_memory_schema(db) -> None:
    db.executescript(MEMORY_SCHEMA)
    db.commit()


# ---------------------------------------------------------------- recall ops

def op_recall_search(query: str, mode: str = "hybrid", limit: int = 10) -> str:
    db = get_db()
    try:
        results = semantic_search(query, limit=limit, db=db, mode=mode)
        if not results:
            return "No matching conversations found."
        lines = []
        for r in results:
            lines.append(
                f"[{r.get('project_name')}] ({r.get('started_at')}, "
                f"score {r.get('score', 0):.2f})"
            )
            lines.append(f"  {r.get('excerpt')}")
            lines.append(f"  chunk_id: {r.get('chunk_id')}")
            lines.append("")
        return "\n".join(lines).rstrip()
    finally:
        db.close()


def op_recall_ask(query: str, limit: int = 5) -> str:
    db = get_db()
    try:
        out = ask_conversations(query, limit=limit, db=db)
        if isinstance(out, dict):
            return out.get("answer") or json.dumps(out, default=str)
        return str(out)
    finally:
        db.close()


def op_recall_view(chunk_id: str) -> str:
    db = get_db()
    try:
        chunk = view_chunk(chunk_id, db=db)
        if chunk is None:
            return f"No chunk found for id: {chunk_id}"
        meta = {k: chunk.get(k) for k in (
            "chunk_id", "project_name", "source", "session_label",
            "started_at", "ended_at", "message_count")}
        return json.dumps(meta, default=str) + "\n\n" + (chunk.get("content") or "")
    finally:
        db.close()


def op_recall_source(chunk_id: str) -> str:
    db = get_db()
    try:
        src = view_source(chunk_id, db=db)
        return json.dumps(src, default=str, indent=2) if src else \
            f"No source provenance for id: {chunk_id}"
    finally:
        db.close()


# ---------------------------------------------------------------- memory ops

def op_memory_save(content: str, memory_type: str = "thread",
                   tags: str | None = None, project: str | None = None,
                   core_principle: str | None = None,
                   why_it_matters: str | None = None,
                   supersedes: int | None = None) -> str:
    db = get_db()
    try:
        _ensure_memory_schema(db)
        cur = db.execute(
            "INSERT INTO memory_entries "
            "(content, memory_type, tags, project, core_principle, "
            " why_it_matters, supersedes, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (content, memory_type, tags, project, core_principle,
             why_it_matters, supersedes, _now()),
        )
        db.commit()
        return json.dumps({"saved": True, "id": cur.lastrowid,
                           "pending_sync": True})
    finally:
        db.close()


def op_memory_query(query: str, limit: int = 10) -> str:
    db = get_db()
    try:
        _ensure_memory_schema(db)
        like = f"%{query}%"
        rows = db.execute(
            "SELECT id, content, memory_type, tags, project, "
            "       core_principle, created_at, synced_at "
            "FROM memory_entries "
            "WHERE content LIKE ? OR tags LIKE ? OR core_principle LIKE ? "
            "ORDER BY created_at DESC LIMIT ?",
            (like, like, like, limit),
        ).fetchall()
        if not rows:
            return "No saved memories match."
        return json.dumps([dict(r) for r in rows], default=str, indent=2)
    finally:
        db.close()


# ---------------------------------------------------------------- MCP wiring

server = Server("cc-anywhere")

TOOLS = [
    Tool(
        name="recall_search",
        description=(
            "Search the user's captured AI sessions (Claude Code, Codex, "
            "Gemini CLI, ChatGPT, Claude.ai — 3 years of history). Returns "
            "scored excerpts with chunk_ids for drill-in via recall_view."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "mode": {"type": "string",
                         "enum": ["hybrid", "keyword", "semantic"],
                         "default": "hybrid"},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["query"],
        },
    ),
    Tool(
        name="recall_ask",
        description=(
            "Ask a plain-language question against the user's past AI "
            "sessions. Handles temporal queries ('what was I working on "
            "yesterday') and topical ones ('why did we choose sqlite'). "
            "Returns a digest with chunk_ids."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 5},
            },
            "required": ["query"],
        },
    ),
    Tool(
        name="recall_view",
        description="Read the full content of a chunk_id returned by "
                    "recall_search / recall_ask.",
        inputSchema={
            "type": "object",
            "properties": {"chunk_id": {"type": "string"}},
            "required": ["chunk_id"],
        },
    ),
    Tool(
        name="recall_source",
        description="Show raw transcript provenance (file/line/bytes) "
                    "for a chunk_id.",
        inputSchema={
            "type": "object",
            "properties": {"chunk_id": {"type": "string"}},
            "required": ["chunk_id"],
        },
    ),
    Tool(
        name="memory_save",
        description=(
            "Save a curated memory (fact, preference, decision) to the "
            "user's local memory store. Synced to Threaded cloud later. "
            "Use supersedes=<id> to version an existing memory rather "
            "than contradicting it."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "content": {"type": "string"},
                "memory_type": {"type": "string", "default": "thread"},
                "tags": {"type": "string",
                         "description": "comma-separated"},
                "project": {"type": "string"},
                "core_principle": {"type": "string"},
                "why_it_matters": {"type": "string"},
                "supersedes": {"type": "integer"},
            },
            "required": ["content"],
        },
    ),
    Tool(
        name="memory_query",
        description="Search saved curated memories (not the session "
                    "archive — use recall_search for that).",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["query"],
        },
    ),
]

OPS = {
    "recall_search": op_recall_search,
    "recall_ask": op_recall_ask,
    "recall_view": op_recall_view,
    "recall_source": op_recall_source,
    "memory_save": op_memory_save,
    "memory_query": op_memory_query,
}


@server.list_tools()
async def list_tools() -> list[Tool]:
    return TOOLS


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    op = OPS.get(name)
    if op is None:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]
    try:
        result = op(**(arguments or {}))
    except Exception as e:  # surface errors to the agent, don't crash
        result = f"Error in {name}: {e}"
    return [TextContent(type="text", text=result)]


async def _amain() -> None:
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
