"""SQLite knowledge graph for the N184 Memory Palace.

Stores relational data: wings, rooms, halls, findings, tunnels, feedback,
culture profiles, pattern evolution, and statistics. ChromaDB handles the
vector/document storage; this layer handles the structured relationships.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from n184_memory_palace.config import HALLS, SQLITE_DB_PATH

SCHEMA_SQL = """\
-- Wings: Top-level codebases
CREATE TABLE IF NOT EXISTS wings (
    wing_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    repository_url TEXT,
    last_scanned TEXT,
    total_findings INTEGER DEFAULT 0,
    false_positive_rate REAL
);

-- Rooms: Components within wings
CREATE TABLE IF NOT EXISTS rooms (
    room_id INTEGER PRIMARY KEY,
    wing_id INTEGER REFERENCES wings(wing_id),
    name TEXT NOT NULL,
    description TEXT,
    file_path TEXT,
    last_scanned TEXT
);

-- Halls: Knowledge types (the 7 halls)
CREATE TABLE IF NOT EXISTS halls (
    hall_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    chromadb_collection TEXT NOT NULL
);

-- Findings: Link SQLite to ChromaDB
CREATE TABLE IF NOT EXISTS findings (
    finding_id INTEGER PRIMARY KEY,
    wing_id INTEGER REFERENCES wings(wing_id),
    room_id INTEGER REFERENCES rooms(room_id),
    hall_id INTEGER REFERENCES halls(hall_id),
    chromadb_id TEXT NOT NULL,
    pattern_name TEXT,
    severity TEXT CHECK(severity IN ('critical', 'high', 'medium', 'low', 'info')),
    confirmed BOOLEAN,
    false_positive BOOLEAN,
    discovered_date TEXT,
    discovered_by TEXT,
    valid_from TEXT,
    valid_until TEXT
);

-- Tunnels: Cross-wing connections (same pattern in multiple codebases)
CREATE TABLE IF NOT EXISTS tunnels (
    tunnel_id INTEGER PRIMARY KEY,
    pattern_name TEXT NOT NULL,
    description TEXT,
    finding_id_1 INTEGER REFERENCES findings(finding_id),
    finding_id_2 INTEGER REFERENCES findings(finding_id),
    similarity_score REAL,
    notes TEXT
);

-- Human Feedback (HIL Loop)
CREATE TABLE IF NOT EXISTS human_feedback (
    feedback_id INTEGER PRIMARY KEY,
    finding_id INTEGER REFERENCES findings(finding_id),
    feedback_type TEXT CHECK(feedback_type IN ('confirmed', 'false_positive', 'needs_context', 'reframe')),
    human_explanation TEXT,
    lesson_learned TEXT,
    advocatus_chromadb_id TEXT,
    requires_reframing BOOLEAN,
    reframing_tactic TEXT,
    outcome_after_reframe TEXT,
    timestamp TEXT
);

-- Culture Profiles (per wing)
CREATE TABLE IF NOT EXISTS culture_profiles (
    profile_id INTEGER PRIMARY KEY,
    wing_id INTEGER REFERENCES wings(wing_id),
    verbosity_level TEXT CHECK(verbosity_level IN ('minimal', 'moderate', 'verbose')),
    formality TEXT CHECK(formality IN ('casual', 'professional', 'academic')),
    security_framing TEXT CHECK(security_framing IN ('avoid', 'moderate', 'required')),
    report_length_max_words INTEGER,
    diff_required BOOLEAN,
    cvss_expected BOOLEAN,
    report_template TEXT,
    acceptance_rate REAL,
    rejection_reasons TEXT,
    updated_date TEXT
);

-- Pattern Evolution (tracking how patterns improve)
CREATE TABLE IF NOT EXISTS pattern_evolution (
    evolution_id INTEGER PRIMARY KEY,
    pattern_name TEXT NOT NULL,
    version INTEGER DEFAULT 1,
    false_positive_rate_before REAL,
    false_positive_rate_after REAL,
    change_description TEXT,
    lessons_applied TEXT,
    timestamp TEXT
);

