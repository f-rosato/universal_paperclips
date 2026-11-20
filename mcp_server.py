#!/usr/bin/env python3
"""MCP server (stdio) that exposes the Paperclips harness as tools.

A thin adapter: every tool call is forwarded to harness.py's HTTP API, which
owns the browser. Start dev.py and harness.py first; the MCP client (Claude
Code, Claude Desktop) launches this file itself.

    claude mcp add paperclips -- .venv/Scripts/python.exe mcp_server.py

Never print to stdout here: stdout is the MCP protocol channel.
"""
import asyncio
import json
import os
import urllib.error
import urllib.request

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

HARNESS = os.environ.get("PAPERCLIPS_HARNESS", "http://127.0.0.1:8765")
MAX_WAIT_S = 120  # keep each tool call well under client tool-call timeouts

server = MCPServer(
    name="paperclips",
    instructions=(
        "Tools to play Universal Paperclips in a live browser. The game runs in "
        "real time, including between your calls. Actions (click, wait, set) "
        "return only a short result; call observe whenever you need to see the "
        "game. Only buttons listed by observe can be clicked, and only when "
        "enabled. Projects (kind='project') are one-off upgrades that also appear "
        "as buttons."
    ),
)


def _request(path, body, timeout):
    req = urllib.request.Request(
        HARNESS + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            msg = json.load(e).get("error", str(e))
        except ValueError:
            msg = str(e)
        raise ToolError(msg) from None
    except urllib.error.URLError as e:
        raise ToolError(
            f"cannot reach the harness at {HARNESS} ({e.reason}); "
            "is harness.py running?"
        ) from None


async def call(path, body, timeout=30):
    return await asyncio.to_thread(_request, path, body, timeout)


@server.tool()
async def observe(text: bool = False) -> dict:
    """Return the current game state.

    - buttons: visible buttons as {id, label, enabled, kind}; kind is "project"
      for one-off project upgrades, else "button".
    - values: visible stats keyed by element id (clips, funds, wire, unsoldClips,
      margin, demand, trust, operations, creativity, ...).
    - controls: slider / investStrat / stratPicker, when unlocked.
    - messages: the game's console log, newest first.

    Set text=true to also get the full visible page text (bigger, but it shows
    the labels next to each number).
    """
    return await call("/observe", {"text": text})


@server.tool()
async def click(id: str, times: int = 1) -> dict:
    """Click the button with this id `times` times (max 1000), quickly.

    Stops early if the button becomes disabled or hidden (e.g. you run out of
    money). Returns {clicked, requested, stopped_reason}; it does not return
    the game state, so call observe to see the effect.
    """
    return await call("/click", {"id": id, "times": times}, timeout=120)


@server.tool()
async def wait(seconds: float) -> dict:
    """Let the game run for `seconds` (max 120) without acting. Returns {waited}."""
    seconds = max(0.0, min(float(seconds), MAX_WAIT_S))
    return await call("/wait", {"seconds": seconds}, timeout=seconds + 30)


@server.tool(name="set")
async def set_control(id: str, value: str | float) -> dict:
    """Set a non-button control. Only works once the control is unlocked.

    - slider: swarm work/think balance, number 0-200.
    - investStrat: investment risk, one of "low", "med", "hi".
    - stratPicker: tournament strategy; valid values are listed by observe.
    """
    if id == "slider":
        try:
            value = float(value)
        except ValueError:
            raise ToolError("slider value must be a number 0-200") from None
    elif isinstance(value, float):  # select options are strings, e.g. "0"
        value = str(int(value)) if value.is_integer() else str(value)
    return await call("/set", {"id": id, "value": value})


if __name__ == "__main__":
    server.run()
