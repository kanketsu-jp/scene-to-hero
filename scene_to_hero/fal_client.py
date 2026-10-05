from __future__ import annotations

import base64
import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path


class MissingApiKey(RuntimeError):
    pass


def get_api_key(env=os.environ) -> str:
    key = env.get("FAL_KEY", "")
    if not key:
        raise MissingApiKey("set the FAL_KEY environment variable")
    return key


def _http_transport(method, url, headers, body_bytes, timeout):
    request = urllib.request.Request(url, data=body_bytes, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, response.read()


def _json(body: bytes) -> dict:
    return json.loads(body.decode("utf-8"))


def _find_url(value):
    if isinstance(value, dict):
        for key in ("url", "file_url"):
            if isinstance(value.get(key), str):
                return value[key]
        for item in value.values():
            found = _find_url(item)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_url(item)
            if found:
                return found
    return None


class FalClient:
    def __init__(
        self, key: str, transport=None, sleep=time.sleep, poll_seconds=5, timeout_seconds=900
    ):
        self.key = key
        self.transport = transport or _http_transport
        self.sleep = sleep
        self.poll_seconds = poll_seconds
        self.timeout_seconds = timeout_seconds

    def _request(self, method, url, body=None, content_type="application/json", timeout=180):
        headers = {"Authorization": f"Key {self.key}", "Accept": "application/json"}
        if content_type:
            headers["Content-Type"] = content_type
        status, data = self.transport(method, url, headers, body, timeout)
        if status < 200 or status >= 300:
            raise RuntimeError(f"HTTP request failed with status {status}")
        return data

    def run(self, endpoint: str, payload: dict, output_path: Path, *, image_fields=None) -> dict:
        del image_fields
        base = f"https://queue.fal.run/{endpoint}"
        response = _json(self._request("POST", base, json.dumps(payload).encode("utf-8")))
        request_id = response.get("request_id")
        output_url = _find_url(response)
        started = time.monotonic()
        if not output_url:
            status_url = response.get("status_url") or f"{base}/requests/{request_id}/status"
            response_url = response.get("response_url") or f"{base}/requests/{request_id}"
            while True:
                if time.monotonic() - started > self.timeout_seconds:
                    raise TimeoutError("fal request timed out")
                status_payload = _json(self._request("GET", status_url, None, timeout=180))
                status = str(status_payload.get("status", "")).upper()
                if status == "COMPLETED":
                    result = _json(self._request("GET", response_url, None, timeout=180))
                    output_url = _find_url(result)
                    break
                if status in {"FAILED", "CANCELED", "CANCELLED"}:
                    raise RuntimeError(f"fal request failed: {status}")
                self.sleep(self.poll_seconds)
        if not output_url:
            raise RuntimeError("fal response did not contain an output URL")
        if output_url.startswith("data:"):
            try:
                encoded = output_url.split(",", 1)[1]
                data = base64.b64decode(encoded)
            except (IndexError, ValueError) as exc:
                raise RuntimeError("invalid data URL") from exc
        else:
            data = self._request("GET", output_url, None, content_type=None, timeout=900)
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("xb") as stream:
            stream.write(data)
        return {
            "request_id": request_id,
            "output_url_host": urllib.parse.urlparse(output_url).netloc,
        }

    def upload_file(self, path: Path) -> str:
        path = Path(path)
        content_type = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        initiate = self._request(
            "POST",
            "https://rest.fal.ai/storage/upload/initiate?storage_type=fal-cdn-v3",
            json.dumps({"file_name": path.name, "content_type": content_type}).encode("utf-8"),
        )
        details = _json(initiate)
        upload_url = details.get("upload_url")
        file_url = details.get("file_url") or details.get("url")
        if not upload_url or not file_url:
            raise RuntimeError("storage upload response was incomplete")
        self._request("PUT", upload_url, path.read_bytes(), content_type=content_type, timeout=900)
        return file_url
