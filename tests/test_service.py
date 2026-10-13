import threading
import time
import types
import unittest
from unittest import mock

from linear_sidebar import service as service_module
from linear_sidebar.config import KeychainError, Settings
from linear_sidebar.linear import LinearError, ProjectIssues
from linear_sidebar.service import SidebarService
from linear_sidebar.workspace import CmuxError, GitError, Workspace

STARTED = {"id": "s1", "name": "In Progress", "type": "started", "color": "#f2c94c", "position": 1}
TODO = {"id": "s2", "name": "Todo", "type": "unstarted", "color": "#e2e2e2", "position": 0}
BACKLOG = {"id": "s3", "name": "Backlog", "type": "backlog", "color": "#bec2c8", "position": 0}
PROJECT = {"id": "p1", "name": "Gadget", "url": "https://linear.app/acme/project/gadget"}
SETTINGS = Settings(host="127.0.0.1", port=0, cmux_bin="cmux", refresh_seconds=60)


def linear_issue(identifier, state, **extra):
    data = {"identifier": identifier, "title": f"Title of {identifier}", "priority": 0,
            "url": f"https://linear.app/acme/issue/{identifier}", "updatedAt": "", "state": state}
    data.update(extra)
    return data


def workspace(title="Gadget", branch="gad-1-login", repo="acme/gadget", ws_id="ws1"):
    return Workspace(id=ws_id, title=title, directory="/tmp/gadget", is_repo=True, branch=branch, repo=repo)


class FakeClient:
    """Answers like Linear for one project; `fail` makes every call raise."""

    def __init__(self):
        self.calls = []
        self.fail = None
        self.branch_issues = {"gad-1-login": linear_issue("GAD-1", STARTED, project=PROJECT,
                                                          parent=None, children={"nodes": []})}
        self.linked_projects = [dict(PROJECT, status={"type": "started"},
                                     externalLinks={"nodes": [{"url": "https://github.com/acme/gadget"}]})]
        self.pull_request_projects = []
        self.projects_complete = True
        self.on_project_issues = None

    def _call(self, name):
        self.calls.append(name)
        if self.fail:
            raise self.fail

    def issue_for_branch(self, branch):
        self._call("issue_for_branch")
        return self.branch_issues.get(branch)

    def issue(self, identifier):
        self._call("issue")
        return None

    def projects(self):
        self._call("projects")
        return self.linked_projects, self.projects_complete

    def projects_for_url(self, url):
        self._call("projects_for_url")
        return self.pull_request_projects

    def started_states(self):
        self._call("started_states")
        return [{"id": "s1", "position": 1, "team": {"id": "t"}}]

    def project_issues(self, project_id):
        self._call("project_issues")
        if self.on_project_issues:
            self.on_project_issues()
        return ProjectIssues([linear_issue("GAD-2", TODO), linear_issue("GAD-1", STARTED)], False,
                             [linear_issue("GAD-3", BACKLOG, createdAt="2026-10-11T10:00:00Z")], 1, False)


class Harness:
    """The service with a fake cmux, a fake Linear and a clock the test moves."""

    def __init__(self, client=None, pull_requests=(), **service_options):
        self.client = client if client is not None else FakeClient()
        self.selected = workspace()
        self.now = 1000.0
        # The wall clock moves with the test's clock; whole seconds keep the arithmetic exact.
        clock = types.SimpleNamespace(monotonic=lambda: self.now, time=lambda: 1_760_000_000 + self.now,
                                      strftime=time.strftime)
        patcher = mock.patch.object(service_module, "time", clock)
        patcher.start()
        self.stop = patcher.stop
        if "key_source" not in service_options:
            service_options["client"] = self.client
        self.service = SidebarService(SETTINGS, workspace_source=self._select,
                                      pull_request_source=lambda cmux, ws_id: list(pull_requests),
                                      client_factory=lambda key: self.client, **service_options)

    def _select(self, cmux_bin):
        if isinstance(self.selected, Exception):
            raise self.selected
        return self.selected

    def tick(self):
        self.service.tick()
        return self.service.current()


