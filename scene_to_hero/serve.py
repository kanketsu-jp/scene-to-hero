from __future__ import annotations

import http.server
import json
import mimetypes
import os
import shutil
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .project import Project

MAX_BODY_BYTES = 1_000_000
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def ui_dir() -> Path:
    return Path(__file__).resolve().parent / "ui"


def is_loopback(host: str) -> bool:
    return host.lower().strip("[]") in LOOPBACK_HOSTS


def exposure_warning(host: str) -> str | None:
    if is_loopback(host):
        return None
    return "Warning: anyone who can reach the server can overwrite project.json."


def _is_inside(root: Path, target: Path) -> bool:
    return target == root or root in target.parents


def _hostname(header_value: str | None) -> str | None:
    if not header_value:
        return None
    value = header_value.strip()
    if value.startswith("["):
        end = value.find("]")
        return value[1:end] if end >= 0 else None
    return value.rsplit(":", 1)[0] if value.count(":") == 1 else value


def _host_allowed(
    header_value: str | None, bound_host: str, allowed_hosts: tuple[str, ...] = ()
) -> bool:
    if not is_loopback(bound_host):
        return True
    hostname = _hostname(header_value)
    return hostname in LOOPBACK_HOSTS or (
        hostname is not None and hostname.lower() in {host.lower() for host in allowed_hosts}
    )


def _content_type(path: Path) -> str:
    if path.suffix.lower() == ".mp4":
        return "video/mp4"
    if path.suffix.lower() == ".json":
        return "application/json"
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def _byte_range(value: str | None, size: int) -> tuple[int, int] | None:
    if not value or not value.startswith("bytes=") or "," in value:
        return None
    spec = value[6:]
    if "-" not in spec:
        return None
    start_text, end_text = spec.split("-", 1)
    try:
        if not start_text:
            length = int(end_text)
            if length <= 0:
                raise ValueError
            start, end = max(0, size - length), size - 1
        else:
            start = int(start_text)
            end = int(end_text) if end_text else size - 1
            if start < 0 or start >= size or end < start:
                raise ValueError
            end = min(end, size - 1)
        return start, end
    except ValueError:
        return None


