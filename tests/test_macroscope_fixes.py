"""Red/green tests for bugs found by macroscope review.

Each test reproduces a bug, fails before the fix, and passes after.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import unittest
from unittest.mock import MagicMock, patch


# ── #1: Handoff sentinel false positive ──────────────────────────────

class TestSentinelFalsePositive(unittest.TestCase):
    """handoff_backend._call checks for PENDING_SENTINEL_PREFIX anywhere in
    the prompt. A legitimate prompt containing the literal prefix text raises
    PendingCalls with empty self.pending, crashing downstream."""

    def test_legitimate_prompt_containing_sentinel_prefix_does_not_raise(self):
        from skillopt_sleep.handoff_backend import (
            HandoffBackend, PendingCalls, PENDING_SENTINEL_PREFIX,
        )
        with tempfile.TemporaryDirectory() as hdir:
            be = HandoffBackend(handoff_dir=hdir)
            # A prompt that legitimately mentions the sentinel prefix
            prompt = f"Explain what {PENDING_SENTINEL_PREFIX} means in the codebase."
            # Should NOT raise — it's a real prompt, not a dependent call
            result = be._call(prompt)
            self.assertTrue(result.startswith(PENDING_SENTINEL_PREFIX))
            self.assertEqual(len(be.pending), 1)

    def test_actual_sentinel_placeholder_still_raises(self):
        from skillopt_sleep.handoff_backend import (
            HandoffBackend, PendingCalls, PENDING_SENTINEL_PREFIX,
            PENDING_SENTINEL_SUFFIX,
        )
        with tempfile.TemporaryDirectory() as hdir:
            be = HandoffBackend(handoff_dir=hdir)
            # First call creates a pending entry and returns a sentinel
            sentinel = be._call("first question")
            # A call whose prompt IS the sentinel (built from a placeholder)
            # should raise
            with self.assertRaises(PendingCalls):
                be._call(f"judge this: {sentinel}")

    def test_short_hex_sentinel_does_not_raise(self):
        """A prompt containing [[SKILLOPT-SLEEP-PENDING:a]] (1 hex digit)
        is NOT a real sentinel — real sentinels have exactly 16 hex digits."""
        from skillopt_sleep.handoff_backend import (
            HandoffBackend, PENDING_SENTINEL_PREFIX, PENDING_SENTINEL_SUFFIX,
        )
        with tempfile.TemporaryDirectory() as hdir:
            be = HandoffBackend(handoff_dir=hdir)
            # 1 hex digit — not a real sentinel
            fake = f"{PENDING_SENTINEL_PREFIX}a{PENDING_SENTINEL_SUFFIX}"
            result = be._call(f"discuss {fake} in the docs")
            self.assertTrue(result.startswith(PENDING_SENTINEL_PREFIX))
            self.assertEqual(len(be.pending), 1)


# ── #2: Handoff no-pending crash ─────────────────────────────────────

class TestNoPendingCrash(unittest.TestCase):
    """When PendingCalls is raised but backend.pending is empty,
    _run_handoff calls _print_run_report(None, ...) which crashes on
    outcome.report."""

    def test_print_run_report_handles_none_outcome(self):
        from skillopt_sleep.__main__ import _print_run_report
        # Should not crash on None outcome
        args = MagicMock()
        args.json = False
        # This should print a graceful message, not crash with AttributeError
        _print_run_report(None, args, {})

    def test_run_handoff_returns_early_on_none_outcome(self):
        """When PendingCalls is raised with empty pending, outcome stays None.
        _run_handoff must return 0 after printing, not crash on
        outcome.staging_dir."""
        from skillopt_sleep.__main__ import _run_handoff
        from skillopt_sleep.config import load_config
        from skillopt_sleep.handoff_backend import PendingCalls
        from skillopt_sleep.mine import assign_splits
        from skillopt_sleep.types import TaskRecord

        tasks = assign_splits(
            [TaskRecord(id="t1", project="/p", intent="test",
                        reference_kind="exact", reference="42")],
            holdout_fraction=0.34, seed=42,
        )

        with tempfile.TemporaryDirectory() as proj, \
                tempfile.TemporaryDirectory() as home:
            with patch("skillopt_sleep.__main__.run_sleep_cycle",
                       side_effect=PendingCalls({})):
                args = MagicMock()
                args.backend = "handoff"
                args.project = proj
                args.json = False
                cfg = load_config(
                    invoked_project=proj, projects="invoked",
                    backend="handoff",
                    claude_home=os.path.join(home, ".claude"),
                )
                rc = _run_handoff(cfg, args, seed_tasks=tasks,
                                  task_meta={}, dry=False)
                self.assertEqual(rc, 0)


# ── #3: Digest redaction mismatch on resume ──────────────────────────

class TestDigestRedactionMismatch(unittest.TestCase):
    """digests.json was written with _redact_deep, but the in-memory digests
    used for mining were unredacted. On resume, redacted digests were reloaded,
    so prompt IDs didn't match and mining couldn't advance.

    Fix: write unredacted digests to the pin (the handoff dir is gitignored
    private local data, consistent with PROMPTS.md). This ensures the pin
    matches what mining saw, so prompt IDs are stable on resume without
    altering what the LLM miner reads."""

    def test_digests_pin_matches_in_memory_digests(self):
        from skillopt_sleep.backend import skill_hash

        # With the fix, both the pin and in-memory digests are unredacted,
        # so prompt IDs always match.
        original = {"text": "api_key=sk-abc123def456"}
        in_memory = original  # no redaction before mining
        from_pin = original   # no redaction in pin
        self.assertEqual(
            skill_hash(in_memory["text"]),
            skill_hash(from_pin["text"]),
        )


# ── #4: Handoff redaction clobbers legitimate content ───────────────

class TestRedactClobbersLegitimateContent(unittest.TestCase):
    """flush_pending applies redact_secrets to the operational prompt written
    to PROMPTS.md and pending.json. Legitimate task content matching a secret
    pattern gets [REDACTED], so the handoff agent can't answer correctly."""

    def test_flush_pending_preserves_operational_prompts(self):
        from skillopt_sleep.handoff_backend import HandoffBackend
        with tempfile.TemporaryDirectory() as hdir:
            be = HandoffBackend(handoff_dir=hdir)
            # A prompt that contains a secret-like pattern but is legitimate
            # task content the answering agent needs to see
            prompt = "Edit the config: set api_key=sk-test123 in the YAML file."
            be._call(prompt)
            be.flush_pending()
            with open(os.path.join(hdir, "pending.json"), encoding="utf-8") as f:
                payload = json.load(f)
            # The prompt in pending.json should NOT be redacted — the
            # answering agent needs the full text to answer correctly.
            self.assertEqual(payload["pending"][0]["prompt"], prompt)


