"""Builds the JSON view the sidebar page renders. Pure functions, no I/O."""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .workspace import Workspace, github_repo

Issue = Dict[str, Any]

CLOSED_TYPES = ("completed", "canceled", "duplicate")
DONE_COLOR = "#5e6ad2"
FALLBACK_COLOR = "#95a2b3"
_TYPE_ORDER = {"started": 0, "unstarted": 1, "backlog": 2, "triage": 3}
# Active projects first when several link the same repository.
_PROJECT_STATUS_ORDER = {"started": 0, "planned": 1, "backlog": 2, "paused": 3, "completed": 4, "canceled": 5}
_HEX_COLOR = re.compile(r"#[0-9a-fA-F]{3,8}")


def project_for_repo(projects: Iterable[Dict[str, Any]], repo: Optional[str]) -> Optional[Dict[str, Any]]:
    """The project with a resource link into the GitHub repository `owner/name`.

    When several projects link it, an active one wins over a finished one.
    """
    if not repo:
        return None
    matches = []
    for project in projects:
        for link in (project.get("externalLinks") or {}).get("nodes") or []:
            linked = github_repo((link or {}).get("url"))
            if linked and linked.lower() == repo.lower():
                matches.append(project)
                break
    if not matches:
        return None
    return min(matches, key=lambda p: _PROJECT_STATUS_ORDER.get((p.get("status") or {}).get("type"), 6))


def started_fills(states: Iterable[Dict[str, Any]]) -> Dict[str, float]:
    """Pie fill per `started` state id: a team's first started state is half full, its last three quarters."""
    by_team: Dict[str, List[Dict[str, Any]]] = {}
    for state in states:
        by_team.setdefault((state.get("team") or {}).get("id", ""), []).append(state)
    fills: Dict[str, float] = {}
    for team_states in by_team.values():
        ordered = sorted(team_states, key=lambda s: s.get("position") or 0)
        last = len(ordered) - 1
        for index, state in enumerate(ordered):
            fills[state["id"]] = 0.5 + (0.25 * index / last if last else 0)
    return fills


def icon(state: Dict[str, Any], fills: Dict[str, float]) -> Dict[str, Any]:
    kind = state.get("type")
    if kind == "completed":
        shape = "done"
    elif kind in ("canceled", "duplicate"):
        shape = "canceled"
    elif kind in ("started", "backlog"):
        shape = kind
    else:
        shape = "unstarted"
    color = state.get("color") or ""
    result: Dict[str, Any] = {"shape": shape, "color": color if _HEX_COLOR.fullmatch(color) else FALLBACK_COLOR}
    if shape == "started":
        result["fill"] = fills.get(state.get("id") or "", 0.5)
    return result


def row(issue: Issue, fills: Dict[str, float]) -> Dict[str, Any]:
    return {
        "id": issue["identifier"],
        "title": issue["title"],
        "url": issue["url"],
        "state": issue["state"]["name"],
        "icon": icon(issue["state"], fills),
    }


def _number(identifier: str) -> int:
    tail = identifier.rsplit("-", 1)[-1]
    return int(tail) if tail.isdigit() else 0


def _by_priority(issues: Sequence[Issue]) -> List[Issue]:
    """Urgent first, no priority last; most recently updated first within a priority."""
    newest_first = sorted(issues, key=lambda i: i.get("updatedAt") or "", reverse=True)
    return sorted(newest_first, key=lambda i: i.get("priority") or 5)