class _Handler(http.server.BaseHTTPRequestHandler):
    server_version = "scene-to-hero"

    @property
    def project_root(self) -> Path:
        return self.server.project_root

    def _send_text(self, status: int, text: str, *, close: bool = False) -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if close:
            self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _allowed(self) -> bool:
        if _host_allowed(
            self.headers.get("Host"), self.server.bound_host, self.server.allowed_hosts
        ):
            return True
        self._send_text(403, "forbidden")
        return False

    def _file_response(self, target: Path) -> None:
        try:
            size = target.stat().st_size
        except OSError:
            self._send_text(404, "not found")
            return
        byte_range = _byte_range(self.headers.get("Range"), size)
        partial = byte_range is not None
        if self.headers.get("Range") and self.headers["Range"].startswith("bytes="):
            range_value = self.headers["Range"]
            if "," not in range_value and "-" in range_value[6:]:
                start_text, end_text = range_value[6:].split("-", 1)
                try:
                    if (not start_text and int(end_text) <= 0) or (
                        start_text
                        and (int(start_text) >= size or int(end_text or size - 1) < int(start_text))
                    ):
                        self.send_response(416)
                        self.send_header("Content-Range", f"bytes */{size}")
                        self.send_header("Content-Length", "0")
                        self.send_header("Cache-Control", "no-store")
                        self.send_header("Accept-Ranges", "bytes")
                        self.end_headers()
                        return
                except ValueError:
                    pass
        start, end = byte_range if partial else (0, size - 1)
        length = end - start + 1 if size else 0
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", _content_type(target))
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-store")
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return
        with target.open("rb") as stream:
            stream.seek(start)
            remaining = length
            while remaining:
                chunk = stream.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def _static(self, root: Path, relative: str, *, ui: bool = False) -> None:
        decoded = unquote(relative)
        target = (root / decoded).resolve()
        if not _is_inside(root.resolve(), target) or (ui and target.parent != root.resolve()):
            self._send_text(404, "not found")
            return
        if target.is_dir() or not target.is_file():
            self._send_text(404, "not found")
            return
        self._file_response(target)

    def _put_project(self) -> None:
        content_type = self.headers.get("Content-Type", "")
        if not content_type.lower().startswith("application/json"):
            self._send_text(400, "Content-Type must be application/json")
            return
        try:
            length = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY_BYTES:
            if 0 <= length <= 2 * MAX_BODY_BYTES:
                self.rfile.read(length)
            self._send_text(
                400,
                "request body is too large or Content-Length is missing",
                close=length > MAX_BODY_BYTES,
            )
            return
        body = self.rfile.read(length)
        try:
            data = json.loads(body.decode("utf-8"))
            if not isinstance(data, dict):
                raise TypeError("project must be an object")
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            self._send_text(400, str(exc))
            return
        try:
            Project.from_dict(data)
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            self._send_text(400, str(exc))
            return
        target = self.project_root / "project.json"
        try:
            if target.exists():
                shutil.copy2(target, target.with_name(target.name + ".bak"))
            temporary = target.with_name(target.name + ".tmp")
            temporary.write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            os.replace(temporary, target)
        except OSError as exc:
            self._send_text(500, str(exc))
            return
        body = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        self._dispatch()

    def do_HEAD(self) -> None:
        self._dispatch()

    def do_PUT(self) -> None:
        self._dispatch()

    def do_POST(self) -> None:
        self._dispatch()

    def do_DELETE(self) -> None:
        self._dispatch()

    def do_OPTIONS(self) -> None:
        self._dispatch()

    def _dispatch(self) -> None:
        if not self._allowed():
            return
        parsed = urlsplit(self.path)
        path = parsed.path
        if self.command not in {"GET", "HEAD", "PUT", "POST", "DELETE"}:
            self._send_text(405, "method not allowed")
            return
        if path == "/" and self.command in {"GET", "HEAD"}:
            self.send_response(302)
            self.send_header("Location", "/ui/")
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        if path == "/api/project":
            if self.command == "PUT":
                self._put_project()
            elif self.command == "GET":
                self._static(self.project_root, "project.json")
            else:
                self._send_text(405, "method not allowed")
            return
        if path == "/ui" or path.startswith("/ui/"):
            if self.command not in {"GET", "HEAD"}:
                self._send_text(405, "method not allowed")
                return
            relative = "index.html" if path == "/ui" or path == "/ui/" else path[4:]
            self._static(ui_dir(), relative, ui=True)
            return
        if path.startswith("/project/"):
            if self.command not in {"GET", "HEAD"}:
                self._send_text(405, "method not allowed")
                return
            self._static(self.project_root, path[9:])
            return
        self._send_text(
            405 if self.command not in {"GET", "HEAD"} else 404,
            "method not allowed" if self.command != "GET" else "not found",
        )


def make_server(
    project_dir, host="127.0.0.1", port=0, allowed_hosts=()
) -> http.server.ThreadingHTTPServer:
    root = Path(project_dir).resolve()

    class Server(http.server.ThreadingHTTPServer):
        allow_reuse_address = True

    server = Server((host, port), _Handler)
    server.project_root = root
    server.bound_host = host
    server.allowed_hosts = tuple(allowed_hosts)
    return server


def run(project_dir, host="127.0.0.1", port=0, allowed_hosts=()) -> int:
    server = make_server(project_dir, host, port, allowed_hosts)
    actual_port = server.server_address[1]
    print(f"http://{host}:{actual_port}/", flush=True)
    warning = exposure_warning(host)
    if warning:
        print(warning, file=sys.stderr, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("stopped", flush=True)
    finally:
        server.server_close()
    return 0