class SidebarServiceTest(unittest.TestCase):
    def setUp(self):
        mock.patch.object(service_module, "log").start()
        self.addCleanup(mock.patch.stopall)

    def harness(self, *args, **kwargs):
        harness = Harness(*args, **kwargs)
        self.addCleanup(harness.stop)
        return harness

    def test_current_issue_from_branch_and_its_project_board(self):
        view = self.harness().tick()
        self.assertEqual(view["status"], "ok")
        self.assertEqual(view["current"]["id"], "GAD-1")
        self.assertEqual(view["project"]["via"], "issue")
        self.assertEqual([r["id"] for g in view["groups"] for r in g["issues"]], ["GAD-2"])
        self.assertEqual([r["id"] for r in view["backlog"]["issues"]], ["GAD-3"])
        self.assertEqual((view["project"]["open"], view["project"]["backlog"]), (2, 1))

    def test_project_from_repo_link_on_a_branch_without_issue(self):
        harness = self.harness()
        harness.selected = workspace(branch="main")
        view = harness.tick()
        self.assertEqual((view["status"], view["project"]["via"]), ("ok", "repo"))

    def test_project_from_pull_request(self):
        harness = self.harness(pull_requests=["https://github.com/acme/other/pull/3"])
        harness.client.pull_request_projects = [PROJECT]
        harness.selected = workspace(branch="main", repo="acme/other")
        self.assertEqual(harness.tick()["project"]["via"], "pull-request")

    def test_nothing_linked(self):
        harness = self.harness()
        harness.selected = workspace(branch="main", repo="acme/unlinked")
        self.assertEqual(harness.tick()["status"], "no-project")

    def test_switching_back_shows_the_cached_view_without_reloading(self):
        harness = self.harness()
        harness.tick()
        harness.selected = workspace(branch="main", repo="acme/unlinked", ws_id="ws2")
        harness.tick()
        calls = len(harness.client.calls)
        harness.selected = workspace()
        view = harness.tick()
        self.assertEqual(view["current"]["id"], "GAD-1")
        self.assertEqual(len(harness.client.calls), calls)

    def test_refresh_reloads(self):
        harness = self.harness()
        harness.tick()
        calls = len(harness.client.calls)
        harness.service.refresh()
        harness.tick()
        self.assertGreater(len(harness.client.calls), calls)
        self.assertEqual(harness.client.calls.count("project_issues"), 2)

    def test_workspaces_of_one_project_share_its_issues_while_they_are_fresh(self):
        harness = self.harness()
        first = harness.tick()
        harness.now += SETTINGS.refresh_seconds / 2
        harness.selected = workspace(branch="main", ws_id="ws2")
        second = harness.tick()
        self.assertEqual((second["project"]["via"], harness.client.calls.count("project_issues")), ("repo", 1))
        # The second view is as old as the issues it shows, and is reloaded when they are due.
        self.assertEqual(second["updatedAt"], first["updatedAt"])
        harness.now += SETTINGS.refresh_seconds / 2 - 1
        harness.tick()
        self.assertEqual(harness.client.calls.count("project_issues"), 1)
        harness.now += 2
        harness.tick()
        self.assertEqual(harness.client.calls.count("project_issues"), 2)

    def test_issues_older_than_half_the_refresh_interval_are_fetched_again(self):
        harness = self.harness()
        harness.tick()
        harness.now += SETTINGS.refresh_seconds / 2 + 1
        harness.selected = workspace(branch="main", ws_id="ws2")
        harness.tick()
        self.assertEqual(harness.client.calls.count("project_issues"), 2)

    def test_refresh_finds_a_project_link_added_a_moment_ago(self):
        harness = self.harness()
        linked, harness.client.linked_projects = harness.client.linked_projects, []
        harness.selected = workspace(branch="main")
        self.assertEqual(harness.tick()["status"], "no-project")
        harness.client.linked_projects = linked
        harness.service.refresh()
        self.assertEqual(harness.tick()["project"]["via"], "repo")

    def test_a_cut_project_search_is_explained(self):
        harness = self.harness()
        harness.client.linked_projects, harness.client.projects_complete = [], False
        harness.selected = workspace(branch="main")
        view = harness.tick()
        self.assertEqual(view["status"], "no-project")
        self.assertIn("500", view["message"])

    def test_views_reload_after_the_refresh_interval(self):
        harness = self.harness()
        harness.tick()
        calls = len(harness.client.calls)
        harness.now += SETTINGS.refresh_seconds - 1
        harness.tick()
        self.assertEqual(len(harness.client.calls), calls)
        harness.now += 2
        harness.tick()
        self.assertGreater(len(harness.client.calls), calls)

    def test_automatic_rename_updates_the_header_without_reloading(self):
        harness = self.harness()
        harness.tick()
        calls = len(harness.client.calls)
        harness.selected = workspace(title="Fixing the login")
        view = harness.tick()
        self.assertEqual(view["workspace"]["title"], "Fixing the login")
        self.assertEqual(view["current"]["id"], "GAD-1")
        self.assertEqual(len(harness.client.calls), calls)

    def test_failed_refresh_keeps_the_last_view_with_an_error(self):
        harness = self.harness()
        harness.tick()
        harness.client.fail = LinearError("Linear API is unreachable: down")
        harness.service.refresh()
        view = harness.tick()
        self.assertEqual(view["status"], "ok")
        self.assertEqual(view["current"]["id"], "GAD-1")
        self.assertIn("unreachable", view["error"])

    def test_failed_first_load_is_an_error_view(self):
        client = FakeClient()
        client.fail = LinearError("Linear rejected the API key")
        view = self.harness(client).tick()
        self.assertEqual((view["status"], view["message"]), ("error", "Linear rejected the API key"))

    def test_cmux_errors_and_no_selection(self):
        harness = self.harness()
        harness.selected = CmuxError("cmux did not answer")
        self.assertEqual(harness.tick()["status"], "cmux-error")
        harness.selected = None
        self.assertEqual(harness.tick()["status"], "no-workspace")

    def test_a_cmux_or_git_blip_keeps_the_board_with_a_banner(self):
        for failure in (CmuxError("cmux did not answer"), GitError("git did not answer")):
            with self.subTest(failure=failure):
                harness = self.harness()
                harness.tick()
                calls = len(harness.client.calls)
                harness.selected = failure
                view = harness.tick()
                self.assertEqual((view["status"], view["current"]["id"], view["error"]), ("ok", "GAD-1", str(failure)))
                harness.selected = workspace()
                view = harness.tick()
                self.assertEqual((view["current"]["id"], view["error"]), ("GAD-1", None))
                self.assertEqual(len(harness.client.calls), calls)

    def test_without_a_key(self):
        service = SidebarService(SETTINGS, None, workspace_source=lambda cmux: workspace())
        service.tick()
        self.assertEqual(service.current()["status"], "no-key")

    def test_an_unexpected_error_does_not_stop_the_poller(self):
        harness = self.harness()
        harness.tick()
        harness.selected = RuntimeError("surprise")
        view = harness.tick()
        self.assertIn("Unexpected error", view["error"])
        harness.selected = workspace()
        view = harness.tick()
        self.assertEqual((view["current"]["id"], view["error"]), ("GAD-1", None))

    def test_an_odd_linear_answer_waits_before_the_next_try(self):
        harness = self.harness()
        harness.client.fail = KeyError("identifier")
        view = harness.tick()
        self.assertEqual(view["status"], "error")
        self.assertIn("Unexpected error", view["message"])
        calls = len(harness.client.calls)
        harness.now += service_module.ERROR_RETRY_SECONDS - 1
        harness.tick()
        self.assertEqual(len(harness.client.calls), calls)
        harness.now += 2
        harness.tick()
        self.assertGreater(len(harness.client.calls), calls)

    def test_an_odd_project_answer_is_asked_again_on_the_retry(self):
        harness = self.harness()
        fetch = harness.client.project_issues
        odd = [ProjectIssues([], False, [{"title": "an issue without an identifier"}], 1, False)]
        harness.client.project_issues = lambda project_id: odd.pop() if odd else fetch(project_id)
        self.assertIn("Unexpected error", harness.tick()["message"])
        harness.now += service_module.ERROR_RETRY_SECONDS + 1
        view = harness.tick()
        self.assertEqual((view["status"], [r["id"] for r in view["backlog"]["issues"]]), ("ok", ["GAD-3"]))

    def test_a_workspace_switch_during_a_load_does_not_show_the_old_workspace(self):
        harness = self.harness()
        harness.client.on_project_issues = lambda: setattr(harness, "selected", workspace(ws_id="ws2", branch="main",
                                                                                          repo="acme/unlinked"))
        view = harness.tick()
        self.assertEqual(view["status"], "loading")
        harness.client.on_project_issues = None
        self.assertEqual(harness.tick()["status"], "no-project")
        harness.selected = workspace()
        self.assertEqual(harness.tick()["current"]["id"], "GAD-1")


    def test_cached_views_are_bounded(self):
        harness = self.harness()
        for index in range(service_module.MAX_CACHED_VIEWS + 5):
            harness.selected = workspace(ws_id=f"ws{index}")
            harness.tick()
        self.assertEqual(len(harness.service._views), service_module.MAX_CACHED_VIEWS)


