"""N184 Memory Palace - Unified facade for the knowledge store.

Combines SQLite (relational knowledge graph) with ChromaDB (vector similarity)
to provide the full memory palace API used by Honore and the N184 agent swarm.

Architecture:
    Wing  = Codebase (openbsd, linux, llama.cpp, mlx, ...)
    Room  = Component (rpki-client, http.c, malloc.c, ...)
    Hall  = Knowledge Type (the Seven Halls)
    Tunnel = Cross-codebase pattern link
    Closet = Summary pointer (finding row in SQLite)
    Drawer = Verbatim storage (document in ChromaDB)
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from n184_memory_palace.chromadb_store import ChromaDBStore
from n184_memory_palace.config import CHROMADB_PATH, HALLS, SQLITE_DB_PATH
from n184_memory_palace.sqlite_store import SQLiteStore


class N184MemoryPalace:
    """Unified interface to the N184 Memory Palace.

    Ties the SQLite knowledge graph (wings, rooms, findings, tunnels,
    culture profiles, feedback) to the ChromaDB vector store (the seven
    halls of verbatim documents).
    """

    def __init__(
        self,
        sqlite_path: Path | str | None = None,
        chromadb_path: Path | str | None = None,
    ) -> None:
        self.sqlite = SQLiteStore(sqlite_path or SQLITE_DB_PATH)
        self.chromadb = ChromaDBStore(chromadb_path or CHROMADB_PATH)

    def initialize(self) -> None:
        """Create all tables, indexes, and ChromaDB collections."""
        self.sqlite.initialize()
        self.chromadb.initialize()

    def close(self) -> None:
        self.sqlite.close()

    # ── Wing / Room Management ─────────────────────────────────────────

    def add_wing(
        self,
        name: str,
        description: str | None = None,
        repository_url: str | None = None,
    ) -> int:
        """Register a new codebase (wing) in the palace."""
        return self.sqlite.add_wing(name, description, repository_url)

    def add_room(
        self,
        wing: str,
        name: str,
        description: str | None = None,
        file_path: str | None = None,
    ) -> int:
        """Register a new component (room) within a wing."""
        return self.sqlite.add_room(wing, name, description, file_path)

    def get_wing(self, name: str) -> dict[str, Any] | None:
        return self.sqlite.get_wing(name)

    def list_wings(self) -> list[dict[str, Any]]:
        return self.sqlite.list_wings()

    # ── Adding Knowledge ───────────────────────────────────────────────

    def add_to_hall(
        self,
        hall_name: str,
        document: str,
        metadata: dict[str, Any],
        doc_id: str | None = None,
        wing: str | None = None,
        room: str | None = None,
        pattern_name: str | None = None,
        severity: str | None = None,
        discovered_by: str | None = None,
    ) -> str:
        """Add a document to a hall and register the finding in SQLite.

        Args:
            hall_name: One of the seven halls (e.g., "vulnerabilities").
            document: Full verbatim content (code, reasoning, dialogue).
            metadata: ChromaDB metadata dict (wing, room, tags, etc.).
            doc_id: Optional explicit ID. Auto-generated if omitted.
            wing: Wing name for SQLite linkage. Falls back to metadata["wing"].
            room: Room name for SQLite linkage. Falls back to metadata["room"].
            pattern_name: Pattern classification.
            severity: Finding severity level.
            discovered_by: Agent that discovered this (honore, vautrin, etc.).

        Returns:
            The ChromaDB document ID.
        """
        # Resolve wing/room from metadata if not explicit
        wing = wing or metadata.get("wing")
        room = room or metadata.get("room")

        # Generate ID if not provided
        if doc_id is None:
            prefix = hall_name[:4]
            ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
            doc_id = f"{prefix}_{ts}"

        # Ensure wing exists
        if wing and not self.sqlite.get_wing(wing):
            self.sqlite.add_wing(wing)

        # Store in ChromaDB (verbatim document + metadata)
        self.chromadb.add(hall_name, doc_id, document, metadata)

        # Register in SQLite knowledge graph (the "closet" pointer)
        if wing:
            self.sqlite.add_finding(
                wing_name=wing,
                room_name=room,
                hall_name=hall_name,
                chromadb_id=doc_id,
                pattern_name=pattern_name or metadata.get("pattern_name"),
                severity=severity or metadata.get("severity"),
                discovered_by=discovered_by or metadata.get("discovered_by"),
            )

        return doc_id

    # ── Querying Knowledge ─────────────────────────────────────────────

    def query_hall(
        self,
        hall_name: str,
        query_text: str,
        n_results: int = 5,
        where: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Query a single hall by semantic similarity.

        This is the primary retrieval method. Use metadata filters
        (wing, room, pattern_name, etc.) to narrow results.

        Returns ChromaDB query result with ids, documents, metadatas, distances.
        """
        return self.chromadb.query(hall_name, query_text, n_results, where)

    def query_multi_hall(
        self,
        hall_names: list[str],
        query_text: str,
        n_results: int = 5,
        where: dict[str, Any] | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Query multiple halls simultaneously.

        Returns results keyed by hall name.
        """
        return self.chromadb.multi_hall_query(hall_names, query_text, n_results, where)

    def check_finding(
        self,
        code_snippet: str,
        wing: str | None = None,
        room: str | None = None,
        pattern_name: str | None = None,
    ) -> dict[str, Any]:
        """Pre-report check: query memory palace before reporting a finding.

        Checks Advocatus Diaboli (false positive lessons), Git Archaeology
        (historical patterns), and Vulnerabilities (known CVEs) to compute
        a confidence adjustment.

        Returns:
            dict with keys:
                confidence_delta: float adjustment to baseline confidence
                similar_fps: list of similar false-positive lessons
                similar_archaeology: list of matching historical patterns
                similar_vulns: list of matching known vulnerabilities
                warnings: list of human-readable warning strings
        """
        where_filter: dict[str, Any] | None = None
        if wing and room:
            where_filter = {"$and": [{"wing": wing}, {"room": room}]}
        elif wing:
            where_filter = {"wing": wing}

        # Query the three relevant halls
        fp_results = self.chromadb.query(
            "advocatus_diaboli", code_snippet, n_results=3, where=where_filter
        )
        archaeology_results = self.chromadb.query(
            "git_archaeology", code_snippet, n_results=3, where=where_filter
        )
        vuln_results = self.chromadb.query(
            "vulnerabilities", code_snippet, n_results=3, where=where_filter
        )

        confidence_delta = 0.0
        warnings: list[str] = []

        # Check for similar false positives
        if fp_results["distances"] and fp_results["distances"][0]:
            best_fp_dist = fp_results["distances"][0][0]
            if best_fp_dist < 0.15:
                confidence_delta -= 0.4
                meta = fp_results["metadatas"][0][0] if fp_results["metadatas"][0] else {}
                warnings.append(
                    f"High similarity to known false positive "
                    f"(distance={best_fp_dist:.3f}, "
                    f"lesson={meta.get('lesson_type', 'unknown')})"
                )
            elif best_fp_dist < 0.30:
                confidence_delta -= 0.2
                warnings.append(
                    f"Moderate similarity to known false positive "
                    f"(distance={best_fp_dist:.3f})"
                )

        # Check for matching historical bug patterns
        if archaeology_results["distances"] and archaeology_results["distances"][0]:
            best_arch_dist = archaeology_results["distances"][0][0]
            if best_arch_dist < 0.10:
                confidence_delta += 0.4
                meta = (
                    archaeology_results["metadatas"][0][0]
                    if archaeology_results["metadatas"][0]
                    else {}
                )
                warnings.append(
                    f"Matches historical bug fix pattern "
                    f"(distance={best_arch_dist:.3f}, "
                    f"commit={meta.get('commit_hash', 'unknown')})"
                )
            elif best_arch_dist < 0.20:
                confidence_delta += 0.2
                warnings.append(
                    f"Moderate similarity to historical bug pattern "
                    f"(distance={best_arch_dist:.3f})"
                )

        # Check for matching known vulnerabilities
        if vuln_results["distances"] and vuln_results["distances"][0]:
            best_vuln_dist = vuln_results["distances"][0][0]
            if best_vuln_dist < 0.15:
                confidence_delta += 0.3
                meta = (
                    vuln_results["metadatas"][0][0]
                    if vuln_results["metadatas"][0]
                    else {}
                )
                warnings.append(
                    f"Similar to known vulnerability "
                    f"(distance={best_vuln_dist:.3f}, "
                    f"cve={meta.get('cve_id', 'unknown')})"
                )

        return {
            "confidence_delta": confidence_delta,
            "similar_fps": _extract_results(fp_results),
            "similar_archaeology": _extract_results(archaeology_results),
            "similar_vulns": _extract_results(vuln_results),
            "warnings": warnings,
        }

    # ── Culture ────────────────────────────────────────────────────────

    def get_culture_profile(self, wing: str) -> dict[str, Any] | None:
        """Get the culture profile for a codebase wing.

        Checks both SQLite (structured profile) and ChromaDB (detailed notes).
        """
        profile = self.sqlite.get_culture_profile(wing)
        # Also pull any culture hall documents for this wing
        culture_docs = self.chromadb.query(
            "culture",
            f"How to report bugs to {wing}",
            n_results=1,
            where={"wing": wing},
        )
        if profile and culture_docs["documents"] and culture_docs["documents"][0]:
            profile["culture_notes"] = culture_docs["documents"][0][0]
        return profile

    def set_culture_profile(self, wing: str, **kwargs: Any) -> int:
        """Set or update the culture profile for a wing."""
        return self.sqlite.set_culture_profile(wing, **kwargs)

    # ── Human Feedback (HIL Loop) ──────────────────────────────────────

    def record_feedback(
        self,
        finding_id: int,
        feedback_type: str,
        human_explanation: str,
        lesson_learned: str | None = None,
        requires_reframing: bool = False,
        reframing_tactic: str | None = None,
    ) -> dict[str, Any]:
        """Record human feedback and optionally store lesson in Advocatus Diaboli.

        Returns dict with feedback_id and optional advocatus_chromadb_id.
        """
        advocatus_id = None

        # If there's a lesson, store it in the Advocatus Diaboli hall
        if lesson_learned:
            finding = self.sqlite.get_finding(finding_id)
            wing = None
            if finding and finding["wing_id"]:
                wings = self.sqlite.list_wings()
                for w in wings:
                    if w["wing_id"] == finding["wing_id"]:
                        wing = w["name"]
                        break

            advocatus_id = self.add_to_hall(
                hall_name="advocatus_diaboli",
                document=f"{human_explanation}\n\nLesson: {lesson_learned}",
                metadata={
                    "lesson_type": feedback_type,
                    "wing": wing or "",
                    "finding_id": finding_id,
                    "applies_to": [],
                    "reinforcement_count": 0,
                },
                discovered_by="human",
            )

        # If reframing needed, also store in Avocado Smash
        if requires_reframing and reframing_tactic:
            self.add_to_hall(
                hall_name="avocado_smash",
                document=f"Reframing needed: {human_explanation}\n\nTactic: {reframing_tactic}",
                metadata={
                    "tactic": reframing_tactic,
                    "outcome": "pending",
                    "finding_id": finding_id,
                },
                discovered_by="human",
            )

        # Record in SQLite
        fb_id = self.sqlite.add_feedback(
            finding_id=finding_id,
            feedback_type=feedback_type,
            human_explanation=human_explanation,
            lesson_learned=lesson_learned,
            advocatus_chromadb_id=advocatus_id,
            requires_reframing=requires_reframing,
            reframing_tactic=reframing_tactic,
        )

        # Mark finding as false positive if applicable
        if feedback_type == "false_positive":
            self.sqlite.update_finding(finding_id, false_positive=True)
        elif feedback_type == "confirmed":
            self.sqlite.update_finding(finding_id, confirmed=True)

        return {"feedback_id": fb_id, "advocatus_chromadb_id": advocatus_id}

    # ── Tunnels (Cross-Codebase Patterns) ──────────────────────────────

    def create_tunnel(
        self,
        pattern_name: str,
        finding_id_1: int,
        finding_id_2: int,
        similarity_score: float,
        description: str | None = None,
        notes: str | None = None,
    ) -> int:
        """Create a tunnel linking the same pattern across two codebases."""
        return self.sqlite.add_tunnel(
            pattern_name, finding_id_1, finding_id_2,
            similarity_score, description, notes,
        )

    def find_cross_codebase_patterns(
        self,
        code_snippet: str,
        n_results: int = 5,
    ) -> dict[str, Any]:
        """Search for the same pattern across all codebases via Git Archaeology."""
        return self.chromadb.query(
            "git_archaeology",
            code_snippet,
            n_results=n_results,
            where={"cross_codebase_potential": True},
        )

    # ── Statistics & Metrics ───────────────────────────────────────────

    def hall_counts(self) -> dict[str, int]:
        """Return document counts for all seven halls."""
        return self.chromadb.counts()

    def record_stat(
        self,
        metric_name: str,
        value: float,
        wing: str | None = None,
        hall: str | None = None,
    ) -> int:
        return self.sqlite.record_stat(metric_name, value, wing, hall)

    def get_stats(
        self,
        metric_name: str | None = None,
        wing: str | None = None,
    ) -> list[dict[str, Any]]:
        return self.sqlite.get_stats(metric_name, wing)

    # ── Pattern Evolution ──────────────────────────────────────────────

    def evolve_pattern(
        self,
        pattern_name: str,
        change_description: str,
        fp_rate_before: float | None = None,
        fp_rate_after: float | None = None,
        lessons_applied: list[str] | None = None,
    ) -> int:
        return self.sqlite.add_pattern_evolution(
            pattern_name, change_description,
            fp_rate_before, fp_rate_after, lessons_applied,
        )


def _extract_results(query_result: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten ChromaDB batch results into a list of dicts."""
    results = []
    if not query_result.get("ids") or not query_result["ids"][0]:
        return results
    ids = query_result["ids"][0]
    docs = query_result.get("documents", [[]])[0]
    metas = query_result.get("metadatas", [[]])[0]
    dists = query_result.get("distances", [[]])[0]
    for i, doc_id in enumerate(ids):
        results.append({
            "id": doc_id,
            "document": docs[i] if i < len(docs) else None,
            "metadata": metas[i] if i < len(metas) else None,
            "distance": dists[i] if i < len(dists) else None,
        })
    return results
