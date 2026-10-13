"""Keeps the sidebar view current for the workspace selected in cmux."""

from __future__ import annotations

import shlex
import threading
import time
import traceback
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import view
from .config import ROOT, KeychainError, Settings
from .linear import (LinearClient, LinearError, LINKS_PER_PROJECT, MAX_PROJECT_PAGES, PROJECTS_PER_PAGE,
                     ProjectIssues)
from .workspace import CmuxError, GitError, Workspace, issue_key, pull_requests, selected_workspace

POLL_SECONDS = 1.5
# The page polls while it is open; with no page asking, the poller stops calling cmux and Linear.
IDLE_AFTER_SECONDS = 10
PROJECTS_TTL_SECONDS = 600
STATES_TTL_SECONDS = 3600
ERROR_RETRY_SECONDS = 15
MAX_CACHED_VIEWS = 50
MAX_CACHED_PROJECTS = 20
NO_ISSUES = ProjectIssues([], False, [], 0, False)
NEW_KEY_COMMAND = f"{shlex.quote(str(ROOT / 'install.sh'))} --new-key"
UNEXPECTED_ERROR = "Unexpected error; the Linear server tab has the details"
PROJECT_SEARCH_CUT = (f"Only the first {MAX_PROJECT_PAGES * PROJECTS_PER_PAGE} Linear projects and "
                      f"{LINKS_PER_PROJECT} resource links per project were searched.")

View = Dict[str, Any]
# Returns (key, where it came from), (None, None) without a key; raises KeychainError.
KeySource = Callable[[], Tuple[Optional[str], Optional[str]]]


def log(message: str) -> None:
    print(time.strftime("%H:%M:%S"), message, flush=True)


class _Cached:
    """Values fetched at most once per `ttl` seconds for each tuple of arguments, keeping the
    `size` most recently fetched; a failed fetch is retried next time."""

    def __init__(self, ttl: float, fetch: Callable[..., Any], size: int = 1):
        self._ttl = ttl
        self._fetch = fetch
        self._size = size
        self._entries: Dict[Tuple, Tuple[Any, float]] = {}

    def get(self, *args: Any) -> Any:
        return self.entry(*args)[0]

    def entry(self, *args: Any) -> Tuple[Any, float]:
        """The value and the time.monotonic() it was fetched at."""
        entry = self._entries.get(args)
        if entry is None or time.monotonic() - entry[1] > self._ttl:
            entry = self._entries[args] = (self._fetch(*args), time.monotonic())
            while len(self._entries) > self._size:
                del self._entries[min(self._entries, key=lambda k: self._entries[k][1])]
        return entry

    def invalidate(self) -> None:
        self._entries.clear()


