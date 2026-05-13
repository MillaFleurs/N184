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
from n184_memory_palace.config import (
    CHROMADB_PATH,
    HALLS,
    N184_HOME,
    SQLITE_DB_PATH,
)
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

        # Bug shapes (distilled patterns with explicit signal direction).
        # Positive shapes boost confidence, negative shapes veto strongly,
        # conditional ones add a warning so Honoré has to think about it.
        matching_shapes = self._match_shapes(
            code_snippet=code_snippet,
            pattern_name=pattern_name,
            wing=wing,
        )
        shape_summaries: list[dict[str, Any]] = []
        for shape in matching_shapes:
            shape_summaries.append({
                "shape_id": shape["shape_id"],
                "name": shape["name"],
                "signal": shape["signal"],
                "criteria_text": shape["criteria_text"],
                "rationale": shape.get("rationale"),
            })
            self.sqlite.increment_shape_match(shape["shape_id"])
            if shape["signal"] == "negative":
                # Strong veto — usually larger than the ChromaDB-similarity FP penalty
                # because shapes are HIL-confirmed (lower false-veto rate).
                confidence_delta -= 0.6
                warnings.append(
                    f"NEGATIVE shape match: {shape['name']} — "
                    f"{shape['criteria_text']}. "
                    f"Reject unless you can argue past the shape's rationale."
                )
            elif shape["signal"] == "positive":
                confidence_delta += 0.3
                warnings.append(
                    f"POSITIVE shape match: {shape['name']} — "
                    f"this kind of finding tends to be real. "
                    f"Criteria: {shape['criteria_text']}"
                )
            else:  # conditional
                warnings.append(
                    f"CONDITIONAL shape match: {shape['name']} — "
                    f"signal depends on context. Criteria: {shape['criteria_text']}"
                )

        return {
            "confidence_delta": confidence_delta,
            "similar_fps": _extract_results(fp_results),
            "similar_archaeology": _extract_results(archaeology_results),
            "similar_vulns": _extract_results(vuln_results),
            "matching_shapes": shape_summaries,
            "warnings": warnings,
        }

    def _match_shapes(
        self,
        code_snippet: str,
        pattern_name: str | None,
        wing: str | None,
    ) -> list[dict[str, Any]]:
        """Find confirmed shapes whose criteria match this finding.

        Matching is intentionally simple: substring + pattern_name. The
        signal direction is what does the heavy lifting, not the matcher.
        If a deployment needs fuzzy matching the shape criteria already
        live as text and Lousteau can be asked to add a semantic match
        layer; we don't bake that in here to keep the dependency surface
        small.
        """
        shapes = self.sqlite.list_shapes(
            wing_name=wing,
            status="confirmed",
            include_cross_wing=True,
        )
        if not shapes:
            return []

        snippet_lower = code_snippet.lower() if code_snippet else ""
        pattern_lower = (pattern_name or "").lower()
        # Stop-list of generic words that produce nuisance matches.
        # Anything in here is excluded from the criteria token list even if
        # it's long enough. Add to this when you see drift in practice.
        STOP_TOKENS = {
            "check", "checks", "checked", "validation", "size", "value",
            "result", "without", "against", "comparing", "limits",
        }
        hits: list[dict[str, Any]] = []
        for shape in shapes:
            name_lower = shape["name"].lower()
            criteria_lower = shape["criteria_text"].lower()
            tokens = [
                t for t in criteria_lower.split()
                if len(t) > 4 and t not in STOP_TOKENS
            ]
            # Strongest signal: caller's pattern_name matches the shape's
            # own name. This is the canonical Lousteau workflow ("the
            # finding's pattern_name is *literally the shape name*").
            if pattern_lower and (
                pattern_lower == name_lower
                or pattern_lower in name_lower
                or name_lower in pattern_lower
            ):
                hits.append(shape)
                continue
            # Otherwise check distinctive criteria tokens against the snippet.
            if snippet_lower and any(t in snippet_lower for t in tokens):
                hits.append(shape)
        return hits

    # ── Bug Shapes ─────────────────────────────────────────────────────

    def propose_shape(
        self,
        name: str,
        signal: str,
        criteria_text: str,
        rationale: str | None = None,
        exemplar_finding_id: int | None = None,
        wing: str | None = None,
        proposed_by: str = "lousteau",
    ) -> int:
        """Agent-side entry point: register a candidate shape for HIL review.

        Lousteau calls this during post-mortem when it sees N near-identical
        dispositions (e.g., three findings the HIL marked as 'Miss' that
        all share the same root cause). Confirmed via palace.confirm_shape
        once the HIL agrees.
        """
        return self.sqlite.propose_shape(
            name=name,
            signal=signal,
            criteria_text=criteria_text,
            rationale=rationale,
            exemplar_finding_id=exemplar_finding_id,
            wing_name=wing,
            proposed_by=proposed_by,
        )

    def confirm_shape(self, shape_id: int, hil_name: str) -> None:
        self.sqlite.confirm_shape(shape_id, hil_name)

    def retire_shape(self, shape_id: int) -> None:
        self.sqlite.retire_shape(shape_id)

    def list_shapes(
        self,
        wing: str | None = None,
        signal: str | None = None,
        status: str | None = "confirmed",
    ) -> list[dict[str, Any]]:
        return self.sqlite.list_shapes(
            wing_name=wing, signal=signal, status=status
        )

    def add_shape_edge(
        self,
        from_shape_id: int,
        to_shape_id: int,
        kind: str,
        note: str | None = None,
    ) -> int:
        return self.sqlite.add_shape_edge(
            from_shape_id, to_shape_id, kind, note
        )

    def regenerate_potstill(
        self,
        wing: str | None = None,
        output_path: Path | None = None,
    ) -> Path:
        """Distill confirmed shapes into a markdown file agents read at the
        start of analysis.

        The file is a *derived view* — never edit it by hand. The SQLite
        shape graph is the source of truth; this file exists because LLMs
        absorb prose faster than they query databases.

        Default output paths:
            wing=None:           ~/.n184/potstill.md
            wing="openbsd-rpki": ~/.n184/wings/openbsd-rpki/potstill.md
        """
        if output_path is None:
            if wing:
                output_path = N184_HOME / "wings" / wing / "potstill.md"
            else:
                output_path = N184_HOME / "potstill.md"
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        shapes = self.sqlite.list_shapes(
            wing_name=wing, status="confirmed", include_cross_wing=True
        )
        # Group by signal so agents see vetoes first (highest leverage).
        groups: dict[str, list[dict[str, Any]]] = {
            "negative": [], "positive": [], "conditional": []
        }
        for shape in shapes:
            groups[shape["signal"]].append(shape)

        scope = wing if wing else "all codebases"
        generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        lines: list[str] = []
        lines.append(f"# Potstill — distilled bug shapes ({scope})")
        lines.append("")
        lines.append(
            "_Auto-generated by Lousteau from the Memory Palace shape graph. "
            "Do not edit by hand — edits will be overwritten. To change a shape, "
            "use `n184-palace confirm-shape` / `retire-shape` and re-run "
            "`regenerate-potstill`._"
        )
        lines.append("")
        lines.append(f"Generated: {generated_at}")
        lines.append(f"Confirmed shapes: {len(shapes)}")
        lines.append("")

        def fmt_shape(s: dict[str, Any]) -> str:
            wing_tag = f" [{s['wing_name']}]" if s.get("wing_name") else " [all codebases]"
            count = s.get("match_count") or 0
            rationale = f"\n  - Why: {s['rationale']}" if s.get("rationale") else ""
            return (
                f"- **{s['name']}**{wing_tag} (matched {count}× to date)\n"
                f"  - Criteria: {s['criteria_text']}{rationale}"
            )

        if groups["negative"]:
            lines.append("## NEGATIVE — reject findings matching these")
            lines.append("")
            for s in groups["negative"]:
                lines.append(fmt_shape(s))
            lines.append("")
        if groups["positive"]:
            lines.append("## POSITIVE — these findings tend to be real")
            lines.append("")
            for s in groups["positive"]:
                lines.append(fmt_shape(s))
            lines.append("")
        if groups["conditional"]:
            lines.append("## CONDITIONAL — signal depends on context")
            lines.append("")
            for s in groups["conditional"]:
                lines.append(fmt_shape(s))
            lines.append("")

        if not shapes:
            lines.append("_No confirmed shapes yet for this scope._")
            lines.append("")

        output_path.write_text("\n".join(lines))
        return output_path

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
