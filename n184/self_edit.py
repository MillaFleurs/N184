"""Guarded self-modification system for N184.

Ported from MyMilla's self_edit.clj. Provides a 5-layer safety model
for when agents need to modify their own soul files, detection patterns,
or palace data.

Safety layers (all must pass):
  1. Disabled by default — requires explicit SELF_EDIT_ENABLED=true
  2. Intent matching — caller must pass the correct intent string
  3. Path whitelisting — only allowed paths can be modified
  4. User confirmation — optional gate requiring HIL approval
  5. Atomic backups — timestamped .bak before any write

Origin: MyMilla (github.com/MillaFleurs/MyMilla) src/milla/self_edit.clj
License: AGPL-3.0
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path


class SelfEditError(Exception):
    """Raised when a self-edit safety check fails."""

    def __init__(self, reason: str, code: str) -> None:
        super().__init__(reason)
        self.code = code


# ── Configuration ─────────────────────────────────────────────────────

# All defaults are restrictive. Nothing edits anything unless explicitly allowed.
SELF_EDIT_ENABLED = os.environ.get("SELF_EDIT_ENABLED", "false").lower() == "true"
SELF_EDIT_INTENT = os.environ.get("SELF_EDIT_INTENT", "pattern-update")
SELF_EDIT_REQUIRE_CONFIRM = os.environ.get("SELF_EDIT_REQUIRE_CONFIRM", "true").lower() == "true"
SELF_EDIT_ALLOW_PATHS: list[str] = [
    p.strip()
    for p in os.environ.get("SELF_EDIT_ALLOW_PATHS", "souls,n184/patterns").split(",")
    if p.strip()
]

# Project root (for path canonicalization)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def enabled() -> bool:
    """Check if self-edit is enabled."""
    return SELF_EDIT_ENABLED


def _check_enabled() -> None:
    if not SELF_EDIT_ENABLED:
        raise SelfEditError(
            "Self-edit is disabled. Set SELF_EDIT_ENABLED=true to enable.",
            "self-edit-disabled",
        )


def _check_intent(intent: str) -> None:
    if intent != SELF_EDIT_INTENT:
        raise SelfEditError(
            f"Intent mismatch: got '{intent}', expected '{SELF_EDIT_INTENT}'",
            "intent-disallowed",
        )


def _check_path(file_path: str | Path) -> Path:
    """Canonicalize and validate a file path against the allowlist."""
    resolved = Path(file_path).resolve()

    # Must be under project root (prevent path traversal)
    try:
        resolved.relative_to(_PROJECT_ROOT)
    except ValueError:
        raise SelfEditError(
            f"Path '{file_path}' is outside project root",
            "path-outside-root",
        )

    # Must match an allowed prefix
    rel = str(resolved.relative_to(_PROJECT_ROOT))
    if not any(rel.startswith(prefix) for prefix in SELF_EDIT_ALLOW_PATHS):
        raise SelfEditError(
            f"Path '{rel}' not in allowed paths: {SELF_EDIT_ALLOW_PATHS}",
            "path-not-allowed",
        )

    return resolved


def _check_confirm(confirmed: bool) -> None:
    if SELF_EDIT_REQUIRE_CONFIRM and not confirmed:
        raise SelfEditError(
            "User confirmation required. Pass confirmed=True after HIL approval.",
            "confirmation-required",
        )


def _backup(file_path: Path) -> Path:
    """Create a timestamped backup of a file."""
    backup_path = file_path.with_suffix(f".bak.{int(time.time() * 1000)}")
    shutil.copy2(file_path, backup_path)
    return backup_path


# ── Public API ────────────────────────────────────────────────────────


def list_files() -> list[dict[str, str]]:
    """List all files under allowed paths.

    Returns list of {path, relative_path} dicts.
    """
    _check_enabled()
    results = []
    for prefix in SELF_EDIT_ALLOW_PATHS:
        base = _PROJECT_ROOT / prefix
        if base.is_dir():
            for f in sorted(base.rglob("*")):
                if f.is_file():
                    results.append({
                        "path": str(f),
                        "relative_path": str(f.relative_to(_PROJECT_ROOT)),
                    })
    return results


def read_file(file_path: str | Path) -> dict[str, str]:
    """Read a file from an allowed path.

    Returns {path, content}.
    """
    _check_enabled()
    resolved = _check_path(file_path)
    if not resolved.exists():
        raise SelfEditError(f"File not found: {file_path}", "file-not-found")
    return {
        "path": str(resolved.relative_to(_PROJECT_ROOT)),
        "content": resolved.read_text(encoding="utf-8"),
    }


def apply_replace(
    file_path: str | Path,
    old: str,
    new: str,
    intent: str,
    confirmed: bool = False,
) -> dict[str, str]:
    """Replace the first occurrence of `old` with `new` in a file.

    All five safety layers are checked:
      1. Self-edit must be enabled
      2. Intent must match configured intent
      3. Path must be in allowlist
      4. User confirmation required (if configured)
      5. Atomic backup created before write

    Returns {path, backup_path, status}.
    """
    # Layer 1: enabled
    _check_enabled()
    # Layer 2: intent
    _check_intent(intent)
    # Layer 3: path
    resolved = _check_path(file_path)
    # Layer 4: confirmation
    _check_confirm(confirmed)

    if not resolved.exists():
        raise SelfEditError(f"File not found: {file_path}", "file-not-found")

    content = resolved.read_text(encoding="utf-8")
    if old not in content:
        raise SelfEditError(
            f"Old text not found in {file_path}", "old-text-not-found"
        )

    # Layer 5: atomic backup
    backup_path = _backup(resolved)

    # Replace first occurrence only
    new_content = content.replace(old, new, 1)
    resolved.write_text(new_content, encoding="utf-8")

    return {
        "path": str(resolved.relative_to(_PROJECT_ROOT)),
        "backup_path": str(backup_path.relative_to(_PROJECT_ROOT)),
        "status": "replaced",
    }