class SidebarService:
    """Pass `client` to use a Linear client as is, or `key_source` to read the API key
    on the poller thread, so the HTTP server answers while the Keychain is slow."""

    def __init__(
        self,
        settings: Settings,
        client: Optional[LinearClient] = None,
        workspace_source: Callable[[str], Optional[Workspace]] = selected_workspace,
        pull_request_source: Callable[[str, str], List[str]] = pull_requests,
        key_source: Optional[KeySource] = None,
        client_factory: Callable[[str], LinearClient] = LinearClient,
    ):
        self._settings = settings
        self._workspace_source = workspace_source
        self._pull_request_source = pull_request_source
        self._key_source = key_source
        self._client_factory = client_factory
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._view: View = view.state("loading")
        self._views: Dict[Tuple, Tuple[View, float]] = {}
        self._context: Optional[Tuple] = None
        # True while the screen shows a cmux, git or unexpected failure over the context's view.
        self._problem_shown = False
        self._last_problem: Optional[str] = None
        self._last_key_note: Optional[str] = None
        self._forced = False
        self._last_request = float("-inf")
        self._use_client(client)

    def _use_client(self, client: Optional[LinearClient]) -> None:
        """Sets the Linear client and forgets every view loaded without it."""
        self._client = client
        self._projects = _Cached(PROJECTS_TTL_SECONDS, lambda: client.projects() if client else ([], True))
        self._fills = _Cached(STATES_TTL_SECONDS,
                              lambda: view.started_fills(client.started_states()) if client else {})
        # Shared by the workspaces of one project, such as several branches of one repository.
        # A view is as old as the issues it shows; for half the refresh interval they are
        # fresh enough that a view built from them is not due again right away.
        self._project_issues = _Cached(self._settings.refresh_seconds / 2,
                                       lambda project_id: client.project_issues(project_id) if client else NO_ISSUES,
                                       size=MAX_CACHED_PROJECTS)
        self._views.clear()

    def current(self) -> View:
        """The view for the page; also tells the poller that someone is looking."""
        idle = time.monotonic() - self._last_request > IDLE_AFTER_SECONDS
        self._last_request = time.monotonic()
        if idle:
            self._wake.set()
        with self._lock:
            return self._view

    def refresh(self) -> None:
        self._forced = True
        self._wake.set()

    def run_forever(self) -> None:
        while True:
            if time.monotonic() - self._last_request < IDLE_AFTER_SECONDS:
                self.tick()
            self._wake.wait(POLL_SECONDS)
            self._wake.clear()

    def tick(self) -> None:
        """One poll. Never raises, so the poller thread survives anything."""
        try:
            self._tick()
        except Exception:  # noqa: BLE001 - keep polling, and show where to look
            log("Unexpected error:\n" + traceback.format_exc().rstrip())
            self._show_problem(UNEXPECTED_ERROR)

    def _publish(self, new_view: View) -> None:
        with self._lock:
            self._view = new_view

    def _show_problem(self, message: str) -> None:
        """Keeps what is on screen with a banner; the next good tick puts the context's view back."""
        with self._lock:
            shown = self._view
        if shown.get("workspace") is None or shown["status"] == "cmux-error":
            self._publish(view.state("cmux-error", message=message))
        else:
            self._publish(dict(shown, error=message))
        self._problem_shown = True

    def _tick(self) -> None:
        try:
            workspace = self._workspace_source(self._settings.cmux_bin)
        except (CmuxError, GitError) as error:
            if str(error) != self._last_problem:
                log(str(error))
                self._last_problem = str(error)
            self._show_problem(str(error))
            return
        self._last_problem = None
        if workspace is None:
            self._context = None
            self._problem_shown = False
            self._publish(view.state("no-workspace"))
            return

        context = workspace.context
        cached = self._views.get(context)
        if context != self._context or self._problem_shown:
            if context != self._context:
                log(f"{workspace.title} · {workspace.branch or 'no branch'} · {workspace.repo or 'no GitHub remote'}")
            self._context = context
            self._problem_shown = False
            self._publish(_with_workspace(cached[0], workspace) if cached else view.state("loading", workspace))
        else:
            # cmux renames workspaces on its own; keep the header current without reloading.
            with self._lock:
                if self._view.get("workspace") != view.workspace_info(workspace):
                    self._view = _with_workspace(self._view, workspace)

        stale = cached is None or time.monotonic() - cached[1] > self._settings.refresh_seconds
        if not (stale or self._forced):
            return
        if self._forced:
            self._forced = False
            # The refresh button also picks up new project links and workflow states.
            self._projects.invalidate()
            self._fills.invalidate()
            self._project_issues.invalidate()
        loaded, loaded_at = self._load(workspace, cached[0] if cached else None)
        self._remember(context, loaded, loaded_at)
        # Linear can take seconds; publish only if the user is still on this workspace.
        try:
            latest = self._workspace_source(self._settings.cmux_bin)
        except (CmuxError, GitError):
            latest = workspace
        if latest is not None and latest.context == context:
            self._publish(_with_workspace(loaded, latest))

    def _remember(self, context: Tuple, loaded: View, loaded_at: float) -> None:
        if loaded["status"] == "key-error":
            # Another read could raise the Keychain prompt again; wait for the refresh button.
            loaded_at = float("inf")
        elif loaded["status"] == "error" or loaded.get("error"):
            # Retry a failure sooner than the normal refresh.
            loaded_at -= max(0, self._settings.refresh_seconds - ERROR_RETRY_SECONDS)
        self._views[context] = (loaded, loaded_at)
        while len(self._views) > MAX_CACHED_VIEWS:
            oldest = min(self._views, key=lambda key: self._views[key][1])
            del self._views[oldest]

    def _read_key(self, workspace: Workspace) -> Optional[View]:
        """Reads the API key and makes a client; without one, returns the view to show instead."""
        if self._key_source is not None:
            # The read can wait on an unlock prompt; say so instead of a bare "Loading…".
            with self._lock:
                if self._view["status"] in ("loading", "key-error"):
                    self._view = view.state("loading", workspace,
                                            message="Reading the Linear API key from the Keychain…")
            try:
                key, source = self._key_source()
            except KeychainError as error:
                self._note_key(f"Keychain: {error}")
                return view.state("key-error", workspace, message=str(error))
            if key:
                self._note_key(f"Linear API key from {source}")
                self._use_client(self._client_factory(key))
                return None
        self._note_key(f"No Linear API key: run {NEW_KEY_COMMAND}")
        return view.state("no-key", workspace, message=NEW_KEY_COMMAND)

    def _note_key(self, message: str) -> None:
        if message != self._last_key_note:
            log(message)
            self._last_key_note = message

    def _load(self, workspace: Workspace, previous: Optional[View]) -> Tuple[View, float]:
        """The view and the time.monotonic() its Linear data is from."""
        if self._client is None:
            instead = self._read_key(workspace)
            if instead is not None:
                return instead, time.monotonic()
        try:
            issue, via = self._current_issue(workspace)
            project, project_via = (issue["project"], "issue") if issue and issue.get("project") else (None, None)
            searched_all = True
            if project is None:
                project, project_via, searched_all = self._linked_project(workspace)
            # Another workspace of the project may have fetched its issues a while ago:
            # the view is as old as they are, and is reloaded when they are due.
            found, fetched_at = (self._project_issues.entry(project["id"]) if project
                                 else (NO_ISSUES, time.monotonic()))
            updated_at = time.time() - (time.monotonic() - fetched_at)
            return view.ready(workspace, issue, via, project, project_via, found, self._fills.get(),
                              int(updated_at * 1000), message=None if searched_all else PROJECT_SEARCH_CUT), fetched_at
        except LinearError as error:
            log(f"Linear: {error}")
            message = str(error)
        except Exception:  # noqa: BLE001 - an odd answer must not skip the retry delay
            log("Unexpected error:\n" + traceback.format_exc().rstrip())
            message = UNEXPECTED_ERROR
            # The odd answer may be a project's cached issues: the retry asks Linear again.
            self._project_issues.invalidate()
        if previous and previous["status"] in ("ok", "no-project"):
            failed = _with_workspace(previous, workspace)
            failed["error"] = message
            return failed, time.monotonic()
        return view.state("error", workspace, message=message), time.monotonic()

    def _current_issue(self, workspace: Workspace) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        assert self._client is not None
        if workspace.branch:
            issue = self._client.issue_for_branch(workspace.branch)
            if issue:
                return issue, "branch"
        key = issue_key(workspace.title)
        if key:
            issue = self._client.issue(key)
            if issue:
                return issue, "title"
        return None, None

    def _linked_project(self, workspace: Workspace) -> Tuple[Optional[Dict[str, Any]], Optional[str], bool]:
        """The project linked to the repository or the branch's pull request, how it was
        found, and whether the project list was searched in full."""
        assert self._client is not None
        projects, searched_all = self._projects.get()
        project = view.project_for_repo(projects, workspace.repo)
        if project:
            return project, "repo", True
        if workspace.is_repo:
            for url in self._pull_request_source(self._settings.cmux_bin, workspace.id):
                projects = self._client.projects_for_url(url)
                if projects:
                    return projects[0], "pull-request", True
        return None, None, searched_all


def _with_workspace(source: View, workspace: Workspace) -> View:
    updated = dict(source)
    updated["workspace"] = view.workspace_info(workspace)
    return updated
