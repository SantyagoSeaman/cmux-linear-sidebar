"""The workspace selected in cmux and the git state of its folder."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple
from urllib.parse import urlsplit

GIT_TIMEOUT_SECONDS = 5
_GITHUB_HOSTS = ("github.com", "www.github.com")
_GIT_URL_SCHEMES = ("https", "http", "ssh", "git", "git+ssh", "ssh+git")
# The scp-like form of an SSH remote: git@github.com:owner/name.git
_SCP_REMOTE = re.compile(r"(?:[^@/\s]+@)?github\.com:/?([^/\s]+)/([^/\s]+?)/?", re.IGNORECASE)
_PULL_REQUEST = re.compile(r"https://github\.com/[^/\s]+/[^/\s]+/pull/\d+")
_ISSUE_KEY = re.compile(r"\s*([A-Za-z][A-Za-z0-9]*-\d+)\s*")


class CmuxError(Exception):
    """cmux did not answer or answered with something unreadable."""


class GitError(Exception):
    """git did not answer in the workspace's folder."""


def issue_key(title: str) -> Optional[str]:
    """`SAN-304` when the whole workspace title is an issue identifier."""
    match = _ISSUE_KEY.fullmatch(title or "")
    return match.group(1).upper() if match else None


@dataclass(frozen=True)
class Workspace:
    id: str
    title: str
    directory: Optional[str]
    is_repo: bool
    branch: Optional[str]
    repo: Optional[str]

    @property
    def context(self) -> Tuple[Optional[str], ...]:
        """Everything the Linear data depends on; a change triggers a reload.

        Only the issue key of the title matters: cmux renames workspaces on its
        own (from the running agent's conversation, for example).
        """
        return (self.id, self.directory, self.branch, self.repo, issue_key(self.title))


def github_repo(url: Optional[str]) -> Optional[str]:
    """`owner/name` of a GitHub remote or web URL (SSH, HTTPS, or a link into the repo).

    The host must be github.com itself: `notgithub.com` or a mirror whose path
    contains `github.com/` is not GitHub.
    """
    if not url or not isinstance(url, str):
        return None
    url = url.strip()
    scp = _SCP_REMOTE.fullmatch(url)
    if scp:
        owner, name = scp.group(1), scp.group(2)
    else:
        if "://" not in url and url.lower().startswith(tuple(f"{host}/" for host in _GITHUB_HOSTS)):
            url = f"https://{url}"
        try:
            parts = urlsplit(url)
            host = parts.hostname
        except ValueError:
            return None
        if parts.scheme.lower() not in _GIT_URL_SCHEMES or host not in _GITHUB_HOSTS:
            return None
        segments = [segment for segment in parts.path.split("/") if segment]
        if len(segments) < 2:
            return None
        owner, name = segments[0], segments[1]
    if name.lower().endswith(".git"):
        name = name[:-4]
    return f"{owner}/{name}" if owner and name else None


def _run(args: Sequence[str], timeout: float = 5) -> Optional[str]:
    try:
        result = subprocess.run(list(args), capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _git(directory: str, *args: str) -> "subprocess.CompletedProcess[str]":
    """Runs git in `directory`; a git that does not answer raises GitError instead of looking like a failure."""
    try:
        return subprocess.run(["git", "-C", directory, *args], capture_output=True, text=True,
                              timeout=GIT_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        raise GitError(f"git did not answer within {GIT_TIMEOUT_SECONDS} seconds in {directory}") from None
    except OSError as error:
        raise GitError(f"Could not run git: {error.strerror or error}") from None


def git_head(directory: str) -> Tuple[bool, Optional[str]]:
    """(is a git repository, current branch). The branch is None on a detached HEAD.

    `symbolic-ref` also names the branch of a repository without commits yet,
    where `rev-parse HEAD` fails. Raises GitError when git does not answer, so a
    slow repository is not taken for a plain folder.
    """
    result = _git(directory, "symbolic-ref", "--quiet", "--short", "HEAD")
    if result.returncode == 0:
        return True, result.stdout.strip() or None
    # Exit status 1 is a detached HEAD; anything else means no repository here.
    return result.returncode == 1, None


def origin_repo(directory: str) -> Optional[str]:
    """`owner/name` of the folder's GitHub `origin` remote."""
    result = _git(directory, "remote", "get-url", "origin")
    return github_repo(result.stdout.strip()) if result.returncode == 0 else None


def selected_workspace(cmux_bin: str) -> Optional[Workspace]:
    """The workspace selected in cmux; raises CmuxError or GitError when cmux or git does not answer."""
    raw = _run([cmux_bin, "workspace", "list", "--json"])
    if raw is None:
        raise CmuxError("cmux did not answer `workspace list`")
    try:
        data = json.loads(raw)
    except ValueError as error:
        raise CmuxError("cmux returned unreadable workspace data") from error
    workspaces = data.get("workspaces") if isinstance(data, dict) else data
    if not isinstance(workspaces, list):
        raise CmuxError("cmux returned unreadable workspace data")
    selected = next((w for w in workspaces if isinstance(w, dict) and w.get("selected")), None)
    if selected is None:
        return None

    directory = selected.get("current_directory") or None
    is_repo, branch = git_head(directory) if directory else (False, None)
    return Workspace(
        id=str(selected.get("id") or ""),
        title=selected.get("title") or "",
        directory=directory,
        is_repo=is_repo,
        branch=branch,
        repo=origin_repo(directory) if is_repo and directory else None,
    )


def pull_requests(cmux_bin: str, workspace_id: str) -> List[str]:
    """Pull request URLs cmux shows in the sidebar for the workspace."""
    raw = _run([cmux_bin, "sidebar-state", "--workspace", workspace_id]) or ""
    return _PULL_REQUEST.findall(raw)
