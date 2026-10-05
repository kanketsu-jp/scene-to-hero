from pathlib import Path

import pytest

from scene_to_hero.budget import BudgetExceeded
from scene_to_hero.generate import (
    GenerateParams,
    build_payload,
    build_replace_last_frame_command,
    build_reverse_command,
    generate,
)


class FakeBudget:
    def __init__(self):
        self.calls = 0

    def reserve(self, *args, **kwargs):
        self.calls += 1
        return True, 0, 1

    def record_result(self, *args, **kwargs):
        pass


class FakeClient:
    def __init__(self):
        self.runs = []
        self.uploads = 0

    def run(self, endpoint, payload, output):
        self.runs.append(payload)
        raise RuntimeError("first")

    def upload_file(self, path):
        self.uploads += 1
        return "uploaded-image-url"


def test_commands_and_payload(tmp_path):
    assert (
        "-vf" in build_reverse_command("ffmpeg", "a", "b")
        and "reverse" in build_reverse_command("ffmpeg", "a", "b")
        and "-an" in build_reverse_command("ffmpeg", "a", "b")
    )
    command = build_replace_last_frame_command("ffmpeg", "r", "s", "d", 10, 2, 640, 480, 24)
    text = " ".join(command)
    assert "trim" in text and "concat" in text and "scale=640:480" in text
    params = GenerateParams(Path("a.png"), "p", tmp_path, "n", seed=None)
    assert "end_image_url" not in build_payload(params, "url") and "seed" not in build_payload(
        params, "url"
    )


def test_dry_run_has_no_side_effects(tmp_path):
    p = GenerateParams(tmp_path / "a.png", "p", tmp_path, "n")
    budget = FakeBudget()
    client = FakeClient()
    result = generate(p, budget, client, dry_run=True)
    assert (
        result["est_usd"] == 0.48
        and budget.calls == 0
        and not client.runs
        and not (tmp_path / "logs").exists()
    )


def test_retry_uses_upload_and_budget_rejection(tmp_path):
    image = tmp_path / "a.png"
    image.write_bytes(b"x")
    p = GenerateParams(image, "p", tmp_path, "n")
    budget = FakeBudget()
    client = FakeClient()
    with pytest.raises(RuntimeError):
        generate(p, budget, client)
    assert budget.calls == 2 and client.uploads == 1

    class Denied(FakeBudget):
        def reserve(self, *args, **kwargs):
            self.calls += 1
            return False, 1, 1

    denied = Denied()
    with pytest.raises(BudgetExceeded):
        generate(p, denied, client)
    assert client.runs[-1].get("image_url") != "new"


def test_duration_validation(tmp_path):
    p = GenerateParams(tmp_path / "a.png", "p", tmp_path, "n", duration=0.1)
    with pytest.raises(ValueError):
        generate(p, FakeBudget(), FakeClient())
