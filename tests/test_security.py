"""Tests for security policy and worktree isolation."""

from __future__ import annotations

from pathlib import Path

import pytest

from kimi_code_plugin_cc.security.policy import (
    ApprovalPolicy,
    create_isolated_worktree,
    read_max_policy_from_env,
    resolve_effective_policy,
)


class TestApprovalPolicy:
    def test_from_string(self) -> None:
        assert ApprovalPolicy.from_string("read-only") == ApprovalPolicy.READ_ONLY
        assert ApprovalPolicy.from_string("explicit") == ApprovalPolicy.EXPLICIT
        assert ApprovalPolicy.from_string("accept-edits") == ApprovalPolicy.ACCEPT_EDITS

    def test_from_string_case_insensitive(self) -> None:
        assert ApprovalPolicy.from_string("READ-ONLY") == ApprovalPolicy.READ_ONLY

    def test_from_string_unknown_raises(self) -> None:
        with pytest.raises(ValueError):
            ApprovalPolicy.from_string("unknown")

    def test_to_string(self) -> None:
        assert ApprovalPolicy.READ_ONLY.to_string() == "read-only"
        assert ApprovalPolicy.EXPLICIT.to_string() == "explicit"
        assert ApprovalPolicy.ACCEPT_EDITS.to_string() == "accept-edits"

    def test_ordering(self) -> None:
        assert ApprovalPolicy.READ_ONLY < ApprovalPolicy.EXPLICIT
        assert ApprovalPolicy.EXPLICIT < ApprovalPolicy.ACCEPT_EDITS


class TestResolveEffectivePolicy:
    def test_requested_below_ceiling(self) -> None:
        effective = resolve_effective_policy(
            ApprovalPolicy.READ_ONLY,
            max_policy=ApprovalPolicy.ACCEPT_EDITS,
        )
        assert effective == ApprovalPolicy.READ_ONLY

    def test_requested_above_ceiling_is_capped(self) -> None:
        effective = resolve_effective_policy(
            ApprovalPolicy.ACCEPT_EDITS,
            max_policy=ApprovalPolicy.READ_ONLY,
        )
        assert effective == ApprovalPolicy.READ_ONLY

    def test_string_inputs(self) -> None:
        effective = resolve_effective_policy("accept-edits", max_policy="explicit")
        assert effective == ApprovalPolicy.EXPLICIT

    def test_env_ceiling_used_when_none_provided(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("KIMI_MAX_POLICY", "explicit")
        effective = resolve_effective_policy("accept-edits")
        assert effective == ApprovalPolicy.EXPLICIT

    def test_env_ceiling_default_when_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("KIMI_MAX_POLICY", raising=False)
        effective = resolve_effective_policy("accept-edits")
        assert effective == ApprovalPolicy.READ_ONLY


class TestReadMaxPolicyFromEnv:
    def test_reads_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KIMI_MAX_POLICY", "accept-edits")
        assert read_max_policy_from_env() == ApprovalPolicy.ACCEPT_EDITS

    def test_defaults_to_read_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("KIMI_MAX_POLICY", raising=False)
        assert read_max_policy_from_env() == ApprovalPolicy.READ_ONLY


class TestWorktreeIsolation:
    def test_create_isolated_worktree(self, tmp_path: Path) -> None:
        worktree = create_isolated_worktree(tmp_path)
        assert worktree.exists()
        assert worktree.is_dir()
        assert "kimi_worktree_" in worktree.name
        assert worktree.parent == tmp_path
