"""scripts/dock-setup.sh and scripts/common.sh against fake cmux, curl, lsof and ps.

The fakes share one JSON state file, so a test sets up the Dock and the port, runs the
script with bash (macOS's bash 3.2 where it exists) and reads what the script did.
uninstall.sh is not run: it deletes the real Keychain item.
"""

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER = str(ROOT / "cmux-linear-sidebar")
BASH = "/bin/bash" if os.path.exists("/bin/bash") else shutil.which("bash")
URL = "http://127.0.0.1:47801/"

FAKE = r'''
import json, os, sys

STATE = os.environ["FAKE_STATE"]
with open(STATE, encoding="utf-8") as file:
    state = json.load(file)
tool, args = os.path.basename(sys.argv[0]), sys.argv[1:]


def save():
    with open(STATE, "w", encoding="utf-8") as file:
        json.dump(state, file)


def alive(server):
    if not server.get("pid"):
        return False
    if not server.get("real"):
        return True
    try:
        os.kill(int(server["pid"]), 0)
        return True
    except OSError:
        return False


def option(name):
    return args[args.index(name) + 1]


server = state["server"]
if tool == "curl":
    if not (server.get("answers") and alive(server)):
        sys.exit(7)
    print(server.get("body") or json.dumps({"app": "cmux-linear-sidebar", "status": "ok"}))
elif tool == "lsof":
    if not alive(server):
        sys.exit(1)
    print(server["pid"])
elif tool == "ps":
    print(server.get("command", ""))
elif tool == "cmux":
    state["log"].append(args)
    command = args[0]
    if command in state.get("fail", []):
        save()
        sys.exit("boom")
    if args[:2] == ["tree", "--json"]:
        print(state["tree"] if state.get("tree") is not None
              else json.dumps({"windows": [{"panes": state["panes"]}]}))
    elif args[:3] == ["surface", "resume", "show"]:
        argv = state["bindings"].get(option("--surface"))
        print(json.dumps({"resume_binding": {"launch_command": {"arguments": argv}, "auto_resume": False}
                          if argv else None}))
    elif args[:3] == ["surface", "resume", "set"]:
        state["bindings"][option("--surface")] = args[args.index("--") + 1:]
    elif command == "new-pane":
        state["panes"].append({"ref": "pane:new", "dock_scope": "global",
                               "surfaces": [{"ref": "surface:page", "type": "browser", "url": option("--url")}]})
    elif command == "new-surface":
        pane = next(p for p in state["panes"] if p["ref"] == option("--pane"))
        pane["surfaces"].append({"ref": "surface:new", "type": "terminal", "title": ""})
    else:
        print("OK")
    if command in state.get("start_server_on", []):
        state["server"] = {"answers": True, "pid": "4242", "command": "python3 " + SERVER}
    save()
'''.replace("SERVER", repr(SERVER))


def page_pane(*terminals, url=URL):
    return {"ref": "pane:1", "dock_scope": "global",
            "surfaces": [{"ref": "surface:page", "type": "browser", "url": url}, *terminals]}


def terminal(ref, title):
    return {"ref": ref, "type": "terminal", "title": title}


class ScriptTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.directory)
        self.bin = os.path.join(self.directory, "bin")
        os.mkdir(self.bin)
        for tool in ("cmux", "curl", "lsof", "ps"):
            path = os.path.join(self.bin, tool)
            with open(path, "w", encoding="utf-8") as script:
                script.write(f"#!{sys.executable}\n{FAKE}")
            os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
        self.state_path = os.path.join(self.directory, "state.json")
        self.state = {"panes": [], "bindings": {}, "log": [], "server": {}}

    def run_bash(self, script, *args, **env):
        with open(self.state_path, "w", encoding="utf-8") as file:
            json.dump(self.state, file)
        environment = dict(os.environ, PATH=f"{self.bin}{os.pathsep}{os.environ['PATH']}",
                           CMUX_BUNDLED_CLI_PATH=os.path.join(self.bin, "cmux"), FAKE_STATE=self.state_path)
        environment.pop("LINEAR_SIDEBAR_PORT", None)
        environment.pop("LINEAR_SIDEBAR_REFRESH_SECONDS", None)
        environment.update(env)
        result = subprocess.run([BASH, *script, *args], capture_output=True, text=True, timeout=60,
                                env=environment, cwd=self.directory)
        with open(self.state_path, encoding="utf-8") as file:
            self.state = json.load(file)
        return result

    def dock_setup(self, *args, **env):
        return self.run_bash([str(ROOT / "scripts" / "dock-setup.sh")], *args, **env)

    def cmux_calls(self, command):
        return [call for call in self.state["log"] if call[0] == command]

    def a_process(self):
        """A real process standing in for a server; reaped as soon as it exits."""
        process = subprocess.Popen(["sleep", "60"])
        threading.Thread(target=process.wait, daemon=True).start()
        self.addCleanup(process.kill)
        return process

    def test_an_unreadable_dock_stops_before_anything_is_opened(self):
        self.state["tree"] = "not json"
        result = self.dock_setup()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Could not read the cmux Dock", result.stderr)
        self.assertEqual(self.cmux_calls("new-pane"), [])

    def test_a_failing_cmux_command_says_so(self):
        self.state["fail"] = ["new-pane"]
        result = self.dock_setup()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cmux new-pane failed: boom", result.stderr)

    def test_a_users_only_terminal_beside_the_page_is_left_alone(self):
        self.state["panes"] = [page_pane(terminal("surface:mine", "zsh"))]
        self.state["start_server_on"] = ["new-surface"]
        result = self.dock_setup()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.cmux_calls("new-surface")), 1)
        self.assertEqual(self.cmux_calls("send"), [])
        self.assertEqual(self.state["bindings"], {"surface:new": [SERVER]})

    def test_a_custom_port_reaches_the_command_and_the_resume_binding(self):
        self.state["panes"] = [page_pane(url="http://127.0.0.1:47999/")]
        self.state["start_server_on"] = ["new-surface"]
        result = self.dock_setup(LINEAR_SIDEBAR_PORT="47999", LINEAR_SIDEBAR_REFRESH_SECONDS="30")
        self.assertEqual(result.returncode, 0, result.stderr)
        typed = self.cmux_calls("new-surface")[0]
        self.assertTrue(typed[typed.index("--command") + 1].endswith(" --port 47999 --refresh-seconds 30"))
        self.assertEqual(self.state["bindings"]["surface:new"], [SERVER, "--port", "47999", "--refresh-seconds", "30"])

    def test_bad_settings_stop_before_cmux_is_asked(self):
        for env in ({"LINEAR_SIDEBAR_PORT": "80a"}, {"LINEAR_SIDEBAR_PORT": "70000"},
                    {"LINEAR_SIDEBAR_REFRESH_SECONDS": "0"}):
            with self.subTest(env=env):
                result = self.dock_setup(**env)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.state["log"], [])

    def test_restart_stops_our_server_and_starts_it_again_in_its_tab(self):
        server = self.a_process()
        self.state["panes"] = [page_pane(terminal("surface:server", "Linear server"))]
        self.state["bindings"] = {"surface:server": [SERVER]}
        self.state["server"] = {"answers": True, "pid": str(server.pid), "real": True, "command": f"Python {SERVER}"}
        self.state["start_server_on"] = ["send"]
        result = self.dock_setup("--restart")
        self.assertEqual(result.returncode, 0, result.stderr)
        server.wait(5)
        self.assertIn("Stopped the server.", result.stdout)
        self.assertEqual([call[call.index("--surface") + 1] for call in self.cmux_calls("send")], ["surface:server"])
        # The binding did not change, so cmux keeps the user's approval.
        self.assertEqual([c for c in self.cmux_calls("surface") if c[:3] == ["surface", "resume", "set"]], [])

    def test_another_program_on_the_port_is_never_stopped(self):
        other = self.a_process()
        self.state["panes"] = [page_pane()]
        self.state["server"] = {"answers": True, "pid": str(other.pid), "real": True,
                                "command": "python3 -m http.server 47801", "body": '{"status": "ok"}'}
        result = self.dock_setup("--restart")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("already in use by another program", result.stderr)
        self.assertIsNone(other.poll())
        self.assertEqual(self.cmux_calls("new-surface"), [])

    def test_stopping_a_server_that_already_exited_does_not_abort(self):
        result = self.run_bash(["-c", f'set -euo pipefail; . "{ROOT}/scripts/common.sh"; stop_server 999999; echo next'])
        self.assertEqual((result.returncode, result.stdout.strip()), (0, "next"))

    def test_find_tabs_fails_when_the_parser_fails(self):
        self.state["tree"] = "not json"
        result = self.run_bash(["-c", f'set -euo pipefail; . "{ROOT}/scripts/common.sh"; find_tabs; echo survived'])
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("survived", result.stdout)


if __name__ == "__main__":
    unittest.main()
