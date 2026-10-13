import http.client
import io
import json
import threading
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from linear_sidebar import linear
from linear_sidebar.linear import LinearClient, LinearError, LinearNotFound


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def answer(payload):
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return FakeResponse(body)


def http_error(code, payload=b""):
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return urllib.error.HTTPError("https://api.linear.app/graphql", code, "error", {}, io.BytesIO(body))


def sent_variables(urlopen_mock, call=0):
    request = urlopen_mock.call_args_list[call][0][0]
    return json.loads(request.data)["variables"]


class QueryTest(unittest.TestCase):
    client = LinearClient("lin_api_test")

    def query_with(self, effect):
        with mock.patch("urllib.request.urlopen", side_effect=[effect] if not isinstance(effect, list) else effect):
            return self.client.query("query { viewer { name } }")

    def test_returns_data(self):
        self.assertEqual(self.query_with(answer({"data": {"viewer": {"name": "A"}}})), {"viewer": {"name": "A"}})

    def test_sends_the_key_as_authorization_header(self):
        with mock.patch("urllib.request.urlopen", return_value=answer({"data": {}})) as urlopen:
            self.client.query("query { viewer { name } }")
        self.assertEqual(urlopen.call_args[0][0].get_header("Authorization"), "lin_api_test")

    def test_errors_of_any_shape_are_linear_errors(self):
        for errors in ({"message": "oops"}, [{"message": 42}], ["text"], [None], "broken", [{"message": "  "}]):
            with self.subTest(errors=errors):
                with self.assertRaises(LinearError) as caught:
                    self.query_with(answer({"errors": errors, "data": None}))
                self.assertNotIsInstance(caught.exception, LinearNotFound)
        with self.assertRaisesRegex(LinearError, "^oops$"):
            self.query_with(answer({"errors": {"message": "oops"}}))

    def test_graphql_errors(self):
        with self.assertRaisesRegex(LinearError, "boom"):
            self.query_with(answer({"errors": [{"message": "boom"}], "data": None}))

    def test_not_found_is_its_own_error(self):
        with self.assertRaises(LinearNotFound):
            self.query_with(answer({"errors": [{"message": "Entity not found: Issue"}], "data": None}))

    def test_rejected_key(self):
        with self.assertRaisesRegex(LinearError, "rejected the API key"):
            self.query_with(http_error(401))

    def test_http_error_with_graphql_body(self):
        with self.assertRaisesRegex(LinearError, "bad filter"):
            self.query_with(http_error(400, {"errors": [{"message": "bad filter"}]}))

    def test_http_error_without_body(self):
        with self.assertRaisesRegex(LinearError, "HTTP 500"):
            self.query_with(http_error(500))

    def test_unreadable_answers(self):
        for effect in (answer(b"<html>captive portal</html>"), http.client.IncompleteRead(b""),
                       answer({"data": None}), answer(b"[]")):
            with self.subTest(effect=effect):
                with self.assertRaises(LinearError):
                    self.query_with(effect)

    def test_unreachable(self):
        with self.assertRaisesRegex(LinearError, "unreachable"):
            self.query_with(urllib.error.URLError("no route"))


class ClientTest(unittest.TestCase):
    client = LinearClient("lin_api_test")

    def test_unknown_issue_is_none(self):
        with mock.patch("urllib.request.urlopen",
                        return_value=answer({"errors": [{"message": "Entity not found: Issue"}], "data": None})):
            self.assertIsNone(self.client.issue("ABC-1"))

    def test_issue_lookup_does_not_hide_network_errors(self):
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("down")):
            with self.assertRaises(LinearError):
                self.client.issue("ABC-1")

    def test_projects_follow_pages(self):
        pages = [
            answer({"data": {"projects": {"nodes": [{"id": "p1"}],
                                          "pageInfo": {"hasNextPage": True, "endCursor": "c1"}}}}),
            answer({"data": {"projects": {"nodes": [{"id": "p2"}],
                                          "pageInfo": {"hasNextPage": False, "endCursor": None}}}}),
        ]
        with mock.patch("urllib.request.urlopen", side_effect=pages) as urlopen:
            projects = self.client.projects()
        self.assertEqual(([p["id"] for p in projects[0]], projects[1]), (["p1", "p2"], True))
        self.assertEqual(sent_variables(urlopen, 1), {"after": "c1"})

    def test_projects_report_a_cut_list(self):
        endless = {"data": {"projects": {"nodes": [{"id": "p"}], "pageInfo": {"hasNextPage": True, "endCursor": "c"}}}}
        with mock.patch("urllib.request.urlopen", side_effect=lambda *a, **k: answer(endless)) as urlopen:
            projects, complete = self.client.projects()
        self.assertEqual((len(projects), complete, urlopen.call_count),
                         (linear.MAX_PROJECT_PAGES, False, linear.MAX_PROJECT_PAGES))

    def test_projects_report_cut_resource_links(self):
        page = {"data": {"projects": {"nodes": [
            {"id": "p1", "externalLinks": {"nodes": [], "pageInfo": {"hasNextPage": True}}},
        ], "pageInfo": {"hasNextPage": False}}}}
        with mock.patch("urllib.request.urlopen", return_value=answer(page)):
            self.assertEqual(self.client.projects()[1], False)

    def test_project_issues_report_more_pages(self):
        payload = {"data": {
            "board": {"nodes": [{"identifier": "A-1"}], "pageInfo": {"hasNextPage": True}},
            "backlog": {"nodes": [{"identifier": "A-2"}, None]},
            "backlogIds": {"nodes": [{"id": "a2"}, {"id": "a3"}], "pageInfo": {"hasNextPage": True}},
        }}
        with mock.patch("urllib.request.urlopen", return_value=answer(payload)):
            found = self.client.project_issues("p1")
        self.assertEqual(([i["identifier"] for i in found.board], found.board_more), (["A-1"], True))
        self.assertEqual(([i["identifier"] for i in found.backlog], found.backlog_count, found.backlog_count_more),
                         (["A-2"], 2, True))

    def test_project_issues_without_a_backlog(self):
        payload = {"data": {"board": {"nodes": [], "pageInfo": {"hasNextPage": False}}, "backlog": None,
                            "backlogIds": None}}
        with mock.patch("urllib.request.urlopen", return_value=answer(payload)):
            self.assertEqual(self.client.project_issues("p1"), linear.ProjectIssues([], False, [], 0, False))

    def test_projects_for_url_skips_issues_without_project(self):
        payload = {"data": {"attachmentsForURL": {"nodes": [
            {"issue": None}, {"issue": {"project": None}}, {"issue": {"project": {"id": "p1"}}},
        ]}}}
        with mock.patch("urllib.request.urlopen", return_value=answer(payload)):
            self.assertEqual(self.client.projects_for_url("https://github.com/a/b/pull/1"), [{"id": "p1"}])


class RedirectTest(unittest.TestCase):
    """A redirect from the API must not carry the key to the next host."""

    def serve(self, handler):
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_address[1]}/"

    def test_authorization_stays_behind_on_a_redirect(self):
        seen = []

        class Elsewhere(BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append(self.headers.get("Authorization"))
                body = b'{"data": {"viewer": {"name": "Somebody"}}}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        elsewhere = self.serve(Elsewhere)

        class Redirecting(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                self.send_response(302)
                self.send_header("Location", elsewhere)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args):
                pass

        with mock.patch.object(linear, "ENDPOINT", self.serve(Redirecting)):
            LinearClient("lin_api_secret").viewer()
        self.assertEqual(seen, [None])


if __name__ == "__main__":
    unittest.main()