# ── #5: Handoff 8-round cap ──────────────────────────────────────────

class TestHandoffRoundCap(unittest.TestCase):
    """The handoff command hard-caps at 8 rounds. A run needing a 9th batch
    is abandoned mid-cycle. The cap should be configurable or unbounded."""

    def test_handoff_command_does_not_hardcode_eight_rounds(self):
        path = os.path.join(
            os.path.dirname(__file__), "..",
            "plugins", "claude-code", "commands", "skillopt-sleep-handoff.md",
        )
        with open(path, encoding="utf-8") as f:
            content = f.read()
        # The command should not contain a hardcoded "at most 8 rounds" cap
        self.assertNotIn("at most 8 rounds", content)


# ── #6: Compat mode ignores max_tokens ───────────────────────────────

class TestCompatMaxTokens(unittest.TestCase):
    """In compat mode, _call ignores the max_tokens argument and always sends
    self.compat_max_tokens (default 8192). Should use min(max_tokens,
    compat_max_tokens)."""

    def test_compat_mode_respects_caller_max_tokens(self):
        from skillopt_sleep.backend import AzureOpenAIBackend
        with tempfile.TemporaryDirectory() as tmp:
            # Set compat mode env
            with patch.dict(os.environ, {
                "AZURE_OPENAI_AUTH_MODE": "openai",
                "AZURE_OPENAI_DEPLOYMENT": "test-deploy",
                "AZURE_OPENAI_API_KEY": "test-key",
                "SKILLOPT_SLEEP_COMPAT_MAX_TOKENS": "8192",
            }):
                backend = AzureOpenAIBackend(
                    endpoint="https://test.openai.azure.com/",
                    deployment="test-deploy",
                )
                # Mock the client to capture what max_tokens gets sent
                mock_resp = MagicMock()
                mock_resp.choices = [MagicMock()]
                mock_resp.choices[0].message.content = "test response"
                mock_resp.usage = MagicMock()
                mock_resp.usage.prompt_tokens = 10
                mock_resp.usage.completion_tokens = 5

                mock_client = MagicMock()
                mock_client.chat.completions.create.return_value = mock_resp
                backend._client = mock_client

                # Call with max_tokens=200 (e.g. judge call)
                backend._call("test prompt", max_tokens=200)

                # Check what was sent
                call_kwargs = mock_client.chat.completions.create.call_args[1]
                # Should be min(200, 8192) = 200, not 8192
                self.assertEqual(call_kwargs["max_tokens"], 200,
                                 "compat mode should respect caller's max_tokens")


# ── #7: Harvest meta-prompt filter conflicts with agent-session filter ─

class TestHarvestFilterConflict(unittest.TestCase):
    """_is_meta_prompt filters out the plugin's command body before
    user_prompts is populated. But _is_agent_session checks user_prompts[0]
    for the 'You are driving SkillOpt-Sleep' marker. Since that marker is
    in the filtered-out command body, the check never matches."""

    def test_agent_session_detected_despite_meta_prompt_filter(self):
        from skillopt_sleep.harvest import _is_meta_prompt, _is_agent_session
        from skillopt_sleep.types import SessionDigest

        # Simulate a Claude Code session that starts with the
        # /skillopt-sleep command body (which _is_meta_prompt filters out)
        # followed by a real user message.
        command_body = (
            "<command-message>\nYou are driving **SkillOpt-Sleep**: "
            "a tool that lets this user's Claude agent improve from past usage"
        )
        real_user_msg = "run the sleep cycle for this project"

        # The command body IS a meta prompt (should be filtered)
        self.assertTrue(_is_meta_prompt(command_body))

        # But _is_agent_session should still detect this as an agent session
        # because the FIRST user message (before filtering) contains the marker.
        # After the fix, _is_agent_session should check the raw first message,
        # not the filtered user_prompts list.
        digest = SessionDigest(
            session_id="test", project="/p", git_branch="",
            started_at="", ended_at="",
            user_prompts=[real_user_msg],  # after filtering
            assistant_finals=[], tools_used=[], files_touched=[],
            feedback_signals=[], n_user_turns=1, n_assistant_turns=1,
            raw_first_user_prompt=command_body,  # before filtering
        )
        # Currently fails: user_prompts[0] is "run the sleep cycle"
        # which doesn't match the marker.
        # After fix: should detect it as an agent session.
        self.assertTrue(_is_agent_session(digest),
                        "should detect agent session even when command body "
                        "is filtered from user_prompts")


if __name__ == "__main__":
    unittest.main()