class CachedTest(unittest.TestCase):
    def test_keeps_the_most_recently_fetched_values_until_they_expire(self):
        fetched = []
        later = [0.0]
        # Every fetch moves the clock by a second.
        clock = types.SimpleNamespace(monotonic=lambda: float(len(fetched)) + later[0])
        with mock.patch.object(service_module, "time", clock):
            cache = service_module._Cached(10, lambda key: fetched.append(key) or key.upper(), size=2)
            self.assertEqual([cache.get(k) for k in ("a", "b", "c", "b", "a")], ["A", "B", "C", "B", "A"])
            self.assertEqual(fetched, ["a", "b", "c", "a"])
            later[0] = 20
            cache.get("a")
            cache.invalidate()
            cache.get("a")
        self.assertEqual(fetched, ["a", "b", "c", "a", "a", "a"])


    def test_a_failed_fetch_is_tried_again_next_time(self):
        answers = ["old", RuntimeError("Linear is down"), "new"]
        now = [0.0]

        def fetch():
            answer = answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer

        with mock.patch.object(service_module, "time", types.SimpleNamespace(monotonic=lambda: now[0])):
            cache = service_module._Cached(10, fetch)
            self.assertEqual(cache.get(), "old")
            now[0] = 20
            with self.assertRaises(RuntimeError):
                cache.get()
            self.assertEqual(cache.get(), "new")


