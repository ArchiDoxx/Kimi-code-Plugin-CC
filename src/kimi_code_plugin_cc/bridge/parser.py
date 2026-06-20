"""NDJSON / stream-json parsing helpers."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any


async def parse_stream_json(
    raw_stream: str | AsyncIterator[str],
) -> list[dict[str, Any]]:
    """Parse a stream-json payload into a list of JSON objects.

    Handles complete and incomplete JSON lines across chunk boundaries. When the
    input is a string, lines are split on newlines and each non-empty line is
    parsed individually. Lines that cannot be decoded as JSON are returned as
    ``{"raw": "<line>"}`` so callers can inspect them without crashing the
    pipeline.
    """
    if isinstance(raw_stream, str):
        return _parse_string(raw_stream)
    if hasattr(raw_stream, "__aiter__"):
        return await parse_stream_json_async(raw_stream)
    raise TypeError(
        f"Expected str or AsyncIterator[str], got {type(raw_stream).__name__}"
    )


def _parse_string(raw_stream: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for line in raw_stream.splitlines():
        line = line.strip()
        if not line:
            continue
        results.append(_parse_line(line))
    return results


def _parse_line(line: str) -> dict[str, Any]:
    try:
        parsed = json.loads(line)
    except json.JSONDecodeError:
        return {"raw": line}
    if isinstance(parsed, dict):
        return parsed
    return {"value": parsed}


async def parse_stream_json_async(
    raw_stream: AsyncIterator[str],
) -> list[dict[str, Any]]:
    """Parse an async stream-json payload across chunk boundaries."""
    results: list[dict[str, Any]] = []
    buffer = ""
    async for chunk in raw_stream:
        buffer += chunk
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            line = line.strip()
            if line:
                results.append(_parse_line(line))
    trailing = buffer.strip()
    if trailing:
        results.append(_parse_line(trailing))
    return results
