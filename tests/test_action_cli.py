"""Smoke tests for the ./action CLI.

These tests exercise argument parsing, verb dispatch, and the error paths
without actually invoking the `claude` CLI or pushing to Redis. They are
designed to catch regressions in the CLI surface area: wrong verb names,
missing target, mode dispatch, error messages.

Run with:
    python3 -m unittest tests/test_action_cli.py
or any test runner that picks up unittest.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
ACTION_PATH = REPO_ROOT / "action"


def _load_action_module():
    """Load ./action as a Python module so we can call its functions directly.

    The file has no .py extension, so spec_from_file_location can't infer
    a loader from the suffix — we pass SourceFileLoader explicitly.
    """
    from importlib.machinery import SourceFileLoader

    loader = SourceFileLoader("action_cli", str(ACTION_PATH))
    spec = importlib.util.spec_from_loader("action_cli", loader)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class ActionCLISubprocessTests(unittest.TestCase):
    """Black-box tests: invoke ./action as a subprocess.

    These guarantee the script is executable, the shebang resolves, and
    the argument parser produces the error messages we promise users.
    """

    def test_help_lists_every_verb(self) -> None:
        result = subprocess.run(
            [str(ACTION_PATH), "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        for verb in (
            "--pull-the-thread",
            "--reconnoiter",
            "--hunt",
            "--consult-docs",
            "--remember",
        ):
            self.assertIn(verb, result.stdout, f"help missing verb {verb}")

    def test_help_mentions_fil_de_soie(self) -> None:
        result = subprocess.run(
            [str(ACTION_PATH), "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertIn("Fil-de-Soie", result.stdout)

    def test_no_verb_is_an_error(self) -> None:
        result = subprocess.run(
            [str(ACTION_PATH), "--target", "/tmp"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no verb specified", result.stderr)

    def test_multiple_verbs_is_an_error(self) -> None:
        result = subprocess.run(
            [str(ACTION_PATH), "--pull-the-thread", "--hunt", "--target", "/tmp"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("more than one verb", result.stderr)

    def test_missing_target_is_an_error(self) -> None:
        result = subprocess.run(
            [str(ACTION_PATH), "--pull-the-thread"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertNotEqual(result.returncode, 0)
        # argparse default message for a missing required arg
        self.assertIn("--target", result.stderr)


class ActionCLIInternalsTests(unittest.TestCase):
    """White-box tests: import action as a module and call internals."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.action = _load_action_module()

    def test_verb_registry_well_formed(self) -> None:
        for flag, meta in self.action.VERBS.items():
            self.assertIn("agent", meta, f"{flag} missing 'agent'")
            self.assertIn("summary", meta, f"{flag} missing 'summary'")
            self.assertIn("default_prompt", meta, f"{flag} missing 'default_prompt'")
            self.assertTrue(meta["default_prompt"].strip(), f"{flag} prompt is empty")
            # Flag should be kebab-case (no spaces, lowercase)
            self.assertEqual(flag, flag.lower())
            self.assertNotIn(" ", flag)

    def test_pull_the_thread_routes_to_fil_de_soie(self) -> None:
        self.assertEqual(
            self.action.VERBS["pull-the-thread"]["agent"], "fil-de-soie"
        )

    def test_every_verb_has_a_soul_file(self) -> None:
        """If a verb is in the registry, its soul must exist on disk.

        Catches the case where someone adds a verb without adding the
        soul, which would 500 at runtime instead of at test time.
        """
        for flag, meta in self.action.VERBS.items():
            soul = self.action.SOULS_DIR / f"claude-{meta['agent']}.md"
            self.assertTrue(
                soul.is_file(),
                f"verb --{flag} points to {meta['agent']} but "
                f"{soul} does not exist",
            )

    def test_make_scan_id_uses_override(self) -> None:
        scan_id = self.action.make_scan_id(
            "fil-de-soie", Path("/tmp/foo"), "manual-id-123"
        )
        self.assertEqual(scan_id, "manual-id-123")

    def test_make_scan_id_includes_agent_and_target_name(self) -> None:
        scan_id = self.action.make_scan_id(
            "fil-de-soie", Path("/tmp/widget-repo"), None
        )
        self.assertTrue(scan_id.startswith("fil-de-soie-widget-repo-"))

    def test_pick_verb_rejects_no_selection(self) -> None:
        # Build a namespace with all verbs False (mimicking no flag passed)
        parser = self.action.build_parser()
        with self.assertRaises(SystemExit):
            args = parser.parse_args(["--target", "/tmp"])
            self.action.pick_verb(args)

    def test_pick_verb_returns_single_selection(self) -> None:
        parser = self.action.build_parser()
        args = parser.parse_args(["--pull-the-thread", "--target", "/tmp"])
        flag, meta = self.action.pick_verb(args)
        self.assertEqual(flag, "pull-the-thread")
        self.assertEqual(meta["agent"], "fil-de-soie")

    def test_pick_verb_rejects_multiple_selections(self) -> None:
        parser = self.action.build_parser()
        args = parser.parse_args(
            ["--pull-the-thread", "--hunt", "--target", "/tmp"]
        )
        with self.assertRaises(SystemExit):
            self.action.pick_verb(args)


