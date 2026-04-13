"""Memory DSL for N184 — fact, desire, opinion, backlog.

Ported from MyMilla's core.clj memory statement system. Provides a
simple vocabulary for storing structured knowledge about the HIL,
project context, and deferred work.

Four statement types:
  - Fact: Persistent world knowledge (user role, project constraints)
  - Desire: Goals, preferences, intentions
  - Opinion: Subjective evaluations (code quality, maintainer style)
  - Backlog: Deferred tasks and ideas for later review

Statements are stored in the Memory Palace SQLite database and
optionally indexed in ChromaDB for semantic retrieval.

Origin: MyMilla (github.com/MillaFleurs/MyMilla) src/milla/core.clj
License: AGPL-3.0
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from n184_memory_palace.config import SQLITE_DB_PATH

# ── Schema ────────────────────────────────────────────────────────────

STATEMENTS_SCHEMA = """\
CREATE TABLE IF NOT EXISTS statements (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL CHECK(kind IN ('fact', 'desire', 'opinion', 'backlog')),
    text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    source TEXT DEFAULT 'hil'
);
CREATE INDEX IF NOT EXISTS idx_statements_kind ON statements(kind);
"""

VALID_KINDS = ("fact", "desire", "opinion", "backlog")


class MemoryDSL:
    """Simple DSL for storing and querying structured memory statements."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self.db_path = Path(db_path) if db_path else SQLITE_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
        return self._conn

    def initialize(self) -> None:
        """Create the statements table if it doesn't exist."""
        self._conn = None  # Force reconnect
        self.conn.executescript(STATEMENTS_SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ── Create ────────────────────────────────────────────────────────

    def _add(self, kind: str, text: str, source: str = "hil") -> int:
        """Store a single statement."""
        now = datetime.now(timezone.utc).isoformat()
        cur = self.conn.execute(
            "INSERT INTO statements (kind, text, created_at, source) VALUES (?, ?, ?, ?)",
            (kind, text, now, source),
        )
        self.conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def fact(self, text: str, source: str = "hil") -> int:
        """Store a fact — persistent world knowledge."""
        return self._add("fact", text, source)

    def desire(self, text: str, source: str = "hil") -> int:
        """Store a desire — goal, preference, intention."""
        return self._add("desire", text, source)

    def opinion(self, text: str, source: str = "hil") -> int:
        """Store an opinion — subjective evaluation."""
        return self._add("opinion", text, source)

    def backlog(self, text: str, source: str = "hil") -> int:
        """Store a backlog item — deferred task or idea."""
        return self._add("backlog", text, source)

    # ── Query ─────────────────────────────────────────────────────────

    def _query_kind(self, kind: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM statements WHERE kind = ? ORDER BY created_at",
            (kind,),
        ).fetchall()
        return [dict(r) for r in rows]

    def facts(self) -> list[dict[str, Any]]:
        """All fact statements, oldest first."""
        return self._query_kind("fact")

    def desires(self) -> list[dict[str, Any]]:
        """All desire statements, oldest first."""
        return self._query_kind("desire")

    def opinions(self) -> list[dict[str, Any]]:
        """All opinion statements, oldest first."""
        return self._query_kind("opinion")

    def backlog_items(self) -> list[dict[str, Any]]:
        """All backlog statements, oldest first."""
        return self._query_kind("backlog")

    def all_statements(self) -> list[dict[str, Any]]:
        """All statements regardless of kind, oldest first."""
        rows = self.conn.execute(
            "SELECT * FROM statements ORDER BY created_at"
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Delete ────────────────────────────────────────────────────────

    def remove(self, statement_id: int) -> bool:
        """Remove a statement by ID."""
        cur = self.conn.execute(
            "DELETE FROM statements WHERE id = ?", (statement_id,)
        )
        self.conn.commit()
        return cur.rowcount > 0

    def clear_kind(self, kind: str) -> int:
        """Remove all statements of a given kind. Returns count removed."""
        if kind not in VALID_KINDS:
            raise ValueError(f"Invalid kind: {kind}. Must be one of {VALID_KINDS}")
        cur = self.conn.execute(
            "DELETE FROM statements WHERE kind = ?", (kind,)
        )
        self.conn.commit()
        return cur.rowcount

    # ── System Prompt Builder ─────────────────────────────────────────

    def build_context(self) -> str:
        """Build a structured context string from all statements.

        Designed to be injected into a system prompt or prepended to
        agent instructions. Groups by kind for readability.
        """
        sections = []
        for kind, label in [
            ("fact", "Known Facts"),
            ("desire", "Goals & Preferences"),
            ("opinion", "Opinions & Evaluations"),
            ("backlog", "Deferred Items"),
        ]:
            items = self._query_kind(kind)
            if items:
                lines = [f"- {item['text']}" for item in items]
                sections.append(f"## {label}\n" + "\n".join(lines))

        return "\n\n".join(sections) if sections else ""
