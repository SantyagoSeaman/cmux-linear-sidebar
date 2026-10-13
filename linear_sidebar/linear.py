"""Linear GraphQL client and the queries the sidebar needs."""

from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.request
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

ENDPOINT = "https://api.linear.app/graphql"
TIMEOUT_SECONDS = 8
PAGE_SIZE = 250  # Linear's maximum for `first`.
PROJECTS_PER_PAGE = 50
MAX_PROJECT_PAGES = 10
LINKS_PER_PROJECT = 50
BACKLOG_LIMIT = 100

_STATE = "state { id name type color position }"
_ISSUE = f"identifier title url priority updatedAt {_STATE}"
_LISTED = f"{_ISSUE} completedAt canceledAt parent {{ identifier }}"
_BACKLOG = f"{_ISSUE} createdAt"
_IN_BACKLOG = 'project: { id: { eq: $projectId } }, state: { type: { eq: "backlog" } }'
_CURRENT = (
    f"{_ISSUE} project {{ id name url }}"
    f" parent {{ identifier title url }}"
    f" children(first: {PAGE_SIZE}) {{ nodes {{ {_ISSUE} }} pageInfo {{ hasNextPage }} }}"
)

ISSUE_BY_BRANCH = f"query($branch: String!) {{ issueVcsBranchSearch(branchName: $branch) {{ {_CURRENT} }} }}"
ISSUE_BY_ID = f"query($id: String!) {{ issue(id: $id) {{ {_CURRENT} }} }}"
PROJECTS = f"""query($after: String) {{
  projects(first: {PROJECTS_PER_PAGE}, after: $after) {{
    nodes {{
      id name url status {{ type }}
      externalLinks(first: {LINKS_PER_PROJECT}) {{ nodes {{ url }} pageInfo {{ hasNextPage }} }}
    }}
    pageInfo {{ hasNextPage endCursor }}
  }}
}}"""
PROJECTS_FOR_URL = "query($url: String!) { attachmentsForURL(url: $url) { nodes { issue { project { id name url } } } } }"
STARTED_STATES = (
    f'query {{ workflowStates(first: {PAGE_SIZE}, filter: {{ type: {{ eq: "started" }} }})'
    " { nodes { id position team { id } } } }"
)
# The board's "current" issues: started (In Progress, In Review) and unstarted (Todo)
# states, plus issues closed during the last 24 hours. In the same request, the newest
# backlog issues (orderBy createdAt returns the most recently created first), and the
# ids of up to a page of backlog issues to count them.
PROJECT_ISSUES = f"""query($projectId: ID!) {{
  board: issues(first: {PAGE_SIZE}, filter: {{
    project: {{ id: {{ eq: $projectId }} }}
    or: [
      {{ state: {{ type: {{ in: ["started", "unstarted"] }} }} }}
      {{ completedAt: {{ gt: "-P1D" }} }}
      {{ canceledAt: {{ gt: "-P1D" }} }}
    ]
  }}) {{ nodes {{ {_LISTED} }} pageInfo {{ hasNextPage }} }}
  backlog: issues(first: {BACKLOG_LIMIT}, orderBy: createdAt, filter: {{ {_IN_BACKLOG} }}) {{ nodes {{ {_BACKLOG} }} }}
  backlogIds: issues(first: {PAGE_SIZE}, filter: {{ {_IN_BACKLOG} }}) {{ nodes {{ id }} pageInfo {{ hasNextPage }} }}
}}"""

Node = Dict[str, Any]


class ProjectIssues(NamedTuple):
    """A project's board, its newest backlog issues, and how many backlog issues it has.

    `board_more` says Linear had more board issues than one page; `backlog_count_more`
    that it has more backlog issues than one page, so `backlog_count` is a lower bound.
    """

    board: List[Node]
    board_more: bool
    backlog: List[Node]
    backlog_count: int
    backlog_count_more: bool


class LinearError(Exception):
    pass


class LinearNotFound(LinearError):
    """Linear answered that the requested entity does not exist."""


def _nodes(data: Node, key: str) -> List[Node]:
    return [n for n in ((data.get(key) or {}).get("nodes") or []) if isinstance(n, dict)]


def _has_next_page(connection: Any) -> bool:
    info = connection.get("pageInfo") if isinstance(connection, dict) else None
    return bool(isinstance(info, dict) and info.get("hasNextPage"))


