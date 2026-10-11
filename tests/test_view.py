import unittest

from linear_sidebar import view
from linear_sidebar.workspace import Workspace

STARTED = {"id": "s-progress", "name": "In Progress", "type": "started", "color": "#f2c94c", "position": 2}
REVIEW = {"id": "s-review", "name": "In Review", "type": "started", "color": "#0f783c", "position": 1002}
TODO = {"id": "s-todo", "name": "Todo", "type": "unstarted", "color": "#e2e2e2", "position": 1}
DONE = {"id": "s-done", "name": "Done", "type": "completed", "color": "#5e6ad2", "position": 3}
CANCELED = {"id": "s-canceled", "name": "Canceled", "type": "canceled", "color": "#95a2b3", "position": 4}


def issue(identifier, state, priority=0, parent=None, updated="2026-10-11T10:00:00Z", **extra):
    data = {
        "identifier": identifier,
        "title": f"Title of {identifier}",
        "url": f"https://linear.app/acme/issue/{identifier}",
        "priority": priority,
        "updatedAt": updated,
        "state": state,
        "parent": {"identifier": parent} if parent else None,
    }
    data.update(extra)
    return data


def workspace(**fields):
    defaults = dict(id="ws", title="Gadget", directory="/tmp/gadget", is_repo=True,
                    branch="gad-304-login", repo="acme/gadget")
    defaults.update(fields)
    return Workspace(**defaults)


def project(project_id, name, status, *urls):
    return {"id": project_id, "name": name, "url": f"https://linear.app/acme/project/{project_id}",
            "status": {"type": status}, "externalLinks": {"nodes": [{"url": u} for u in urls]}}


class ProjectForRepoTest(unittest.TestCase):
    projects = [
        project("p1", "Gadget", "started", "https://www.figma.com/file/x"),
        project("p2", "Widget", "started",
                "https://github.com/Acme/widget-internal/blob/main/docs/releasing.md",
                "https://pypi.org/project/widget/"),
    ]

    def test_matches_any_link_into_the_repo_ignoring_case(self):
        self.assertEqual(view.project_for_repo(self.projects, "acme/Widget-Internal")["id"], "p2")

    def test_no_match(self):
        self.assertIsNone(view.project_for_repo(self.projects, "Acme/gadget"))
        self.assertIsNone(view.project_for_repo(self.projects, None))

    def test_an_active_project_wins_over_a_finished_one(self):
        projects = [
            project("old", "Widget v1", "completed", "https://github.com/acme/widget"),
            project("new", "Widget v2", "started", "https://github.com/acme/widget"),
        ]
        self.assertEqual(view.project_for_repo(projects, "acme/widget")["id"], "new")

    def test_links_without_url_are_skipped(self):
        projects = [{"id": "p", "name": "P", "externalLinks": {"nodes": [None, {"url": None}]}}]
        self.assertIsNone(view.project_for_repo(projects, "acme/widget"))


class StartedFillsTest(unittest.TestCase):
    def test_first_started_state_is_half_full_and_last_three_quarters(self):
        team = {"id": "t1"}
        fills = view.started_fills([
            {"id": "review", "position": 1002, "team": team},
            {"id": "progress", "position": 2, "team": team},
            {"id": "solo", "position": 5, "team": {"id": "t2"}},
            {"id": "orphan", "position": 1, "team": None},
        ])
        self.assertEqual(fills, {"progress": 0.5, "review": 0.75, "solo": 0.5, "orphan": 0.5})


class IconTest(unittest.TestCase):
    def test_unsafe_or_missing_colors_fall_back(self):
        for color in ("red;background:url(x)", "#fff\n", None, ""):
            with self.subTest(color=color):
                self.assertEqual(view.icon({"type": "unstarted", "color": color}, {})["color"],
                                 view.FALLBACK_COLOR)

    def test_shapes(self):
        self.assertEqual(view.icon(DONE, {})["shape"], "done")
        self.assertEqual(view.icon({"type": "duplicate", "color": "#95a2b3"}, {})["shape"], "canceled")
        self.assertEqual(view.icon({"type": "backlog", "color": "#bec2c8"}, {})["shape"], "backlog")
        self.assertEqual(view.icon({"type": "triage", "color": "#fc7840"}, {})["shape"], "unstarted")
        self.assertEqual(view.icon(STARTED, {"s-progress": 0.75})["fill"], 0.75)


