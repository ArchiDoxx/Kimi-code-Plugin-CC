"""Tests for the doctor preflight and the operator CLI."""

from __future__ import annotations

from typing import Any
from unittest import mock

from kimi_code_plugin_cc.cli import main
from kimi_code_plugin_cc.doctor import Check, format_report, has_failure, run_checks

DOCTOR_MODULE = "kimi_code_plugin_cc.doctor"

# A realistic help excerpt: enough to exercise flag detection without pinning
# the CLI's full output.
HELP_WITH_ALL_FLAGS = (
    "  -m, --model <model>       LLM model alias\n"
    "  -p, --prompt <prompt>     Run one prompt non-interactively\n"
    "  --output-format <format>  text | stream-json\n"
    "  --skills-dir <dir>        Load skills from this directory\n"
)
HELP_WITHOUT_SKILLS_DIR = HELP_WITH_ALL_FLAGS.replace(
    "  --skills-dir <dir>        Load skills from this directory\n", ""
)

# A `codex exec --help` excerpt covering the surface the adapter depends on.
CODEX_EXEC_HELP = (
    "  -m, --model <MODEL>              Model the agent should use\n"
    "  -s, --sandbox <SANDBOX_MODE>     [possible values: read-only, "
    "workspace-write, danger-full-access]\n"
    "      --json                       Print events to stdout as JSONL\n"
    "  -o, --output-last-message <FILE> Where the last message is written\n"
    "      --ephemeral                  Run without persisting session files\n"
    "      --ignore-user-config         Do not load $CODEX_HOME/config.toml\n"
    "      --skip-git-repo-check        Allow running outside a Git repository\n"
)
CODEX_EXEC_HELP_WITHOUT_OUTPUT = CODEX_EXEC_HELP.replace(
    "  -o, --output-last-message <FILE> Where the last message is written\n", ""
)


def _find(checks: list[Check], name: str) -> Check:
    return next(check for check in checks if check.name == name)


def _healthy_cli(
    help_output: str = HELP_WITH_ALL_FLAGS,
    version: str | None = "0.29.1",
    codex_help: str | None = None,
):
    """Patch the doctor's CLI probes.

    ``codex_help`` is ``None`` by default so codex reads as *not installed* —
    the common case, and the one the kimi-focused assertions below rely on.
    Passing a help string makes codex present and drives its flag checks.
    """

    def _which(name: str) -> str | None:
        if name == "kimi":
            return "/usr/bin/kimi"
        if name == "codex" and codex_help is not None:
            return "/usr/bin/codex"
        return None

    def _help(prefix: list[str]) -> str:
        # The codex flag surface lives under the `exec` subcommand, so the
        # doctor probes `codex exec --help`, not `codex --help`.
        return (codex_help or "") if "exec" in prefix else help_output

    return (
        mock.patch(f"{DOCTOR_MODULE}.shutil.which", side_effect=_which),
        mock.patch(f"{DOCTOR_MODULE}.deshim_cmd_wrapper", return_value=None),
        mock.patch(f"{DOCTOR_MODULE}.cli_version", return_value=version),
        mock.patch(f"{DOCTOR_MODULE}.help_text", side_effect=_help),
    )


class TestRunChecks:
    def test_missing_cli_fails_with_install_instructions(self) -> None:
        with mock.patch(f"{DOCTOR_MODULE}.shutil.which", return_value=None):
            checks = run_checks()
        cli_check = _find(checks, "agent CLI")
        assert cli_check.status == "fail"
        assert "npm i -g" in cli_check.detail
        assert has_failure(checks) is True

    def test_missing_cli_still_reports_local_configuration(self) -> None:
        # A broken CLI must not hide the rest of the diagnosis.
        with mock.patch(f"{DOCTOR_MODULE}.shutil.which", return_value=None):
            checks = run_checks()
        names = {check.name for check in checks}
        assert {
            "policy ceiling",
            "depth guard",
            "prompt limit",
            "worktree base",
        } <= names

    def test_healthy_cli_reports_ready(self) -> None:
        which, deshim, version, help_ = _healthy_cli()
        with which, deshim, version, help_:
            checks = run_checks()
        assert has_failure(checks) is False
        assert _find(checks, "flag surface").status == "ok"
        assert _find(checks, "skills isolation").status == "ok"

    def test_missing_required_flag_is_a_failure(self) -> None:
        # Losing a pinned flag breaks every run, so it must not be a warning.
        crippled = HELP_WITH_ALL_FLAGS.replace("--output-format <format>", "")
        which, deshim, version, help_ = _healthy_cli(help_output=crippled)
        with which, deshim, version, help_:
            checks = run_checks()
        flag_check = _find(checks, "flag surface")
        assert flag_check.status == "fail"
        assert "--output-format" in flag_check.detail

    def test_old_cli_without_skills_dir_only_warns(self) -> None:
        # Isolation is a reproducibility nicety, not a hard requirement.
        which, deshim, version, help_ = _healthy_cli(
            help_output=HELP_WITHOUT_SKILLS_DIR, version="0.22.2"
        )
        with which, deshim, version, help_:
            checks = run_checks()
        assert _find(checks, "skills isolation").status == "warn"
        assert has_failure(checks) is False

    def test_unreadable_help_is_a_failure(self) -> None:
        which, deshim, version, help_ = _healthy_cli(help_output="", version=None)
        with which, deshim, version, help_:
            checks = run_checks()
        assert _find(checks, "flag surface").status == "fail"
        assert _find(checks, "CLI version").status == "warn"


