"""Tests for the helpers shared by every CLI agent adapter.

The Windows ``.cmd``-shim resolver gets the most attention here. It is the
piece that once silently truncated every multi-line prompt at line 1 (cmd.exe
cuts arguments at the first newline), and its failure mode is a *quietly wrong
answer* rather than a crash — so each fallback branch is asserted directly
instead of only through an adapter.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from kimi_code_plugin_cc.agent_registry.common import (
    DEFAULT_MAX_PROMPT_CHARS,
    deshim_cmd_wrapper,
    env_flag_enabled,
    max_prompt_chars,
    resolve_executable,
)
from kimi_code_plugin_cc.errors import AgentNotInstalledError

COMMON_MODULE = "kimi_code_plugin_cc.agent_registry.common"
SHIM_BODY = '@node  "%dp0%\\node_modules\\@moonshot-ai\\kimi-code\\dist\\main.mjs" %*\n'
ENTRY_RELPATH = Path("node_modules/@moonshot-ai/kimi-code/dist/main.mjs")


def _write_shim(directory: Path, body: str = SHIM_BODY, entry: bool = True) -> Path:
    """Create a minimal npm shim layout and return the shim path."""
    shim = directory / "kimi.cmd"
    shim.write_text(body, encoding="utf-8")
    if entry:
        target = directory / ENTRY_RELPATH
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("", encoding="utf-8")
    return shim


class TestDeshimCmdWrapper:
    def test_resolves_shim_to_node_and_entry_point(self, tmp_path: Path) -> None:
        shim = _write_shim(tmp_path)
        deshimed = deshim_cmd_wrapper(shim)
        assert deshimed is not None
        assert deshimed[0] == "node"
        assert Path(deshimed[1]) == (tmp_path / ENTRY_RELPATH).resolve()

    def test_prefers_a_node_exe_next_to_the_shim(self, tmp_path: Path) -> None:
        # npm installs node beside the shim; using it avoids depending on
        # whatever `node` happens to come first on the host's PATH.
        shim = _write_shim(tmp_path)
        (tmp_path / "node.exe").write_text("", encoding="utf-8")
        deshimed = deshim_cmd_wrapper(shim)
        assert deshimed is not None
        assert deshimed[0] == str(tmp_path / "node.exe")

    def test_resolves_a_shim_that_hardcodes_an_absolute_entry_point(
        self, tmp_path: Path
    ) -> None:
        # Some npm/pnpm layouts bake the absolute path into the shim instead of
        # using %dp0%; that path must be taken as-is, not joined onto the shim
        # directory.
        entry = tmp_path / ENTRY_RELPATH
        entry.parent.mkdir(parents=True, exist_ok=True)
        entry.write_text("", encoding="utf-8")
        shim = tmp_path / "kimi.cmd"
        shim.write_text(f'@node  "{entry}" %*\n', encoding="utf-8")
        deshimed = deshim_cmd_wrapper(shim)
        assert deshimed is not None
        assert Path(deshimed[1]) == entry.resolve()

    def test_native_binary_is_not_a_shim(self, tmp_path: Path) -> None:
        binary = tmp_path / "codex.exe"
        binary.write_text("", encoding="utf-8")
        assert deshim_cmd_wrapper(binary) is None

    def test_unreadable_shim_falls_back_instead_of_raising(
        self, tmp_path: Path
    ) -> None:
        # Fail-safe: running the shim as-is still works for single-line
        # prompts, so an unreadable file must not abort the run.
        shim = _write_shim(tmp_path)
        with mock.patch.object(Path, "read_text", side_effect=OSError("locked")):
            assert deshim_cmd_wrapper(shim) is None

    def test_shim_without_a_node_entry_point_falls_back(self, tmp_path: Path) -> None:
        shim = _write_shim(tmp_path, body="@echo something else\n", entry=False)
        assert deshim_cmd_wrapper(shim) is None

    def test_shim_pointing_at_a_missing_entry_point_falls_back(
        self, tmp_path: Path
    ) -> None:
        # A half-removed npm package: the shim survives, the entry point does
        # not. Falling back keeps the CLI's own error message intact.
        shim = _write_shim(tmp_path, entry=False)
        assert deshim_cmd_wrapper(shim) is None


class TestResolveExecutable:
    def test_returns_the_resolved_binary(self) -> None:
        with mock.patch(f"{COMMON_MODULE}.shutil.which", return_value="/usr/bin/codex"):
            assert resolve_executable("codex", "hint") == ["/usr/bin/codex"]

    def test_missing_executable_carries_the_agents_own_hint(self) -> None:
        with (
            mock.patch(f"{COMMON_MODULE}.shutil.which", return_value=None),
            pytest.raises(AgentNotInstalledError) as excinfo,
        ):
            resolve_executable("codex", "Install with 'npm install -g @openai/codex'.")
        assert excinfo.value.executable == "codex"
        assert "@openai/codex" in str(excinfo.value)


class TestEnvHelpers:
    @pytest.mark.parametrize("value", ["0", "false", "no", "off", "OFF", " off "])
    def test_falsey_values_switch_a_flag_off(self, value: str) -> None:
        with mock.patch.dict("os.environ", {"KIMI_TEST_FLAG": value}):
            assert env_flag_enabled("KIMI_TEST_FLAG") is False

    @pytest.mark.parametrize("value", ["1", "true", "yes", "anything"])
    def test_other_values_leave_it_on(self, value: str) -> None:
        with mock.patch.dict("os.environ", {"KIMI_TEST_FLAG": value}):
            assert env_flag_enabled("KIMI_TEST_FLAG") is True

    def test_unset_variable_uses_the_default(self) -> None:
        assert env_flag_enabled("KIMI_DEFINITELY_UNSET_FLAG", default=False) is False
        assert env_flag_enabled("KIMI_DEFINITELY_UNSET_FLAG") is True

    @pytest.mark.parametrize("value", ["not-a-number", "0", "-5"])
    def test_a_broken_prompt_limit_never_disables_the_guard(self, value: str) -> None:
        with mock.patch.dict("os.environ", {"KIMI_MAX_PROMPT_CHARS": value}):
            assert max_prompt_chars() == DEFAULT_MAX_PROMPT_CHARS
