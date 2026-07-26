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


def _find(checks: list[Check], name: str) -> Check:
    return next(check for check in checks if check.name == name)


def _healthy_cli(
    help_output: str = HELP_WITH_ALL_FLAGS,
    version: str | None = "0.29.1",
):
    return (
        mock.patch(f"{DOCTOR_MODULE}.shutil.which", return_value="/usr/bin/kimi"),
        mock.patch(f"{DOCTOR_MODULE}._deshim_cmd_wrapper", return_value=None),
        mock.patch(f"{DOCTOR_MODULE}.cli_version", return_value=version),
        mock.patch(f"{DOCTOR_MODULE}.help_text", return_value=help_output),
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