class TestCodexChecks:
    """codex is the optional second agent: absent is a warning, broken is fatal."""

    def test_absent_codex_only_warns_and_keeps_doctor_green(self) -> None:
        # kimi remains the primary agent, so a machine without codex is ready.
        which, deshim, version, help_ = _healthy_cli()
        with which, deshim, version, help_:
            checks = run_checks()
        codex = _find(checks, "codex CLI")
        assert codex.status == "warn"
        assert "optional" in codex.detail.lower()
        assert has_failure(checks) is False

    def test_absent_codex_names_the_install_channel(self) -> None:
        which, deshim, version, help_ = _healthy_cli()
        with which, deshim, version, help_:
            checks = run_checks()
        assert "@openai/codex" in _find(checks, "codex CLI").detail

    def test_present_codex_reports_its_flag_surface(self) -> None:
        which, deshim, version, help_ = _healthy_cli(codex_help=CODEX_EXEC_HELP)
        with which, deshim, version, help_:
            checks = run_checks()
        assert _find(checks, "codex CLI").status == "ok"
        assert _find(checks, "codex flag surface").status == "ok"
        assert has_failure(checks) is False

    def test_installed_codex_missing_a_base_flag_is_fatal(self) -> None:
        # An installed-but-incompatible CLI breaks every codex run, so unlike
        # absence it must not be downgraded to a warning.
        which, deshim, version, help_ = _healthy_cli(
            codex_help=CODEX_EXEC_HELP_WITHOUT_OUTPUT
        )
        with which, deshim, version, help_:
            checks = run_checks()
        flag_check = _find(checks, "codex flag surface")
        assert flag_check.status == "fail"
        assert "--output-last-message" in flag_check.detail
        assert has_failure(checks) is True

    def test_unreadable_codex_help_is_fatal(self) -> None:
        which, deshim, version, help_ = _healthy_cli(codex_help="")
        with which, deshim, version, help_:
            checks = run_checks()
        # An empty help string means the CLI is on PATH but not answering;
        # that cannot be distinguished from a drifted flag surface, so fail.
        assert _find(checks, "codex flag surface").status == "fail"

    def test_codex_isolation_support_is_reported(self) -> None:
        which, deshim, version, help_ = _healthy_cli(codex_help=CODEX_EXEC_HELP)
        with which, deshim, version, help_:
            checks = run_checks()
        assert _find(checks, "codex isolation").status == "ok"

    def test_codex_without_isolation_flags_only_warns(self) -> None:
        stripped = CODEX_EXEC_HELP.replace(
            "      --ephemeral                  Run without persisting session files\n",
            "",
        )
        which, deshim, version, help_ = _healthy_cli(codex_help=stripped)
        with which, deshim, version, help_:
            checks = run_checks()
        assert _find(checks, "codex isolation").status == "warn"
        assert has_failure(checks) is False


class TestReport:
    def test_failure_report_says_not_ready(self) -> None:
        report = format_report([Check("agent CLI", "fail", "missing")])
        assert "[FAIL]" in report
        assert "Not ready" in report

    def test_clean_report_says_ready(self) -> None:
        report = format_report([Check("agent CLI", "ok", "found")])
        assert report.endswith("Ready.")

    def test_warnings_are_counted_but_not_fatal(self) -> None:
        report = format_report(
            [
                Check("agent CLI", "ok", "found"),
                Check("skills isolation", "warn", "old CLI"),
            ]
        )
        assert "1 warning(s)" in report


class TestCli:
    def test_doctor_exits_zero_when_healthy(self, capsys: Any) -> None:
        with mock.patch(
            "kimi_code_plugin_cc.cli.run_checks",
            return_value=[Check("agent CLI", "ok", "found")],
        ):
            code = main(["doctor"])
        assert code == 0
        assert "Ready." in capsys.readouterr().out

    def test_doctor_exits_nonzero_on_failure(self, capsys: Any) -> None:
        # Non-zero so setup scripts and CI can gate on it.
        with mock.patch(
            "kimi_code_plugin_cc.cli.run_checks",
            return_value=[Check("agent CLI", "fail", "missing")],
        ):
            code = main(["doctor"])
        assert code == 1
        assert "Not ready" in capsys.readouterr().out

    def test_no_subcommand_prints_help(self, capsys: Any) -> None:
        assert main([]) == 0
        assert "doctor" in capsys.readouterr().out

    def test_mcp_subcommand_forwards_transport(self) -> None:
        with mock.patch("kimi_code_plugin_cc.cli.mcp_main") as mcp_main:
            assert main(["mcp", "--transport", "sse"]) == 0
        mcp_main.assert_called_once_with(["--transport", "sse"])
