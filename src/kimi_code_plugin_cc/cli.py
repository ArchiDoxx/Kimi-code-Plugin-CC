"""Main CLI entry points for the Kimi Code plugin."""

from __future__ import annotations

import argparse
import sys

from kimi_code_plugin_cc.doctor import format_report, has_failure, run_checks
from kimi_code_plugin_cc.mcp_server import main as mcp_main
from kimi_code_plugin_cc.transcript_view import (
    DEFAULT_LIMIT,
    TranscriptViewError,
    emit,
    list_runs,
    render_round,
    render_run,
    render_run_list,
    resolve_run_id,
)

# A missing subcommand is a usage error, so it gets argparse's own exit code
# rather than 1 - which this CLI reserves for "the command was fine, the data
# was not" (unknown run id, missing round).
USAGE_EXIT_CODE = 2


def _run_doctor() -> int:
    """Print the preflight report and return a shell-friendly exit code.

    Exit code 1 on failure so the command is usable in CI and setup scripts,
    not only interactively. Warnings do not fail the run.
    """
    checks = run_checks()
    print(format_report(checks))
    return 1 if has_failure(checks) else 0


def _positive_int(raw: str) -> int:
    """Argparse type for counts that are meaningless at zero or below."""
    value = int(raw)
    if value < 1:
        raise argparse.ArgumentTypeError("must be 1 or greater")
    return value


def _run_transcripts(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    """Dispatch the read-only transcript subcommands.

    Every viewer problem arrives as a :class:`TranscriptViewError`, so this is
    the single place that turns one into a message plus exit code 1. Nothing
    below writes to the transcript directory.
    """
    if args.transcripts_command is None:
        parser.print_help()
        return USAGE_EXIT_CODE
    try:
        if args.transcripts_command == "list":
            emit(render_run_list(list_runs(args.limit)))
            return 0
        run_dir = resolve_run_id(args.run_id)
        if args.round is None:
            emit(render_run(run_dir))
        else:
            emit(render_round(run_dir, args.round))
        return 0
    except TranscriptViewError as exc:
        emit(str(exc), stream=sys.stderr)
        return 1


def _add_transcripts_parser(
    subparsers: argparse._SubParsersAction,
) -> argparse.ArgumentParser:
    """Wire ``transcripts list`` and ``transcripts show`` onto *subparsers*."""
    parser = subparsers.add_parser(
        "transcripts",
        help="Read recorded loop transcripts (read-only)",
    )
    transcripts = parser.add_subparsers(dest="transcripts_command")
    list_parser = transcripts.add_parser("list", help="List recent runs, newest first")
    list_parser.add_argument(
        "--limit",
        type=_positive_int,
        default=DEFAULT_LIMIT,
        metavar="N",
        help=f"Number of runs to show (default {DEFAULT_LIMIT})",
    )
    show_parser = transcripts.add_parser(
        "show", help="Show one run; a unique run-id prefix is enough"
    )
    show_parser.add_argument("run_id", help="Run id or a unique prefix of one")
    show_parser.add_argument(
        "--round",
        type=int,
        metavar="N",
        help="Print the recorded round N verbatim instead of the summary",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Dispatch to subcommands.

    The ``mcp`` subcommand forwards its parsed ``--transport`` value explicitly
    to :func:`mcp_main` (rather than relying on ``parse_known_args`` leftovers,
    which would silently swallow the flag and fall back to ``stdio``).
    """
    parser = argparse.ArgumentParser(
        prog="kimi-code-plugin",
        description="Claude Code plugin for headless CLI agents",
    )
    subparsers = parser.add_subparsers(dest="command")

    mcp_parser = subparsers.add_parser("mcp", help="Start the MCP server")
    mcp_parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default="stdio",
    )
    subparsers.add_parser(
        "doctor",
        help="Check the agent CLI, its flag surface, and the plugin's configuration",
    )
    transcripts_parser = _add_transcripts_parser(subparsers)

    args = parser.parse_args(argv)
    if args.command == "mcp":
        mcp_main(["--transport", args.transport])
        return 0
    if args.command == "doctor":
        return _run_doctor()
    if args.command == "transcripts":
        return _run_transcripts(args, transcripts_parser)

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