-- Statistics (dashboard metrics)
CREATE TABLE IF NOT EXISTS statistics (
    stat_id INTEGER PRIMARY KEY,
    metric_name TEXT NOT NULL,
    metric_value REAL,
    wing_id INTEGER REFERENCES wings(wing_id),
    hall_id INTEGER REFERENCES halls(hall_id),
    calculated_date TEXT
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_findings_wing ON findings(wing_id);
CREATE INDEX IF NOT EXISTS idx_findings_room ON findings(room_id);
CREATE INDEX IF NOT EXISTS idx_findings_hall ON findings(hall_id);
CREATE INDEX IF NOT EXISTS idx_findings_pattern ON findings(pattern_name);
CREATE INDEX IF NOT EXISTS idx_feedback_finding ON human_feedback(finding_id);
CREATE INDEX IF NOT EXISTS idx_tunnels_pattern ON tunnels(pattern_name);
"""


class SQLiteStore:
    """Manages the SQLite knowledge graph for the N184 Memory Palace."""

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
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    def initialize(self) -> None:
        """Create all tables, indexes, and seed the 7 halls."""
        self.conn.executescript(SCHEMA_SQL)
        for name, info in HALLS.items():
            self.conn.execute(
                "INSERT OR IGNORE INTO halls (name, description, chromadb_collection) "
                "VALUES (?, ?, ?)",
                (name, info["description"], info["collection"]),
            )
        self.conn.commit()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ── Wings ──────────────────────────────────────────────────────────

    def add_wing(
        self,
        name: str,
        description: str | None = None,
        repository_url: str | None = None,
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO wings (name, description, repository_url, last_scanned) "
            "VALUES (?, ?, ?, ?)",
            (name, description, repository_url, _now()),
        )
        self.conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def get_wing(self, name: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM wings WHERE name = ?", (name,)
        ).fetchone()
        return dict(row) if row else None

    def list_wings(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM wings").fetchall()]

    # ── Rooms ──────────────────────────────────────────────────────────

    def add_room(
        self,
        wing_name: str,
        name: str,
        description: str | None = None,
        file_path: str | None = None,
    ) -> int:
        wing = self.get_wing(wing_name)
        if wing is None:
            raise ValueError(f"Wing '{wing_name}' not found")
        cur = self.conn.execute(
            "INSERT INTO rooms (wing_id, name, description, file_path, last_scanned) "
            "VALUES (?, ?, ?, ?, ?)",
            (wing["wing_id"], name, description, file_path, _now()),
        )
        self.conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def get_room(self, wing_name: str, room_name: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT r.* FROM rooms r "
            "JOIN wings w ON r.wing_id = w.wing_id "
            "WHERE w.name = ? AND r.name = ?",
            (wing_name, room_name),
        ).fetchone()
        return dict(row) if row else None

    def list_rooms(self, wing_name: str | None = None) -> list[dict[str, Any]]:
        if wing_name:
            return [
                dict(r)
                for r in self.conn.execute(
                    "SELECT r.* FROM rooms r "
                    "JOIN wings w ON r.wing_id = w.wing_id "
                    "WHERE w.name = ?",
                    (wing_name,),
                ).fetchall()
            ]
        return [dict(r) for r in self.conn.execute("SELECT * FROM rooms").fetchall()]

    # ── Halls ──────────────────────────────────────────────────────────

    def get_hall(self, name: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM halls WHERE name = ?", (name,)
        ).fetchone()
        return dict(row) if row else None

    def list_halls(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM halls").fetchall()]

    # ── Findings ───────────────────────────────────────────────────────

    def add_finding(
        self,
        wing_name: str,
        room_name: str | None,
        hall_name: str,
        chromadb_id: str,
        pattern_name: str | None = None,
        severity: str | None = None,
        discovered_by: str | None = None,
    ) -> int:
        wing = self.get_wing(wing_name)
        if wing is None:
            raise ValueError(f"Wing '{wing_name}' not found")

        room_id = None
        if room_name:
            room = self.get_room(wing_name, room_name)
            if room is None:
                room_id = self.add_room(wing_name, room_name)
            else:
                room_id = room["room_id"]

        hall = self.get_hall(hall_name)
        if hall is None:
            raise ValueError(f"Hall '{hall_name}' not found")

        now = _now()
        cur = self.conn.execute(
            "INSERT INTO findings "
            "(wing_id, room_id, hall_id, chromadb_id, pattern_name, "
            " severity, confirmed, false_positive, discovered_date, "
            " discovered_by, valid_from) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                wing["wing_id"],
                room_id,
                hall["hall_id"],
                chromadb_id,
                pattern_name,
                severity,
                False,
                False,
                now,
                discovered_by,
                now,
            ),
        )
        # Bump wing finding count
        self.conn.execute(
            "UPDATE wings SET total_findings = total_findings + 1 WHERE wing_id = ?",
            (wing["wing_id"],),
        )
        self.conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def get_finding(self, finding_id: int) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM findings WHERE finding_id = ?", (finding_id,)
        ).fetchone()
        return dict(row) if row else None

    def update_finding(self, finding_id: int, **kwargs: Any) -> None:
        allowed = {
            "confirmed", "false_positive", "severity",
            "pattern_name", "valid_until",
        }
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        self.conn.execute(
            f"UPDATE findings SET {set_clause} WHERE finding_id = ?",
            (*updates.values(), finding_id),
        )
        self.conn.commit()

    def list_findings(
        self,
        wing_name: str | None = None,
        hall_name: str | None = None,
        pattern_name: str | None = None,
    ) -> list[dict[str, Any]]:
        query = "SELECT f.* FROM findings f"
        joins: list[str] = []
        conditions: list[str] = []
        params: list[Any] = []

        if wing_name:
            joins.append("JOIN wings w ON f.wing_id = w.wing_id")
            conditions.append("w.name = ?")
            params.append(wing_name)
        if hall_name:
            joins.append("JOIN halls h ON f.hall_id = h.hall_id")
            conditions.append("h.name = ?")
            params.append(hall_name)
        if pattern_name:
            conditions.append("f.pattern_name = ?")
            params.append(pattern_name)

        if joins:
            query += " " + " ".join(joins)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        return [dict(r) for r in self.conn.execute(query, params).fetchall()]

    # ── Tunnels ────────────────────────────────────────────────────────

    def add_tunnel(
        self,
        pattern_name: str,
        finding_id_1: int,
        finding_id_2: int,
        similarity_score: float,
        description: str | None = None,
        notes: str | None = None,
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO tunnels "
            "(pattern_name, description, finding_id_1, finding_id_2, "
            " similarity_score, notes) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (pattern_name, description, finding_id_1, finding_id_2,
             similarity_score, notes),
        )
        self.conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def list_tunnels(
        self, pattern_name: str | None = None
    ) -> list[dict[str, Any]]:
        if pattern_name:
            rows = self.conn.execute(
                "SELECT * FROM tunnels WHERE pattern_name = ?", (pattern_name,)
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM tunnels").fetchall()
        return [dict(r) for r in rows]

    # ── Human Feedback ─────────────────────────────────────────────────

    def add_feedback(
        self,
        finding_id: int,
        feedback_type: str,
        human_explanation: str,
        lesson_learned: str | None = None,
        advocatus_chromadb_id: str | None = None,
        requires_reframing: bool = False,
        reframing_tactic: str | None = None,
        outcome_after_reframe: str | None = None,
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO human_feedback "
            "(finding_id, feedback_type, human_explanation, lesson_learned, "
            " advocatus_chromadb_id, requires_reframing, reframing_tactic, "
            " outcome_after_reframe, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                finding_id,
                feedback_type,
                human_explanation,
                lesson_learned,
                advocatus_chromadb_id,
                requires_reframing,
                reframing_tactic,
                outcome_after_reframe,
                _now(),
            ),
        )
        self.conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    # ── Culture Profiles ───────────────────────────────────────────────

    def set_culture_profile(
        self,
        wing_name: str,
        verbosity_level: str = "moderate",
        formality: str = "professional",
        security_framing: str = "moderate",
        report_length_max_words: int | None = None,
        diff_required: bool = False,
        cvss_expected: bool = True,
        report_template: str | None = None,
    ) -> int:
        wing = self.get_wing(wing_name)
        if wing is None:
            raise ValueError(f"Wing '{wing_name}' not found")

        # Upsert
        existing = self.conn.execute(
            "SELECT profile_id FROM culture_profiles WHERE wing_id = ?",
            (wing["wing_id"],),
        ).fetchone()

        if existing:
            self.conn.execute(
                "UPDATE culture_profiles SET "
                "verbosity_level=?, formality=?, security_framing=?, "
                "report_length_max_words=?, diff_required=?, cvss_expected=?, "
                "report_template=?, updated_date=? "
                "WHERE profile_id=?",
                (
                    verbosity_level, formality, security_framing,
                    report_length_max_words, diff_required, cvss_expected,
                    report_template, _now(), existing["profile_id"],
                ),
            )
            self.conn.commit()
            return existing["profile_id"]

        cur = self.conn.execute(
            "INSERT INTO culture_profiles "
            "(wing_id, verbosity_level, formality, security_framing, "
            " report_length_max_words, diff_required, cvss_expected, "
            " report_template, updated_date) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                wing["wing_id"], verbosity_level, formality, security_framing,
                report_length_max_words, diff_required, cvss_expected,
                report_template, _now(),
            ),
        )
        self.conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def get_culture_profile(self, wing_name: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT cp.* FROM culture_profiles cp "
            "JOIN wings w ON cp.wing_id = w.wing_id "
            "WHERE w.name = ?",
            (wing_name,),
        ).fetchone()
        return dict(row) if row else None

    # ── Pattern Evolution ──────────────────────────────────────────────

    def add_pattern_evolution(
        self,
        pattern_name: str,
        change_description: str,
        fp_rate_before: float | None = None,
        fp_rate_after: float | None = None,
        lessons_applied: list[str] | None = None,
    ) -> int:
        # Get next version
        row = self.conn.execute(
            "SELECT MAX(version) as max_v FROM pattern_evolution WHERE pattern_name = ?",
            (pattern_name,),
        ).fetchone()
        next_version = (row["max_v"] or 0) + 1

        cur = self.conn.execute(
            "INSERT INTO pattern_evolution "
            "(pattern_name, version, false_positive_rate_before, "
            " false_positive_rate_after, change_description, "
            " lessons_applied, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                pattern_name,
                next_version,
                fp_rate_before,
                fp_rate_after,
                change_description,
                json.dumps(lessons_applied) if lessons_applied else None,
                _now(),
            ),
        )
        self.conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    # ── Statistics ─────────────────────────────────────────────────────

    def record_stat(
        self,
        metric_name: str,
        metric_value: float,
        wing_name: str | None = None,
        hall_name: str | None = None,
    ) -> int:
        wing_id = None
        if wing_name:
            wing = self.get_wing(wing_name)
            wing_id = wing["wing_id"] if wing else None

        hall_id = None
        if hall_name:
            hall = self.get_hall(hall_name)
            hall_id = hall["hall_id"] if hall else None

        cur = self.conn.execute(
            "INSERT INTO statistics "
            "(metric_name, metric_value, wing_id, hall_id, calculated_date) "
            "VALUES (?, ?, ?, ?, ?)",
            (metric_name, metric_value, wing_id, hall_id, _now()),
        )
        self.conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def get_stats(
        self,
        metric_name: str | None = None,
        wing_name: str | None = None,
    ) -> list[dict[str, Any]]:
        query = "SELECT s.* FROM statistics s"
        conditions: list[str] = []
        params: list[Any] = []

        if metric_name:
            conditions.append("s.metric_name = ?")
            params.append(metric_name)
        if wing_name:
            query += " JOIN wings w ON s.wing_id = w.wing_id"
            conditions.append("w.name = ?")
            params.append(wing_name)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY s.calculated_date DESC"

        return [dict(r) for r in self.conn.execute(query, params).fetchall()]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
