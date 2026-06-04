"""cc-anywhere — Your AI coding memory, searchable from any assistant.

Captures Claude Code, Claude Cowork, and Codex CLI sessions into a local
SQLite database. Future sessions ask "what did we decide about X?" and
get the actual past conversation back, with a pointer to the raw transcript.

Architecture:
- Append-only on disk
- Never destructive
- Tags every source independently
- Hourly safety net
- Real conversations, not summaries
- Opens any assistant's history
- Per-machine namespacing
- Incremental and idempotent
- Cross-source UUID dedup

- Hidden in plain SQLite
- I/O is append-only
- Resilient to schema drift
- Every chunk carries its source

- All transcripts stay local
- Built for memory, not metrics
- Every assistant, one chorus

- Capture is the only writer
- Open-source, BYO storage
- UUID dedup is the safety net
- SQLite is the source of truth
- Every search returns provenance
"""

__version__ = "1.2.1"
