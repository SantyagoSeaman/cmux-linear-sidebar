import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_PATH = Path(__file__).resolve().parent.parent / "scripts" / "dock_surfaces.py"
_SPEC = importlib.util.spec_from_file_location("dock_surfaces", _PATH)
dock_surfaces = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(dock_surfaces)

URL = "http://127.0.0.1:47801/"
SERVER = "/Users/me/.cmux-linear-sidebar/cmux-linear-sidebar"


def pane(ref, *surfaces, dock=True):
    node = {"ref": ref, "surfaces": list(surfaces)}
    if dock:
        node["dock_scope"] = "global"
    return node


def browser(ref, url=URL):
    return {"ref": ref, "type": "browser", "url": url}


def terminal(ref, title="Terminal"):
    return {"ref": ref, "type": "terminal", "title": title}


def tree(*panes):
    return {"windows": [{"workspaces": [{"panes": list(panes)}]}]}


def resumes(bindings=None):
    """A resume lookup that answers from `bindings` (ref -> argv) and records what it was asked."""
    asked = []

    def resume_of(ref):
        asked.append(ref)
        return (bindings or {}).get(ref, [])
    resume_of.asked = asked
    return resume_of


class FindTest(unittest.TestCase):
    def test_page_with_its_titled_server_tab(self):
        found = dock_surfaces.find(tree(pane("pane:1", browser("s:1"), terminal("s:2", "Linear server"))), URL,
                                   resumes({"s:2": [SERVER]}))
        self.assertEqual(found, {"PAGE_PANE": "pane:1", "PAGE_SURFACE": "s:1", "SERVER_SURFACE": "s:2",
                                 "SERVER_RESUME": SERVER, "PANE_TERMINALS": "s:2"})

    def test_no_page(self):
        found = dock_surfaces.find(tree(pane("pane:1", browser("s:1", "https://example.com/"))), URL, resumes())
        self.assertEqual(found, {"PAGE_PANE": "", "PAGE_SURFACE": "", "SERVER_SURFACE": "", "SERVER_RESUME": "",
                                 "PANE_TERMINALS": ""})

    def test_pages_outside_the_dock_are_ignored(self):
        found = dock_surfaces.find(tree(pane("pane:1", browser("s:1"), dock=False)), URL, resumes())
        self.assertEqual(found["PAGE_PANE"], "")

    def test_a_stopped_server_is_known_by_its_resume_binding(self):
        found = dock_surfaces.find(tree(pane("pane:1", browser("s:1"), terminal("s:9", "zsh"), terminal("s:2", "zsh"))),
                                   URL, resumes({"s:2": [SERVER, "--port", "47802"]}))
        self.assertEqual((found["SERVER_SURFACE"], found["SERVER_RESUME"]), ("s:2", f"{SERVER} --port 47802"))

    def test_a_users_only_terminal_is_never_the_server(self):
        for title in ("zsh", "", "Terminal"):
            with self.subTest(title=title):
                found = dock_surfaces.find(tree(pane("pane:1", browser("s:1"), terminal("s:2", title))), URL,
                                           resumes({"s:2": ["/usr/local/bin/claude"]}))
                self.assertEqual((found["PAGE_SURFACE"], found["SERVER_SURFACE"], found["PANE_TERMINALS"]),
                                 ("s:1", "", "s:2"))

    def test_the_terminal_this_run_opened_counts_before_its_title_is_set(self):
        found = dock_surfaces.find(tree(pane("pane:1", browser("s:1"), terminal("s:9", "zsh"), terminal("s:2", ""))),
                                   URL, resumes(), created_after={"s:9"})
        self.assertEqual(found["SERVER_SURFACE"], "s:2")

    def test_two_new_terminals_are_left_alone(self):
        found = dock_surfaces.find(tree(pane("pane:1", browser("s:1"), terminal("s:2"), terminal("s:3"))),
                                   URL, resumes(), created_after=set())
        self.assertEqual(found["SERVER_SURFACE"], "")

    def test_the_title_is_enough_without_asking_for_bindings_of_other_tabs(self):
        resume_of = resumes()
        dock_surfaces.find(tree(pane("pane:1", terminal("s:9", "zsh"), browser("s:1"), terminal("s:2", "Linear server"))),
                           URL, resume_of)
        self.assertEqual(resume_of.asked, ["s:2"])


class ResumeArgumentsTest(unittest.TestCase):
    def test_reads_the_launch_command_or_the_command_string(self):
        self.assertEqual(dock_surfaces.resume_arguments({"launch_command": {"arguments": [SERVER]}}), [SERVER])
        self.assertEqual(dock_surfaces.resume_arguments({"command": f"'{SERVER}' --port 1"}), [SERVER, "--port", "1"])

    def test_odd_bindings_have_no_command(self):
        for binding in (None, "text", {}, {"command": "'unclosed"}, {"launch_command": {"arguments": [1]}}):
            with self.subTest(binding=binding):
                self.assertFalse(dock_surfaces.runs_server(dock_surfaces.resume_arguments(binding)))

    def test_a_command_that_only_mentions_the_folder_is_not_the_server(self):
        # Agent bindings start with `cd -- '<folder>'`, and the folder has the program's name.
        binding = {"command": "cd -- '/Users/me/.cmux-linear-sidebar' && claude"}
        self.assertFalse(dock_surfaces.runs_server(dock_surfaces.resume_arguments(binding)))


class MainTest(unittest.TestCase):
    """The script against a fake cmux executable."""

    def run_script(self, tree_output, bindings=None, *args):
        with tempfile.TemporaryDirectory() as directory:
            cmux = os.path.join(directory, "cmux")
            responses = {"tree": tree_output, "bindings": bindings or {}}
            with open(os.path.join(directory, "responses.json"), "w", encoding="utf-8") as file:
                json.dump(responses, file)
            with open(cmux, "w", encoding="utf-8") as script:
                script.write(f"""#!{sys.executable}
import json, os, sys
responses = json.load(open(os.path.join(os.path.dirname(__file__), "responses.json")))
if sys.argv[1:3] == ["tree", "--json"]:
    print(responses["tree"])
elif sys.argv[1:4] == ["surface", "resume", "show"]:
    ref = sys.argv[sys.argv.index("--surface") + 1]
    argv = responses["bindings"].get(ref)
    print(json.dumps({{"resume_binding": {{"launch_command": {{"arguments": argv}}}} if argv else None}}))
else:
    sys.exit("unexpected " + " ".join(sys.argv[1:]))
""")
            os.chmod(cmux, os.stat(cmux).st_mode | stat.S_IEXEC)
            return subprocess.run([sys.executable, str(_PATH), cmux, URL, *args],
                                  capture_output=True, text=True, timeout=30)

    def test_prints_shell_assignments(self):
        layout = tree(pane("pane:1", browser("s:1"), terminal("s:2", "zsh")))
        result = self.run_script(json.dumps(layout), {"s:2": [SERVER]})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("SERVER_SURFACE=s:2", result.stdout.splitlines())
        self.assertIn(f"SERVER_RESUME={SERVER}", result.stdout.splitlines())

    def test_unreadable_tree_fails_with_a_message_and_no_assignments(self):
        result = self.run_script("not json")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("unreadable", result.stderr)


if __name__ == "__main__":
    unittest.main()