class ActionCLILocalModeTests(unittest.TestCase):
    """Verify local mode without actually launching the claude CLI."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.action = _load_action_module()

    def test_local_mode_errors_when_claude_missing(self) -> None:
        """If `claude` is not on PATH, local mode must fail with a clear message."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.object(self.action.shutil, "which", return_value=None):
                with self.assertRaises(SystemExit) as cm:
                    self.action.run_local(
                        agent="fil-de-soie",
                        flag="pull-the-thread",
                        soul_path=self.action.SOULS_DIR
                        / "claude-fil-de-soie.md",
                        target=Path(tmpdir),
                        scan_id="test-scan",
                        prompt="test prompt",
                    )
                self.assertIn("claude", str(cm.exception).lower())

    def test_local_mode_errors_when_target_not_a_directory(self) -> None:
        """A nonexistent target must produce a clear error, not a silent crash."""
        with mock.patch.object(
            self.action.shutil, "which", return_value="/usr/bin/claude"
        ):
            with self.assertRaises(SystemExit) as cm:
                self.action.run_local(
                    agent="fil-de-soie",
                    flag="pull-the-thread",
                    soul_path=self.action.SOULS_DIR
                    / "claude-fil-de-soie.md",
                    target=Path("/nonexistent/path/should/not/exist/anywhere"),
                    scan_id="test-scan",
                    prompt="test prompt",
                )
            self.assertIn("not a directory", str(cm.exception))

    def test_local_mode_passes_soul_and_handoff_to_claude(self) -> None:
        """Verify the claude subprocess gets the right arguments."""
        import io
        from contextlib import redirect_stdout

        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.object(
                self.action.shutil, "which", return_value="/fake/claude"
            ), mock.patch.object(self.action.subprocess, "run") as mock_run, \
                    redirect_stdout(io.StringIO()):
                mock_run.return_value = mock.Mock(returncode=0)
                self.action.run_local(
                    agent="fil-de-soie",
                    flag="pull-the-thread",
                    soul_path=self.action.SOULS_DIR
                    / "claude-fil-de-soie.md",
                    target=Path(tmpdir),
                    scan_id="test-scan",
                    prompt="default fil-de-soie prompt",
                )
                mock_run.assert_called_once()
                cmd = mock_run.call_args[0][0]
                self.assertEqual(cmd[0], "/fake/claude")
                self.assertIn("--append-system-prompt", cmd)
                self.assertIn("--print", cmd)
                handoff = cmd[-1]
                self.assertIn("test-scan", handoff)
                self.assertIn(tmpdir, handoff)
                self.assertIn("default fil-de-soie prompt", handoff)


if __name__ == "__main__":
    unittest.main()