class BoardTest(unittest.TestCase):
    def test_groups_by_state_with_closed_last_and_collapsed(self):
        groups = view.board([
            issue("SAN-1", TODO),
            issue("SAN-2", REVIEW),
            issue("SAN-3", STARTED),
            issue("SAN-4", DONE, completedAt="2026-10-11T09:00:00Z"),
            issue("SAN-5", CANCELED, canceledAt="2026-10-11T10:00:00Z"),
        ], [], {})
        self.assertEqual([g["name"] for g in groups], ["In Progress", "In Review", "Todo", "Closed · 24h"])
        self.assertTrue(groups[-1]["collapsed"])
        self.assertEqual([r["id"] for r in groups[-1]["issues"]], ["SAN-5", "SAN-4"])
        self.assertEqual(groups[-1]["issues"][0]["icon"]["shape"], "canceled")

    def test_excluded_issues_are_left_out(self):
        groups = view.board([
            issue("SAN-304", STARTED),
            issue("SAN-325", STARTED, parent="SAN-304"),
            issue("SAN-7", STARTED),
            issue("SAN-13", TODO, parent="SAN-7"),
        ], ["SAN-304", "SAN-325"], {})
        self.assertEqual([r["id"] for g in groups for r in g["issues"]], ["SAN-7", "SAN-13"])

    def test_group_keys_tell_apart_states_of_one_name_and_another_type(self):
        groups = view.board([issue("SAN-1", STARTED), issue("SAN-2", dict(TODO, name="In Progress"))], [], {})
        self.assertEqual([g["key"] for g in groups], ["state:started:In Progress", "state:unstarted:In Progress"])

    def test_urgent_first_and_no_priority_last(self):
        groups = view.board([
            issue("SAN-1", TODO, priority=0),
            issue("SAN-2", TODO, priority=4),
            issue("SAN-3", TODO, priority=1),
        ], [], {})
        self.assertEqual([r["id"] for r in groups[0]["issues"]], ["SAN-3", "SAN-2", "SAN-1"])

    def test_same_state_of_two_teams_is_one_group(self):
        other_team_progress = dict(STARTED, id="s-progress-2", position=7)
        groups = view.board([issue("SAN-1", STARTED), issue("WID-1", other_team_progress)], [], {})
        self.assertEqual([(g["name"], g["count"]) for g in groups], [("In Progress", 2)])
        self.assertEqual(len({g["key"] for g in groups}), len(groups))


class CurrentCardTest(unittest.TestCase):
    def test_progress_counts_closed_sub_issues_and_lists_open_ones(self):
        current = issue("SAN-304", STARTED, children={"nodes": [
            issue("SAN-319", DONE), issue("SAN-320", CANCELED),
            issue("SAN-326", TODO), issue("SAN-325", STARTED),
        ]})
        current["parent"] = {"identifier": "SAN-195", "title": "Epic", "url": "https://linear.app/acme/issue/SAN-195"}
        card = view.current_card(current, "branch", {"s-progress": 0.5})
        self.assertEqual(card["progress"], {"done": 2, "total": 4, "more": False})
        self.assertEqual([s["id"] for s in card["subIssues"]], ["SAN-325", "SAN-326"])
        self.assertEqual(card["parent"]["id"], "SAN-195")
        self.assertEqual(card["icon"], {"shape": "started", "color": "#f2c94c", "fill": 0.5})

    def test_more_sub_issues_than_one_page_are_flagged(self):
        current = issue("SAN-304", STARTED, children={"nodes": [issue("SAN-319", DONE)],
                                                      "pageInfo": {"hasNextPage": True}})
        self.assertTrue(view.current_card(current, "branch", {})["progress"]["more"])

    def test_without_sub_issues_there_is_no_progress(self):
        card = view.current_card(issue("SAN-1", TODO), "title", {})
        self.assertIsNone(card["progress"])
        self.assertEqual(card["subIssues"], [])


class ReadyTest(unittest.TestCase):
    def test_no_issue_and_no_project_is_the_empty_state(self):
        result = view.ready(workspace(branch="main"), None, None, None, None, [], False, {}, 0)
        self.assertEqual(result["status"], "no-project")
        self.assertEqual(result["workspace"]["branch"], "main")
        self.assertNotIn("directory", result["workspace"])

    def test_project_from_repo_link(self):
        linked = project("p2", "Widget", "started")
        result = view.ready(workspace(), None, None, linked, "repo", [issue("WID-1", TODO)], True, {}, 1)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["project"]["via"], "repo")
        self.assertEqual(result["groups"][0]["issues"][0]["id"], "WID-1")
        self.assertTrue(result["truncated"])

    def test_closed_sub_issues_stay_on_the_board_and_open_ones_move_to_the_card(self):
        current = issue("SAN-304", STARTED, children={"nodes": [
            issue("SAN-319", DONE, parent="SAN-304"), issue("SAN-325", STARTED, parent="SAN-304")]})
        board_issues = [
            issue("SAN-304", STARTED),
            issue("SAN-319", DONE, parent="SAN-304", completedAt="2026-10-11T09:00:00Z"),
            issue("SAN-325", STARTED, parent="SAN-304"),
            issue("SAN-7", TODO),
        ]
        result = view.ready(workspace(), current, "branch", project("p1", "Gadget", "started"), "issue",
                            board_issues, False, {}, 1)
        self.assertEqual([s["id"] for s in result["current"]["subIssues"]], ["SAN-325"])
        self.assertEqual({g["name"]: [r["id"] for r in g["issues"]] for g in result["groups"]},
                         {"Todo": ["SAN-7"], "Closed · 24h": ["SAN-319"]})

    def test_a_message_explains_only_a_missing_project(self):
        missing = view.ready(workspace(), None, None, None, None, [], False, {}, 1, message="searched 500")
        self.assertEqual((missing["status"], missing["message"]), ("no-project", "searched 500"))
        found = view.ready(workspace(), None, None, project("p2", "Widget", "started"), "repo", [], False, {}, 1,
                           message="searched 500")
        self.assertIsNone(found["message"])

    def test_current_issue_without_project_has_no_board(self):
        result = view.ready(workspace(), issue("SAN-1", TODO), "branch", None, None, [], False, {}, 1)
        self.assertEqual(result["status"], "ok")
        self.assertIsNone(result["project"])
        self.assertEqual(result["groups"], [])
        self.assertEqual(result["current"]["id"], "SAN-1")


if __name__ == "__main__":
    unittest.main()
