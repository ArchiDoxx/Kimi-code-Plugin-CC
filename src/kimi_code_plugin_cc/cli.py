"""Main CLI entry points for the Kimi Code plugin."""

from __future__ import annotations

import argparse
import sys

from kimi_code_plugin_cc.mcp_server import main as mcp_main


def main(argv: list[str] | None = None) -> int:
    """Dispatch to subcommands."""
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

    args, mcp_extra = parser.parse_known_args(argv)
    if args.command == "mcp":
        mcp_main(mcp_extra)
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