def board(issues: Sequence[Issue], exclude: Iterable[str], fills: Dict[str, float]) -> List[Dict[str, Any]]:
    """Open issues grouped by workflow state, then one group of issues closed in the last 24 hours.

    States of different teams with the same type and name form one group, as on
    Linear's project board. Issues in `exclude` are left out: the current
    issue's card shows them.
    """
    excluded = set(exclude)
    open_states: Dict[Tuple[str, str], Dict[str, Any]] = {}
    open_issues: Dict[Tuple[str, str], List[Issue]] = {}
    closed: List[Issue] = []
    for issue in issues:
        if issue["identifier"] in excluded:
            continue
        state = issue["state"]
        if state["type"] in CLOSED_TYPES:
            closed.append(issue)
            continue
        key = (state["type"], state["name"])
        known = open_states.get(key)
        if known is None or (state.get("position") or 0) < (known.get("position") or 0):
            open_states[key] = state
        open_issues.setdefault(key, []).append(issue)

    groups = []
    ordered = sorted(open_states.items(),
                     key=lambda item: (_TYPE_ORDER.get(item[0][0], 9), item[1].get("position") or 0))
    for key, state in ordered:
        members = _by_priority(open_issues[key])
        groups.append({
            "key": f"state:{state['type']}:{state['name']}",
            "name": state["name"],
            "icon": icon(state, fills),
            "count": len(members),
            "issues": [row(i, fills) for i in members],
            "collapsed": False,
        })
    if closed:
        recent = sorted(closed, key=lambda i: i.get("completedAt") or i.get("canceledAt") or "", reverse=True)
        done_color = next((i["state"].get("color") for i in closed if i["state"]["type"] == "completed"), None)
        groups.append({
            "key": "closed-24h",
            "name": "Closed · 24h",
            "icon": icon({"type": "completed", "color": done_color or DONE_COLOR}, fills),
            "count": len(recent),
            "issues": [row(i, fills) for i in recent],
            "collapsed": True,
        })
    return groups


def current_card(issue: Issue, via: str, fills: Dict[str, float]) -> Dict[str, Any]:
    """The current issue with its parent and open sub-issues; `progress.more` says
    Linear has more sub-issues than one page, so the counts are a lower bound."""
    connection = issue.get("children") or {}
    children = [c for c in (connection.get("nodes") or []) if isinstance(c, dict)]
    more = bool((connection.get("pageInfo") or {}).get("hasNextPage"))
    open_children = sorted(
        (c for c in children if c["state"]["type"] not in CLOSED_TYPES),
        key=lambda c: (_TYPE_ORDER.get(c["state"]["type"], 9), c["state"].get("position") or 0,
                       _number(c["identifier"])),
    )
    parent = issue.get("parent")
    card = row(issue, fills)
    card.update({
        "via": via,
        "parent": {"id": parent["identifier"], "title": parent["title"], "url": parent["url"]} if parent else None,
        "progress": {"done": len(children) - len(open_children), "total": len(children), "more": more}
        if children else None,
        "subIssues": [row(c, fills) for c in open_children],
    })
    return card


def workspace_info(workspace: Optional[Workspace]) -> Optional[Dict[str, Any]]:
    if workspace is None:
        return None
    return {
        "title": workspace.title,
        "isRepo": workspace.is_repo,
        "branch": workspace.branch,
        "repo": workspace.repo,
    }


def state(status: str, workspace: Optional[Workspace] = None, **fields: Any) -> Dict[str, Any]:
    """A view with every key the page reads. `status` is one of loading, ok,
    no-project, no-key, key-error (the Keychain did not hand out the key),
    no-workspace, cmux-error (cmux or git did not answer) and error (Linear
    failed before anything loaded); `error` carries a failed refresh over an
    older view, shown as a banner."""
    view: Dict[str, Any] = {
        "status": status,
        "workspace": workspace_info(workspace),
        "current": None,
        "project": None,
        "groups": [],
        "truncated": False,
        "message": None,
        "error": None,
        "updatedAt": None,
    }
    view.update(fields)
    return view


def ready(
    workspace: Workspace,
    issue: Optional[Issue],
    via: Optional[str],
    project: Optional[Dict[str, Any]],
    project_via: Optional[str],
    issues: Sequence[Issue],
    truncated: bool,
    fills: Dict[str, float],
    updated_at_ms: int,
    message: Optional[str] = None,
) -> Dict[str, Any]:
    """`message` explains a no-project result further, such as a search that stopped early."""
    current = current_card(issue, via or "branch", fills) if issue else None
    # The card shows the current issue and its open sub-issues; closed ones stay on the board.
    on_card = [current["id"], *(s["id"] for s in current["subIssues"])] if current else []
    status = "ok" if project or current else "no-project"
    return state(
        status,
        workspace,
        current=current,
        project={"name": project["name"], "url": project["url"], "via": project_via} if project else None,
        groups=board(issues, on_card, fills) if project else [],
        truncated=bool(project) and truncated,
        message=message if status == "no-project" else None,
        updatedAt=updated_at_ms,
    )
