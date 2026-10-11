import http.client
import json
import subprocess
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest import mock

from linear_sidebar import server as server_module
from linear_sidebar.server import make_handler, openable


class FakeService:
    def __init__(self):
        self.refreshed = 0

    def current(self):
        return {"status": "ok", "workspace": {"title": "Gadget"}}

    def refresh(self):
        self.refreshed += 1


class OpenableTest(unittest.TestCase):
    def test_only_plain_linear_links(self):
        self.assertTrue(openable("https://linear.app/acme/issue/GAD-1"))
        for url in ("http://linear.app/acme", "https://linear.app.evil.com/x", "https://evil.com/linear.app",
                    "https://linear.app@evil.com/", "https://user@linear.app/x", "https://linear.app:8443/x",
                    "https://[::1/", "javascript:alert(1)", 123, None):
            with self.subTest(url=url):
                self.assertFalse(openable(url))


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.service = FakeService()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.service, 0))
        self.port = self.httpd.server_address[1]
        self.httpd.RequestHandlerClass = make_handler(self.service, self.port)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.opened = mock.patch.object(server_module.subprocess, "run").start()
        self.opened.return_value.returncode = 0
        mock.patch.object(server_module, "log").start()
        self.addCleanup(mock.patch.stopall)

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def request(self, method, path, body=None, headers=None, with_headers=False):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        all_headers = {"Host": f"127.0.0.1:{self.port}"}
        all_headers.update(headers or {})
        payload = body if isinstance(body, (bytes, type(None))) else json.dumps(body).encode()
        connection.request(method, path, body=payload, headers=all_headers)
        response = connection.getresponse()
        result = (response.status, response.read())
        received = dict(response.getheaders())
        connection.close()
        return (*result, received) if with_headers else result

    def post(self, path, body, **headers):
        merged = {"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{self.port}"}
        merged.update(headers)
        return self.request("POST", path, body, {k: v for k, v in merged.items() if v is not None})

    def test_state_and_page(self):
        status, body = self.request("GET", "/api/state")
        self.assertEqual((status, json.loads(body)["status"]), (200, "ok"))
        self.assertEqual(self.request("GET", "/")[0], 200)
        self.assertEqual(self.request("GET", "/nope")[0], 404)

    def test_state_names_the_program_for_the_scripts(self):
        self.assertEqual(json.loads(self.request("GET", "/api/state")[1])["app"], "cmux-linear-sidebar")

    def test_responses_forbid_framing_and_sniffing(self):
        for path in ("/", "/api/state", "/nope"):
            with self.subTest(path=path):
                headers = self.request("GET", path, with_headers=True)[2]
                self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
                self.assertEqual(headers["Content-Security-Policy"], "frame-ancestors 'none'")

    def test_foreign_host_is_refused(self):
        self.assertEqual(self.request("GET", "/api/state", headers={"Host": "evil.example:80"})[0], 403)

    def test_open_runs_the_system_open_command(self):
        url = "https://linear.app/acme/issue/GAD-1"
        self.assertEqual(self.post("/api/open", {"url": url})[0], 204)
        self.opened.assert_called_once()
        self.assertEqual(self.opened.call_args[0][0], ["/usr/bin/open", url])

    def test_open_that_fails_or_hangs_answers_502(self):
        url = {"url": "https://linear.app/acme/issue/GAD-1"}
        self.opened.return_value.returncode = 1
        self.assertEqual(self.post("/api/open", url)[0], 502)
        for failure in (subprocess.TimeoutExpired(["/usr/bin/open"], 10), OSError("no open")):
            with self.subTest(failure=failure):
                self.opened.side_effect = failure
                self.assertEqual(self.post("/api/open", url)[0], 502)

    def test_open_rejects_other_urls(self):
        for url in ("https://evil.com/", 7):
            with self.subTest(url=url):
                self.assertEqual(self.post("/api/open", {"url": url})[0], 400)
        self.opened.assert_not_called()

    def test_cross_site_requests_are_refused(self):
        url = {"url": "https://linear.app/acme"}
        self.assertEqual(self.post("/api/open", url, Origin="https://evil.example")[0], 403)
        self.assertEqual(self.post("/api/open", url, **{"Sec-Fetch-Site": "cross-site"})[0], 403)
        self.assertEqual(self.post("/api/open", url, **{"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.post("/api/refresh", {}, Origin="https://evil.example")[0], 403)
        self.opened.assert_not_called()
        self.assertEqual(self.service.refreshed, 0)

    def test_requests_without_origin_are_allowed(self):
        self.assertEqual(self.post("/api/refresh", {}, Origin=None)[0], 204)
        self.assertEqual(self.service.refreshed, 1)

    def test_bad_bodies(self):
        self.assertEqual(self.post("/api/open", b"not json")[0], 400)
        self.assertEqual(self.post("/api/open", [1, 2])[0], 400)
        self.assertEqual(self.post("/api/open", b"{}", **{"Content-Length": "lots"})[0], 400)
        self.assertEqual(self.post("/api/unknown", {})[0], 404)


if __name__ == "__main__":
    unittest.main()