class ApiKeyTest(unittest.TestCase):
    """The poller reads the key, so the server answers while the Keychain is slow."""

    def setUp(self):
        mock.patch.object(service_module, "log").start()
        self.addCleanup(mock.patch.stopall)

    def harness(self, key_source):
        harness = Harness(key_source=key_source)
        self.addCleanup(harness.stop)
        return harness

    def test_the_state_answers_while_the_keychain_is_read(self):
        release = threading.Event()
        reading = threading.Event()

        def slow_keychain():
            reading.set()
            release.wait(5)
            return "lin_api_test", "Keychain"

        harness = self.harness(slow_keychain)
        poller = threading.Thread(target=harness.service.tick)
        poller.start()
        self.assertTrue(reading.wait(5))
        view = harness.service.current()
        self.assertEqual(view["status"], "loading")
        self.assertIn("Keychain", view["message"])
        release.set()
        poller.join(5)
        self.assertEqual(harness.service.current()["status"], "ok")

    def test_a_missing_key_is_looked_for_again_later(self):
        keys = [(None, None), ("lin_api_test", "Keychain")]
        harness = self.harness(lambda: keys.pop(0))
        view = harness.tick()
        self.assertEqual(view["status"], "no-key")
        self.assertTrue(view["message"].endswith("install.sh --new-key"))
        harness.now += SETTINGS.refresh_seconds + 1
        self.assertEqual(harness.tick()["status"], "ok")

    def test_a_keychain_error_is_not_a_missing_key_and_waits_for_the_refresh_button(self):
        reads = []

        def locked():
            reads.append(1)
            if len(reads) == 1:
                raise KeychainError("The Keychain did not answer within 30 seconds; it may be locked")
            return "lin_api_test", "Keychain"

        harness = self.harness(locked)
        view = harness.tick()
        self.assertEqual(view["status"], "key-error")
        self.assertIn("locked", view["message"])
        harness.now += 3600
        harness.tick()
        self.assertEqual(len(reads), 1)
        harness.service.refresh()
        self.assertEqual(harness.tick()["status"], "ok")


if __name__ == "__main__":
    unittest.main()
