import http.client
import json
import threading
import urllib.error
import urllib.request

import pytest

from scene_to_hero.serve import (
    MAX_BODY_BYTES,
    exposure_warning,
    is_loopback,
    make_server,
    ui_dir,
)


@pytest.fixture
def running_server(tmp_path):
    directory = tmp_path / "demo"
    (directory / "scenes").mkdir(parents=True)
    data = {
        "name": "demo",
        "scenes": [
            {
                "id": "a",
                "path": "scenes/a.png",
                "order": 0,
                "importance": 3,
                "role": "reference",
                "note": "",
            },
            {
                "id": "b",
                "path": "scenes/b.png",
                "order": 1,
                "importance": 3,
                "role": "final",
                "note": "",
            },
        ],
        "knowledge": {},
        "future": {"enabled": True},
    }
    original = json.dumps(data, indent=2) + "\n"
    (directory / "project.json").write_text(original, encoding="utf-8")
    (directory / "scenes" / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (directory / "clip.mp4").write_bytes(bytes(range(100)) * 10)
    (tmp_path / "secret.txt").write_text("sentinel", encoding="utf-8")
    server = make_server(directory, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield directory, base, data
    finally:
        server.shutdown()
        server.server_close()


def request(base, path, method="GET", body=None, headers=None):
    req = urllib.request.Request(base + path, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


def test_static_and_project_routes(running_server):
    directory, base, _ = running_server
    status, _headers, body = request(base, "/project/project.json")
    assert status == 200 and body == (directory / "project.json").read_bytes()
    assert request(base, "/api/project")[2] == body
    assert request(base, "/project/scenes/a.png")[1]["Content-Type"] == "image/png"
    assert request(base, "/ui/")[2].find(b"Scene to Hero") >= 0
    assert request(base, "/ui/" + "logic.js")[0] == 200
    conn = http.client.HTTPConnection("127.0.0.1", int(base.rsplit(":", 1)[1]))
    conn.request("GET", "/")
    response = conn.getresponse()
    assert response.status == 302 and response.getheader("Location") == "/ui/"
    conn.close()


def test_ranges_and_head(running_server):
    _, base, _ = running_server
    status, headers, body = request(base, "/project/clip.mp4", headers={"Range": "bytes=0-9"})
    assert status == 206 and headers["Content-Range"] == "bytes 0-9/1000" and len(body) == 10
    assert (
        request(base, "/project/clip.mp4", headers={"Range": "bytes=-5"})[2]
        == bytes(range(100))[-5:]
    )
    assert (
        request(base, "/project/clip.mp4", headers={"Range": "bytes=990-"})[2]
        == bytes(range(100))[-10:]
    )
    assert request(base, "/project/clip.mp4", headers={"Range": "bytes=abc"})[0] == 200
    assert request(base, "/project/clip.mp4", headers={"Range": "bytes=5000-"})[0] == 416
    status, headers, body = request(base, "/project/clip.mp4", method="HEAD")
    assert status == 200 and not body and headers["Accept-Ranges"] == "bytes"


def test_traversal_and_host_guard(running_server, tmp_path):
    directory, base, _ = running_server
    (directory / "outside-link.txt").symlink_to(tmp_path / "secret.txt")
    port = int(base.rsplit(":", 1)[1])
    for path in (
        "/project/../secret.txt",
        "/project/%2e%2e/secret.txt",
        "/project/..%2fsecret.txt",
        "/project/..%2f..%2fx",
        "/project//etc/hosts",
        "/project/outside-link.txt",
        "/project/%00x",
        "/project/" + "a" * 5000,
    ):
        conn = http.client.HTTPConnection("127.0.0.1", port)
        conn.request("GET", path)
        response = conn.getresponse()
        assert response.status in {403, 404} and b"sentinel" not in response.read()
        conn.close()
    assert request(base, "/api/project", headers={"Host": "evil.example"})[0] == 403
    assert request(base, "/api/project", headers={"Host": f"localhost:{port}"})[0] == 200


def test_allowed_hosts(tmp_path):
    directory = tmp_path / "demo"
    directory.mkdir()
    (directory / "project.json").write_text("{}", encoding="utf-8")
    server = make_server(directory, port=0, allowed_hosts=("example.test",))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        for host, expected in (
            ("example.test", 200),
            ("EXAMPLE.test:443", 200),
            ("other.test", 403),
        ):
            conn = http.client.HTTPConnection("127.0.0.1", port)
            conn.request("GET", "/api/project", headers={"Host": host})
            assert conn.getresponse().status == expected
            conn.close()
    finally:
        server.shutdown()
        server.server_close()


def test_save_and_rejections(running_server):
    directory, base, original = running_server
    previous = (directory / "project.json").read_bytes()
    sent = json.loads(json.dumps(original))
    sent["future_save"] = True
    sent["scenes"][0]["scene_future"] = {"x": 1}
    sent["scenes"][0]["note"] = "changed"
    body = json.dumps(sent, ensure_ascii=False, indent=2).encode()
    assert (
        request(
            base,
            "/api/project",
            method="PUT",
            body=body,
            headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
        )[0]
        == 200
    )
    written = (directory / "project.json").read_bytes()
    assert written == body + b"\n" and (directory / "project.json.bak").read_bytes() == previous
    assert (
        request(
            base,
            "/api/project",
            method="PUT",
            body=body,
            headers={"Content-Type": "application/json"},
        )[0]
        == 200
    )
    assert (directory / "project.json.bak").read_bytes() == written
    for bad, content_type in (
        (b"not json", "application/json"),
        (b"[]", "application/json"),
        (body, "text/plain"),
    ):
        before = (directory / "project.json").read_bytes()
        assert (
            request(
                base, "/api/project", method="PUT", body=bad, headers={"Content-Type": content_type}
            )[0]
            == 400
        )
        assert (directory / "project.json").read_bytes() == before
    assert request(base, "/api/project", method="POST")[0] == 405


def test_put_rejects_absolute_scene_path(running_server):
    directory, base, original = running_server
    before = (directory / "project.json").read_bytes()
    sent = json.loads(json.dumps(original))
    sent["scenes"][0]["path"] = "/etc/hosts"
    body = json.dumps(sent).encode()
    assert (
        request(
            base,
            "/api/project",
            method="PUT",
            body=body,
            headers={"Content-Type": "application/json"},
        )[0]
        == 400
    )
    assert (directory / "project.json").read_bytes() == before


def test_put_origin_and_fetch_site_guards(running_server):
    directory, base, original = running_server
    body = json.dumps(original).encode()
    before = (directory / "project.json").read_bytes()
    headers = {"Content-Type": "application/json", "Content-Length": str(len(body))}
    assert (
        request(
            base,
            "/api/project",
            method="PUT",
            body=body,
            headers={**headers, "Origin": "https://attacker.example"},
        )[0]
        == 403
    )
    assert (
        request(
            base, "/api/project", method="PUT", body=body, headers={**headers, "Origin": "null"}
        )[0]
        == 403
    )
    assert (
        request(
            base,
            "/api/project",
            method="PUT",
            body=body,
            headers={**headers, "Sec-Fetch-Site": "cross-site"},
        )[0]
        == 403
    )
    assert (directory / "project.json").read_bytes() == before
    host = base.removeprefix("http://")
    assert (
        request(
            base,
            "/api/project",
            method="PUT",
            body=body,
            headers={**headers, "Origin": f"http://{host}"},
        )[0]
        == 200
    )
    assert request(base, "/api/project", method="PUT", body=body, headers=headers)[0] == 200
    assert (
        request(
            base,
            "/api/project",
            method="PUT",
            body=body,
            headers={**headers, "Origin": f"http://127.0.0.1:{int(host.rsplit(':', 1)[1]) + 1}"},
        )[0]
        == 403
    )


def test_put_origin_behind_proxy(tmp_path):
    directory = tmp_path / "demo"
    directory.mkdir()
    data = {"name": "demo", "scenes": []}
    body = json.dumps(data).encode()
    (directory / "project.json").write_bytes(body + b"\n")
    server = make_server(directory, port=0, allowed_hosts=("proxy.example",))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        assert (
            request(
                f"http://127.0.0.1:{port}",
                "/api/project",
                method="PUT",
                body=body,
                headers={
                    "Host": "proxy.example",
                    "Origin": "https://proxy.example",
                    "Content-Type": "application/json",
                },
            )[0]
            == 200
        )
    finally:
        server.shutdown()
        server.server_close()


def test_oversize_is_rejected(running_server):
    directory, base, _ = running_server
    sent = {
        "name": "demo",
        "scenes": [
            {
                "id": "a",
                "path": "a.png",
                "order": 0,
                "importance": 3,
                "role": "reference",
                "note": "x" * (1_000_000 + 500),
            }
        ],
    }
    body = json.dumps(sent).encode()
    assert len(body) > MAX_BODY_BYTES
    status = request(
        base, "/api/project", method="PUT", body=body, headers={"Content-Type": "application/json"}
    )[0]
    assert status == 400 and not (directory / "project.json.bak").exists()


def test_helpers(tmp_path):
    assert all(is_loopback(host) for host in ("127.0.0.1", "localhost", "::1"))
    assert not is_loopback("0.0.0.0") and not is_loopback("192.0.2.1")
    assert exposure_warning("127.0.0.1") is None
    assert exposure_warning("0.0.0.0")
    server = make_server(tmp_path, port=0)
    assert server.server_address[1] > 0
    server.server_close()
    assert (ui_dir() / "index.html").exists() and (ui_dir() / "logic.js").exists()
