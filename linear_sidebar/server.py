"""Local HTTP server for the Dock page: static files, the view, and opening links."""

from __future__ import annotations

import errno
import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

from . import config
from .service import SidebarService, log

# Sent with every view, so the scripts can tell this server from another program on the port.
APP = "cmux-linear-sidebar"

STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}
OPENABLE_HOSTS = ("linear.app",)
MAX_BODY_BYTES = 64 * 1024


def openable(url: Any) -> bool:
    """Only plain https://linear.app links may be handed to the system browser."""
    if not isinstance(url, str):
        return False
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    return (parts.scheme == "https" and parts.hostname in OPENABLE_HOSTS
            and port is None and not parts.username and not parts.password)


def make_handler(service: SidebarService, port: int):
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    allowed_origins = {f"http://{host}" for host in allowed_hosts}

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: bytes = b"", content_type: str = "text/plain; charset=utf-8") -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            # A <meta> policy cannot forbid framing; only this header can.
            self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def _host_allowed(self) -> bool:
            # Rejects DNS-rebinding requests that reach the port under a foreign host name.
            if self.headers.get("Host") in allowed_hosts:
                return True
            self._send(403, b"forbidden")
            return False

        def _write_allowed(self) -> bool:
            """Only the page itself may trigger actions: same origin, JSON body.

            A JSON content type cannot be sent cross-origin without a CORS preflight,
            which this server never approves.
            """
            origin = self.headers.get("Origin")
            if (origin is not None and origin not in allowed_origins) or \
                    self.headers.get("Sec-Fetch-Site") == "cross-site":
                self._send(403, b"forbidden")
                return False
            content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if content_type != "application/json":
                self._send(415, b"send application/json")
                return False
            return True

        def _json_body(self) -> Optional[Dict[str, Any]]:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if length < 0 or length > MAX_BODY_BYTES:
                self._send(400, b"bad request body")
                return None
            try:
                data = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                data = None
            if not isinstance(data, dict):
                self._send(400, b"expected a JSON object")
                return None
            return data

        def do_GET(self) -> None:
            if not self._host_allowed():
                return
            path = urlsplit(self.path).path
            if path == "/api/state":
                body = json.dumps({"app": APP, **service.current()}, ensure_ascii=False).encode()
                self._send(200, body, "application/json; charset=utf-8")
            elif path in STATIC:
                name, content_type = STATIC[path]
                self._send(200, (config.WEB_DIR / name).read_bytes(), content_type)
            else:
                self._send(404, b"not found")

        def do_POST(self) -> None:
            if not self._host_allowed():
                return
            path = urlsplit(self.path).path
            if path not in ("/api/refresh", "/api/open"):
                self._send(404, b"not found")
                return
            if not self._write_allowed():
                return
            body = self._json_body()
            if body is None:
                return
            if path == "/api/refresh":
                service.refresh()
                self._send(204)
                return
            url = body.get("url")
            if not openable(url):
                self._send(400, b"only https://linear.app links can be opened")
                return
            # Absolute path on purpose: inside cmux terminals `open` is a wrapper that
            # routes http(s) URLs into cmux's own browser instead of the system default.
            try:
                opened = subprocess.run(["/usr/bin/open", url], capture_output=True, timeout=10).returncode == 0
            except (OSError, subprocess.SubprocessError) as error:
                log(f"Could not open {url}: {error}")
                opened = False
            self._send(204 if opened else 502, b"" if opened else b"could not open the link")

        def log_message(self, format: str, *args: Any) -> None:
            pass  # The Dock terminal shows workspace changes and errors only.

    return Handler


def main(port: Optional[int] = None, refresh_seconds: Optional[int] = None) -> int:
    try:
        settings = config.load(port, refresh_seconds)
    except config.SettingsError as error:
        log(str(error))
        return 2
    # The poller reads the API key: the Keychain can wait on an unlock prompt, and the
    # page shows that wait instead of a server that does not answer.
    service = SidebarService(settings, key_source=config.api_key)
    try:
        server = ThreadingHTTPServer((settings.host, settings.port), make_handler(service, settings.port))
    except OSError as error:
        if error.errno == errno.EADDRINUSE:
            log(f"Port {settings.port} is busy: another Linear sidebar is probably running.")
            return 1
        raise
    if sys.stdout.isatty():
        # The Dock tab is labelled with the terminal title.
        sys.stdout.write("\033]0;Linear server\007")
    log(f"Linear sidebar on http://{settings.host}:{settings.port}/")
    threading.Thread(target=service.run_forever, name="poller", daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
