#!/usr/bin/env python3
"""PreToolUse hook: block Edit/Write/MultiEdit/NotebookEdit on README.md.

README.md is human-owned in this repo (see AGENTS.md). Agents that try to
edit it are redirected to README-agents.md, a gitignored scratch file the
maintainer reviews and merges by hand.

Fails open: any error here (bad stdin, missing fields, path resolution
trouble) allows the tool call through rather than blocking unrelated work.
"""

import json
import os
import sys

WRITE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
REDIRECT_MESSAGE = (
    "README.md is protected by a repo hook (see AGENTS.md) and cannot be "
    "edited or written by an agent. Write the proposed change to "
    "README-agents.md instead (create it if missing) — the maintainer "
    "reviews that file and updates README.md by hand."
)


def repo_root(start: str) -> str:
    current = start
    while True:
        if os.path.isdir(os.path.join(current, ".git")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return start
        current = parent


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    tool_name = payload.get("tool_name", "")
    if tool_name not in WRITE_TOOLS:
        return 0

    tool_input = payload.get("tool_input") or {}
    file_path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
    if not file_path:
        return 0

    cwd = payload.get("cwd") or os.getcwd()
    resolved = file_path if os.path.isabs(file_path) else os.path.join(cwd, file_path)
    resolved = os.path.normpath(resolved)

    root = repo_root(cwd)
    readme_path = os.path.normpath(os.path.join(root, "README.md"))

    if resolved == readme_path:
        sys.stderr.write(REDIRECT_MESSAGE + "\n")
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
