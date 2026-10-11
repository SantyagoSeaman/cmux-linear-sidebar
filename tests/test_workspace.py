import json
import os
import stat
import subprocess
import tempfile
import unittest
from unittest import mock

from linear_sidebar import workspace as workspace_module
from linear_sidebar.workspace import (CmuxError, GitError, git_head, github_repo, issue_key, pull_requests,
                                      selected_workspace)


def git(directory, *args):
    subprocess.run(["git", "-C", directory, *args], check=True, capture_output=True)


def fake_cmux(directory, output, status=0):
    """An executable that prints `output` whatever it is asked, like a canned cmux."""
    path = os.path.join(directory, "cmux")
    with open(path, "w", encoding="utf-8") as script:
        script.write(f"#!/bin/sh\ncat <<'EOF'\n{output}\nEOF\nexit {status}\n")
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    return path


class GithubRepoTest(unittest.TestCase):
    def test_remote_and_web_url_forms(self):
        cases = {
            "git@github.com:Acme/widget-internal.git": "Acme/widget-internal",
            "https://github.com/Acme/gadget.git": "Acme/gadget",
            "ssh://git@github.com/acme/tool": "acme/tool",
            "ssh://git@github.com:22/acme/tool.git": "acme/tool",
            "https://github.com/acme/tool/blob/main/docs/releasing.md": "acme/tool",
            "https://github.com/acme/tool/pull/147": "acme/tool",
            "https://www.github.com/acme/tool#readme": "acme/tool",
            "github.com/acme/tool": "acme/tool",
            "git+ssh://git@github.com/acme/tool.git": "acme/tool",
            " https://GitHub.com/acme/tool/ ": "acme/tool",
        }
        for url, expected in cases.items():
            with self.subTest(url=url):
                self.assertEqual(github_repo(url), expected)

    def test_non_github_urls(self):
        for url in (None, "", "https://pypi.org/project/widget/", "git@gitlab.com:acme/tool.git",
                    "https://notgithub.com/acme/tool", "git@notgithub.com:acme/tool.git",
                    "https://github.com.evil.example/acme/tool", "https://gitlab.com/github.com/acme/repo",
                    "https://example.com/mirror/github.com/acme/repo", "https://github.com/acme",
                    "ftp://github.com/acme/tool", "https://[github.com/acme/tool"):
            with self.subTest(url=url):
                self.assertIsNone(github_repo(url))


class IssueKeyTest(unittest.TestCase):
    def test_whole_title_must_be_an_identifier(self):
        self.assertEqual(issue_key(" san-304 "), "SAN-304")
        self.assertIsNone(issue_key("Café login fix"))
        self.assertIsNone(issue_key("SAN-304 dialogue"))
        self.assertIsNone(issue_key("SAN-304\nmore"))
        self.assertIsNone(issue_key(""))


class GitHeadTest(unittest.TestCase):
    # `git init -b` needs git 2.28 or newer; Apple's command line tools have it.
    def test_repository_without_commits_has_its_branch(self):
        with tempfile.TemporaryDirectory() as directory:
            git(directory, "init", "-q", "-b", "trunk")
            self.assertEqual(git_head(directory), (True, "trunk"))

    def test_detached_head_is_a_repository_without_branch(self):
        with tempfile.TemporaryDirectory() as directory:
            git(directory, "init", "-q", "-b", "trunk")
            git(directory, "-c", "user.name=t", "-c", "user.email=t@example.com",
                "commit", "-q", "--allow-empty", "-m", "first")
            git(directory, "checkout", "-q", "--detach")
            self.assertEqual(git_head(directory), (True, None))

    def test_plain_folder_is_not_a_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(git_head(directory), (False, None))

    def test_git_that_does_not_answer_is_an_error_not_a_plain_folder(self):
        for failure in (subprocess.TimeoutExpired(["git"], 5), FileNotFoundError(2, "No such file", "git")):
            with self.subTest(failure=failure):
                with mock.patch.object(workspace_module.subprocess, "run", side_effect=failure):
                    with self.assertRaises(GitError):
                        git_head("/tmp")


class SelectedWorkspaceTest(unittest.TestCase):
    def test_reads_the_selected_workspace_and_its_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = os.path.join(directory, "repo")
            os.mkdir(repo)
            git(repo, "init", "-q", "-b", "gad-7-login")
            git(repo, "remote", "add", "origin", "git@github.com:acme/gadget.git")
            listing = {"workspaces": [
                {"id": "a", "title": "Other", "selected": False, "current_directory": directory},
                "not a workspace",
                {"id": "b", "title": "Gadget", "selected": True, "current_directory": repo},
            ]}
            ws = selected_workspace(fake_cmux(directory, json.dumps(listing)))
        self.assertEqual((ws.id, ws.title, ws.is_repo, ws.branch, ws.repo),
                         ("b", "Gadget", True, "gad-7-login", "acme/gadget"))

    def test_plain_list_and_nothing_selected(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertIsNone(selected_workspace(fake_cmux(directory, json.dumps([{"id": "a"}]))))

    def test_missing_directory_is_not_a_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            ws = selected_workspace(fake_cmux(directory, json.dumps([{"id": "a", "selected": True}])))
        self.assertEqual((ws.directory, ws.is_repo, ws.branch), (None, False, None))

    def test_git_timeout_reading_the_remote_is_an_error(self):
        real_run = subprocess.run

        def slow_remote(args, **kwargs):
            if "remote" in args:
                raise subprocess.TimeoutExpired(args, 5)
            return real_run(args, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            git(directory, "init", "-q", "-b", "gad-7-login")
            listing = json.dumps([{"id": "a", "selected": True, "current_directory": directory}])
            cmux = fake_cmux(directory, listing)
            with mock.patch.object(workspace_module.subprocess, "run", side_effect=slow_remote):
                with self.assertRaises(GitError):
                    selected_workspace(cmux)

    def test_unreadable_or_failing_cmux(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(CmuxError):
                selected_workspace(fake_cmux(directory, "not json"))
            with self.assertRaises(CmuxError):
                selected_workspace(fake_cmux(directory, "{}", status=1))
            with self.assertRaises(CmuxError):
                selected_workspace(fake_cmux(directory, json.dumps({"workspaces": "none"})))


class PullRequestsTest(unittest.TestCase):
    def test_finds_pull_request_urls_in_sidebar_state(self):
        sample = "git_branch=feature/x dirty\npr=#147 open https://github.com/acme/gadget/pull/147\npr_label=PR"
        with tempfile.TemporaryDirectory() as directory:
            urls = pull_requests(fake_cmux(directory, sample), "ws")
        self.assertEqual(urls, ["https://github.com/acme/gadget/pull/147"])


if __name__ == "__main__":
    unittest.main()