def _graphql_error(errors: Any) -> LinearError:
    """The first GraphQL error as a LinearError, whatever shape Linear gave it."""
    first = errors[0] if isinstance(errors, list) else errors
    message = first.get("message") if isinstance(first, dict) else None
    if not isinstance(message, str) or not message.strip():
        return LinearError("Linear API returned an error without a message")
    message = message.strip()
    if message.startswith("Entity not found"):
        return LinearNotFound(message)
    return LinearError(message)


class LinearClient:
    def __init__(self, api_key: str, timeout: float = TIMEOUT_SECONDS):
        self._api_key = api_key
        self._timeout = timeout

    def query(self, query: str, **variables: Any) -> Node:
        request = urllib.request.Request(
            ENDPOINT,
            data=json.dumps({"query": query, "variables": variables}).encode(),
            headers={
                "Content-Type": "application/json",
                "User-Agent": "cmux-linear-sidebar",
            },
        )
        # Never forwarded if Linear ever answers with a redirect to another host.
        request.add_unredirected_header("Authorization", self._api_key)
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as error:
            with error:
                if error.code in (401, 403):
                    raise LinearError("Linear rejected the API key") from error
                payload = _json_or_none(error)
            if not isinstance(payload, dict) or not payload.get("errors"):
                raise LinearError(f"Linear API returned HTTP {error.code}") from error
        except (urllib.error.URLError, OSError) as error:
            raise LinearError(f"Linear API is unreachable: {getattr(error, 'reason', error)}") from error
        except (ValueError, http.client.HTTPException) as error:
            raise LinearError("Linear API returned an unreadable answer") from error

        if not isinstance(payload, dict):
            raise LinearError("Linear API returned an unreadable answer")
        errors = payload.get("errors")
        if errors:
            raise _graphql_error(errors)
        data = payload.get("data")
        if not isinstance(data, dict):
            raise LinearError("Linear API returned no data")
        return data

    def viewer(self) -> Node:
        return self.query("query { viewer { name email } }").get("viewer") or {}

    def issue_for_branch(self, branch: str) -> Optional[Node]:
        return self.query(ISSUE_BY_BRANCH, branch=branch).get("issueVcsBranchSearch")

    def issue(self, identifier: str) -> Optional[Node]:
        try:
            return self.query(ISSUE_BY_ID, id=identifier).get("issue")
        except LinearNotFound:
            return None  # Linear reports an unknown identifier as an error, not null.

    def projects(self) -> Tuple[List[Node], bool]:
        """Every project the key can see, up to MAX_PROJECT_PAGES pages, and whether that was
        all of them with all their resource links."""
        projects: List[Node] = []
        after = None
        complete = False
        for _ in range(MAX_PROJECT_PAGES):
            page = self.query(PROJECTS, after=after).get("projects") or {}
            projects.extend(_nodes({"projects": page}, "projects"))
            after = (page.get("pageInfo") or {}).get("endCursor")
            if not _has_next_page(page) or not after:
                complete = not _has_next_page(page)
                break
        links_complete = not any(_has_next_page(p.get("externalLinks")) for p in projects)
        return projects, complete and links_complete

    def projects_for_url(self, url: str) -> List[Node]:
        nodes = _nodes(self.query(PROJECTS_FOR_URL, url=url), "attachmentsForURL")
        projects = ((n.get("issue") or {}).get("project") for n in nodes)
        return [p for p in projects if p]

    def started_states(self) -> List[Node]:
        return _nodes(self.query(STARTED_STATES), "workflowStates")

    def project_issues(self, project_id: str) -> ProjectIssues:
        """The project's current issues, its BACKLOG_LIMIT newest backlog issues and the size
        of its backlog, in one request."""
        data = self.query(PROJECT_ISSUES, projectId=project_id)
        return ProjectIssues(_nodes(data, "board"), _has_next_page(data.get("board")), _nodes(data, "backlog"),
                             len(_nodes(data, "backlogIds")), _has_next_page(data.get("backlogIds")))


def _json_or_none(error: urllib.error.HTTPError) -> Optional[Any]:
    try:
        return json.loads(error.read())
    except (ValueError, OSError, http.client.HTTPException):
        return None
