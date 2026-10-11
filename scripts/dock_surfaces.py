"""Finds the Linear sidebar's tabs in the cmux Dock.

Asks cmux for its tree and prints shell assignments: PAGE_PANE and PAGE_SURFACE (the
browser showing URL), SERVER_SURFACE (the server's terminal in the same Dock pane),
SERVER_RESUME (that terminal's resume command, its words joined by spaces) and
PANE_TERMINALS (every terminal in the pane). Missing values are empty. Exits non-zero
with a message when cmux fails.

A terminal is the server's when its title is SERVER_TITLE, which the server sets while
it runs, or when its resume binding starts cmux-linear-sidebar, which dock-setup.sh sets.
Right after dock-setup.sh opens the tab neither is there yet: `--created-after` lists
the terminals that existed before, and the only new one is the server's. Any other
terminal is the user's and is never typed into or closed.

Usage: python3 scripts/dock_surfaces.py CMUX URL [--created-after "REF ..."]
"""

import argparse
import json
import os
import shlex
import subprocess
import sys

SERVER_TITLE = "Linear server"
PROGRAM = "cmux-linear-sidebar"


class CmuxError(Exception):
    pass


def run_cmux(cmux, *args):
    """cmux's JSON answer to `args`."""
    try:
        result = subprocess.run([cmux, *args], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise CmuxError(f"cmux did not answer `{' '.join(args)}`: {error}") from None
    if result.returncode != 0:
        said = result.stderr.strip() or result.stdout.strip() or f"exit status {result.returncode}"
        raise CmuxError(f"cmux failed `{' '.join(args)}`: {said}")
    try:
        return json.loads(result.stdout)
    except ValueError:
        raise CmuxError(f"cmux returned unreadable output for `{' '.join(args)}`") from None


def dock_panes(node):
    if isinstance(node, dict):
        if node.get("dock_scope") and isinstance(node.get("surfaces"), list):
            yield node
        for value in node.values():
            yield from dock_panes(value)
    elif isinstance(node, list):
        for value in node:
            yield from dock_panes(value)


def resume_arguments(binding):
    """The command a resume binding runs, as a list of words; empty without one."""
    if not isinstance(binding, dict):
        return []
    launch = binding.get("launch_command")
    arguments = launch.get("arguments") if isinstance(launch, dict) else None
    if isinstance(arguments, list) and all(isinstance(a, str) for a in arguments):
        return arguments
    try:
        return shlex.split(binding.get("command") or "")
    except (TypeError, ValueError):
        return []


def runs_server(arguments):
    return bool(arguments) and os.path.basename(arguments[0]) == PROGRAM


def server_terminal(terminals, resume_of, created_after=None):
    """The server's terminal among the page pane's terminals, and its resume command."""
    for terminal in terminals:
        if terminal.get("title") == SERVER_TITLE:
            return terminal, resume_of(terminal["ref"])
    for terminal in terminals:
        arguments = resume_of(terminal["ref"])
        if runs_server(arguments):
            return terminal, arguments
    if created_after is not None:
        new = [t for t in terminals if t["ref"] not in created_after]
        if len(new) == 1:
            return new[0], resume_of(new[0]["ref"])
    return None, []


def find(tree, url, resume_of, created_after=None):
    """The tabs, given the tree and a function from a terminal's ref to its resume command."""
    found = {"PAGE_PANE": "", "PAGE_SURFACE": "", "SERVER_SURFACE": "", "SERVER_RESUME": "", "PANE_TERMINALS": ""}
    for pane in dock_panes(tree):
        surfaces = [s for s in pane["surfaces"] if isinstance(s, dict) and s.get("ref")]
        page = next((s for s in surfaces if s.get("type") == "browser"
                     and (s.get("url") or "").startswith(url)), None)
        if page is None:
            continue
        terminals = [s for s in surfaces if s.get("type") == "terminal"]
        server, arguments = server_terminal(terminals, resume_of, created_after)
        found.update(PAGE_PANE=pane.get("ref") or "", PAGE_SURFACE=page["ref"],
                     SERVER_SURFACE=server["ref"] if server else "",
                     SERVER_RESUME=" ".join(arguments) if server else "",
                     PANE_TERMINALS=" ".join(t["ref"] for t in terminals))
        break
    return found


def main(argv=None):
    parser = argparse.ArgumentParser(description="Finds the Linear sidebar's tabs in the cmux Dock.")
    parser.add_argument("cmux")
    parser.add_argument("url")
    parser.add_argument("--created-after", help="terminal refs in the page pane before this run opened one")
    args = parser.parse_args(argv)
    created_after = set(args.created_after.split()) if args.created_after is not None else None

    def resume_of(ref):
        shown = run_cmux(args.cmux, "surface", "resume", "show", "--json", "--surface", ref)
        return resume_arguments(shown.get("resume_binding") if isinstance(shown, dict) else None)

    try:
        found = find(run_cmux(args.cmux, "tree", "--json"), args.url, resume_of, created_after)
    except CmuxError as error:
        sys.exit(str(error))
    for key, value in found.items():
        print(f"{key}={shlex.quote(value)}")


if __name__ == "__main__":
    main()
