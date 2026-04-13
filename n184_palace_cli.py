#!/usr/bin/env python3
"""CLI wrapper for the N184 Memory Palace.

Exposes the N184MemoryPalace API as shell subcommands with JSON output,
designed for use by AI agents running inside NanoClaw containers.

Usage:
    n184-palace init
    n184-palace add-wing --name openbsd --repo-url https://github.com/openbsd/src
    n184-palace query --hall vulnerabilities --text "buffer overflow"
    n184-palace check-finding --code-snippet "memcpy(buf, input, len)"
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Ensure the n184_memory_palace package is importable.
#
# The package directory is called "n184" but internal imports use
# "n184_memory_palace".  We handle three scenarios:
#   1. Container: package installed in venv site-packages as n184_memory_palace/
#   2. Local dev: CLI lives next to the n184/ directory (repo root)
#   3. Local dev: CLI lives inside the n184/ directory
_script_dir = Path(__file__).resolve().parent

# Scenario 2: CLI is at repo root, package is in n184/ subdirectory.
# The directory is named "n184" but internal imports use "n184_memory_palace".
# Register the package under both names before anything tries to import it.
_n184_pkg = _script_dir / "n184"
if _n184_pkg.is_dir() and (_n184_pkg / "palace.py").exists():
    import types
    import importlib.util

    # Create a synthetic "n184_memory_palace" package that points to n184/
    spec = importlib.util.spec_from_file_location(
        "n184_memory_palace",
        _n184_pkg / "__init__.py",
        submodule_search_locations=[str(_n184_pkg)],
    )
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules["n184_memory_palace"] = mod
    # Load submodules before executing __init__ (which imports from them)
    for submod_name in ("config", "sqlite_store", "chromadb_store", "palace"):
        sub_path = _n184_pkg / f"{submod_name}.py"
        if sub_path.exists():
            sub_spec = importlib.util.spec_from_file_location(
                f"n184_memory_palace.{submod_name}", sub_path
            )
            sub_mod = importlib.util.module_from_spec(sub_spec)  # type: ignore[arg-type]
            sys.modules[f"n184_memory_palace.{submod_name}"] = sub_mod
            sub_spec.loader.exec_module(sub_mod)  # type: ignore[union-attr]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]

from n184_memory_palace.palace import N184MemoryPalace
from n184_memory_palace.config import HALLS


def _palace() -> N184MemoryPalace:
    return N184MemoryPalace()


def _json_out(obj: object) -> None:
    json.dump(obj, sys.stdout, indent=2, default=str)
    print()


# ── Subcommands ────────────────────────────────────────────────────────


def cmd_init(args: argparse.Namespace) -> None:
    """Create all tables, indexes, and ChromaDB collections."""
    palace = _palace()
    palace.initialize()
    _json_out({"status": "ok", "message": "Memory Palace initialized"})
    palace.close()


def cmd_add_wing(args: argparse.Namespace) -> None:
    palace = _palace()
    palace.initialize()
    wing_id = palace.add_wing(args.name, args.description, args.repo_url)
    _json_out({"status": "ok", "wing_id": wing_id, "name": args.name})
    palace.close()


def cmd_add_room(args: argparse.Namespace) -> None:
    palace = _palace()
    palace.initialize()
    room_id = palace.add_room(args.wing, args.name, args.description, args.file_path)
    _json_out({"status": "ok", "room_id": room_id, "wing": args.wing, "name": args.name})
    palace.close()


def cmd_add(args: argparse.Namespace) -> None:
    palace = _palace()
    palace.initialize()
    metadata = json.loads(args.metadata) if args.metadata else {}
    doc_id = palace.add_to_hall(
        hall_name=args.hall,
        document=args.document,
        metadata=metadata,
        wing=args.wing,
        room=args.room,
        pattern_name=args.pattern,
        severity=args.severity,
        discovered_by=args.discovered_by,
    )
    _json_out({"status": "ok", "doc_id": doc_id, "hall": args.hall})
    palace.close()


def cmd_query(args: argparse.Namespace) -> None:
    palace = _palace()
    palace.initialize()
    where = json.loads(args.where) if args.where else None
    results = palace.query_hall(args.hall, args.text, args.n_results, where)
    # Flatten for easier consumption
    flat = []
    if results.get("ids") and results["ids"][0]:
        ids = results["ids"][0]
        docs = results.get("documents", [[]])[0]
        metas = results.get("metadatas", [[]])[0]
        dists = results.get("distances", [[]])[0]
        for i, doc_id in enumerate(ids):
            flat.append({
                "id": doc_id,
                "document": docs[i] if i < len(docs) else None,
                "metadata": metas[i] if i < len(metas) else None,
                "distance": dists[i] if i < len(dists) else None,
            })
    _json_out({"status": "ok", "hall": args.hall, "count": len(flat), "results": flat})
    palace.close()


def cmd_query_multi(args: argparse.Namespace) -> None:
    palace = _palace()
    palace.initialize()
    hall_names = [h.strip() for h in args.halls.split(",")]
    results = palace.query_multi_hall(hall_names, args.text, args.n_results)
    _json_out({"status": "ok", "halls": hall_names, "results": results})
    palace.close()


def cmd_check_finding(args: argparse.Namespace) -> None:
    palace = _palace()
    palace.initialize()
    result = palace.check_finding(
        code_snippet=args.code_snippet,
        wing=args.wing,
        room=args.room,
        pattern_name=args.pattern,
    )
    _json_out({"status": "ok", **result})
    palace.close()


def cmd_feedback(args: argparse.Namespace) -> None:
    palace = _palace()
    palace.initialize()
    result = palace.record_feedback(
        finding_id=args.finding_id,
        feedback_type=args.type,
        human_explanation=args.explanation,
        lesson_learned=args.lesson,
        requires_reframing=args.reframe,
        reframing_tactic=args.reframe_tactic,
    )
    _json_out({"status": "ok", **result})
    palace.close()


def cmd_tunnel(args: argparse.Namespace) -> None:
    palace = _palace()
    palace.initialize()
    tunnel_id = palace.create_tunnel(
        pattern_name=args.pattern,
        finding_id_1=args.finding1,
        finding_id_2=args.finding2,
        similarity_score=args.similarity,
        description=args.description,
    )
    _json_out({"status": "ok", "tunnel_id": tunnel_id})
    palace.close()


def cmd_culture(args: argparse.Namespace) -> None:
    palace = _palace()
    palace.initialize()
    if args.get:
        profile = palace.get_culture_profile(args.wing)
        _json_out({"status": "ok", "wing": args.wing, "profile": profile})
    else:
        kwargs = {}
        if args.verbosity:
            kwargs["verbosity_level"] = args.verbosity
        if args.formality:
            kwargs["formality"] = args.formality
        if args.security_framing:
            kwargs["security_framing"] = args.security_framing
        profile_id = palace.set_culture_profile(args.wing, **kwargs)
        _json_out({"status": "ok", "wing": args.wing, "profile_id": profile_id})
    palace.close()


def cmd_stats(args: argparse.Namespace) -> None:
    palace = _palace()
    palace.initialize()
    stats = palace.get_stats(args.metric, args.wing)
    _json_out({"status": "ok", "count": len(stats), "stats": stats})
    palace.close()


def cmd_hall_counts(args: argparse.Namespace) -> None:
    palace = _palace()
    palace.initialize()
    counts = palace.hall_counts()
    total = sum(counts.values())
    _json_out({"status": "ok", "total": total, "halls": counts})
    palace.close()


def cmd_list_wings(args: argparse.Namespace) -> None:
    palace = _palace()
    palace.initialize()
    wings = palace.list_wings()
    _json_out({"status": "ok", "count": len(wings), "wings": wings})
    palace.close()


def cmd_list_findings(args: argparse.Namespace) -> None:
    palace = _palace()
    palace.initialize()
    findings = palace.sqlite.list_findings(
        wing_name=args.wing,
        hall_name=args.hall,
        pattern_name=args.pattern,
    )
    _json_out({"status": "ok", "count": len(findings), "findings": findings})
    palace.close()


def cmd_evolve_pattern(args: argparse.Namespace) -> None:
    palace = _palace()
    palace.initialize()
    lessons = json.loads(args.lessons) if args.lessons else None
    evo_id = palace.evolve_pattern(
        pattern_name=args.pattern,
        change_description=args.description,
        fp_rate_before=args.fp_before,
        fp_rate_after=args.fp_after,
        lessons_applied=lessons,
    )
    _json_out({"status": "ok", "evolution_id": evo_id})
    palace.close()


def cmd_record_stat(args: argparse.Namespace) -> None:
    palace = _palace()
    palace.initialize()
    stat_id = palace.record_stat(args.metric, args.value, args.wing, args.hall)
    _json_out({"status": "ok", "stat_id": stat_id})
    palace.close()


# ── Parser ─────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="n184-palace",
        description="CLI interface to the N184 Memory Palace",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # init
    sub.add_parser("init", help="Initialize the Memory Palace (tables + collections)")

    # add-wing
    p = sub.add_parser("add-wing", help="Register a new codebase wing")
    p.add_argument("--name", required=True)
    p.add_argument("--description", default=None)
    p.add_argument("--repo-url", default=None)

    # add-room
    p = sub.add_parser("add-room", help="Register a component room within a wing")
    p.add_argument("--wing", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--description", default=None)
    p.add_argument("--file-path", default=None)

    # add
    p = sub.add_parser("add", help="Add a document to a hall")
    p.add_argument("--hall", required=True, choices=list(HALLS.keys()))
    p.add_argument("--document", required=True)
    p.add_argument("--wing", default=None)
    p.add_argument("--room", default=None)
    p.add_argument("--pattern", default=None)
    p.add_argument("--severity", default=None, choices=["critical", "high", "medium", "low", "info"])
    p.add_argument("--discovered-by", default=None)
    p.add_argument("--metadata", default=None, help="JSON string of metadata")

    # query
    p = sub.add_parser("query", help="Query a hall by semantic similarity")
    p.add_argument("--hall", required=True, choices=list(HALLS.keys()))
    p.add_argument("--text", required=True)
    p.add_argument("--n-results", type=int, default=5)
    p.add_argument("--where", default=None, help="JSON metadata filter")

    # query-multi
    p = sub.add_parser("query-multi", help="Query multiple halls")
    p.add_argument("--halls", required=True, help="Comma-separated hall names")
    p.add_argument("--text", required=True)
    p.add_argument("--n-results", type=int, default=5)

    # check-finding
    p = sub.add_parser("check-finding", help="Pre-report confidence check")
    p.add_argument("--code-snippet", required=True)
    p.add_argument("--wing", default=None)
    p.add_argument("--room", default=None)
    p.add_argument("--pattern", default=None)

    # feedback
    p = sub.add_parser("feedback", help="Record human feedback on a finding")
    p.add_argument("--finding-id", type=int, required=True)
    p.add_argument("--type", required=True, choices=["confirmed", "false_positive", "needs_context", "reframe"])
    p.add_argument("--explanation", required=True)
    p.add_argument("--lesson", default=None)
    p.add_argument("--reframe", action="store_true", default=False)
    p.add_argument("--reframe-tactic", default=None)

    # tunnel
    p = sub.add_parser("tunnel", help="Link a pattern across two codebases")
    p.add_argument("--pattern", required=True)
    p.add_argument("--finding1", type=int, required=True)
    p.add_argument("--finding2", type=int, required=True)
    p.add_argument("--similarity", type=float, required=True)
    p.add_argument("--description", default=None)

    # culture
    p = sub.add_parser("culture", help="Get or set culture profile for a wing")
    p.add_argument("--wing", required=True)
    p.add_argument("--get", action="store_true", default=False)
    p.add_argument("--verbosity", choices=["minimal", "moderate", "verbose"], default=None)
    p.add_argument("--formality", choices=["casual", "professional", "academic"], default=None)
    p.add_argument("--security-framing", choices=["avoid", "moderate", "required"], default=None)

    # stats
    p = sub.add_parser("stats", help="Get statistics")
    p.add_argument("--metric", default=None)
    p.add_argument("--wing", default=None)

    # hall-counts
    sub.add_parser("hall-counts", help="Show document counts per hall")

    # list-wings
    sub.add_parser("list-wings", help="List all registered wings")

    # list-findings
    p = sub.add_parser("list-findings", help="List findings")
    p.add_argument("--wing", default=None)
    p.add_argument("--hall", default=None)
    p.add_argument("--pattern", default=None)

    # evolve-pattern
    p = sub.add_parser("evolve-pattern", help="Record pattern evolution")
    p.add_argument("--pattern", required=True)
    p.add_argument("--description", required=True)
    p.add_argument("--fp-before", type=float, default=None)
    p.add_argument("--fp-after", type=float, default=None)
    p.add_argument("--lessons", default=None, help="JSON array of lesson strings")

    # record-stat
    p = sub.add_parser("record-stat", help="Record a metric")
    p.add_argument("--metric", required=True)
    p.add_argument("--value", type=float, required=True)
    p.add_argument("--wing", default=None)
    p.add_argument("--hall", default=None)

    return parser


COMMANDS = {
    "init": cmd_init,
    "add-wing": cmd_add_wing,
    "add-room": cmd_add_room,
    "add": cmd_add,
    "query": cmd_query,
    "query-multi": cmd_query_multi,
    "check-finding": cmd_check_finding,
    "feedback": cmd_feedback,
    "tunnel": cmd_tunnel,
    "culture": cmd_culture,
    "stats": cmd_stats,
    "hall-counts": cmd_hall_counts,
    "list-wings": cmd_list_wings,
    "list-findings": cmd_list_findings,
    "evolve-pattern": cmd_evolve_pattern,
    "record-stat": cmd_record_stat,
}


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    handler = COMMANDS.get(args.command)
    if handler is None:
        parser.print_help()
        sys.exit(1)
    try:
        handler(args)
    except Exception as e:
        _json_out({"status": "error", "error": str(e), "type": type(e).__name__})
        sys.exit(1)


if __name__ == "__main__":
    main()
