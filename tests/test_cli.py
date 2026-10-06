import shutil
import subprocess

from PIL import Image

from scene_to_hero.cli import _next_generate_name, main
from scene_to_hero.project import Project, Scene, load, save


def test_cli_help_and_unimplemented(capsys):
    assert main(["--help"]) == 0
    assert main(["upscale"]) == 2
    assert "not implemented yet" in capsys.readouterr().err


def test_cli_init_order_dry_run(tmp_path, monkeypatch, capsys):
    source = tmp_path / "input"
    source.mkdir()
    for name in ("one.png", "two.png"):
        Image.new("RGB", (2, 2)).save(source / name)
    monkeypatch.setenv("SCENE_TO_HERO_HOME", str(tmp_path / "home"))
    assert main(["init", "demo", "--scenes", str(source)]) == 0
    assert main(["order", "demo", "--final", "scene-02"]) == 0
    monkeypatch.delenv("FAL_KEY", raising=False)
    assert main(["generate", "demo", "--prompt", "test", "--dry-run"]) == 0
    assert main(["generate", "demo", "--prompt", "test", "--dry-run"]) == 0
    output = capsys.readouterr().out
    assert "est" in output and "$" in output
    assert not (tmp_path / "home" / "demo" / "logs").exists()
    assert not (tmp_path / "home" / "demo" / "clips").exists()


def test_generate_name_helper(tmp_path):
    (tmp_path / "hero-001.mp4").touch()
    assert _next_generate_name(tmp_path) == "hero-002"


def test_ui_allow_host_reaches_server(monkeypatch, tmp_path, capsys):
    directory = tmp_path / "demo"
    directory.mkdir()
    save(Project("demo", [], {}), directory / "project.json")
    seen = {}

    def fake_run(project_dir, host, port, allowed_hosts):
        seen.update(project_dir=project_dir, host=host, port=port, allowed_hosts=allowed_hosts)
        return 0

    monkeypatch.setattr("scene_to_hero.cli.serve.run", fake_run)
    assert (
        main(["ui", "demo", "--root", str(tmp_path), "--allow-host", "a", "--allow-host", "b"]) == 0
    )
    assert seen["allowed_hosts"] == ("a", "b")
    assert "allowing Host: a, b" in capsys.readouterr().err


def test_cli_converge_end_to_end(tmp_path, capsys):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise AssertionError("ffmpeg is required")
    root = tmp_path / "root"
    directory = root / "demo"
    directory.mkdir(parents=True)
    final = directory / "final.png"
    Image.new("RGB", (32, 24), (230, 20, 40)).save(final)
    frames = tmp_path / "frames"
    frames.mkdir()
    for index in range(1, 5):
        Image.new("RGB", (32, 24), (index * 20, 30, 40)).save(frames / f"{index:08d}.png")
    video = tmp_path / "candidate.mov"
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-v",
            "error",
            "-framerate",
            "24",
            "-i",
            str(frames / "%08d.png"),
            "-c:v",
            "png",
            "-pix_fmt",
            "rgb24",
            str(video),
        ],
        check=True,
    )
    save(
        Project("demo", [Scene("final", "final.png", 0, role="final")], {}),
        directory / "project.json",
    )
    assert (
        main(["converge", "demo", "--root", str(root), "--video", str(video), "--frames", "2"]) == 0
    )
    assert (directory / "converged" / "candidate_converged.mp4").exists()
    assert (directory / "converged" / "candidate_sheet.jpg").exists()
    output = capsys.readouterr().out
    assert "review the contact sheet by eye; numbers alone are not a pass" in output


def test_cli_converge_requires_final(tmp_path, capsys):
    directory = tmp_path / "demo"
    directory.mkdir()
    save(Project("demo", [Scene("a", "a.png", 0)], {}), directory / "project.json")
    assert main(["converge", "demo", "--root", str(tmp_path), "--video", "missing.mp4"]) == 1
    assert "no final scene is set" in capsys.readouterr().err


def test_budget_guard_precedes_prompt_and_key(tmp_path, monkeypatch, capsys):
    root = tmp_path / "root"
    directory = root / "demo"
    directory.mkdir(parents=True)
    save(Project("demo", [Scene("a", "a.png", 0, role="final")], {}), directory / "project.json")
    monkeypatch.delenv("FAL_KEY", raising=False)
    monkeypatch.setattr(
        "builtins.input", lambda _: (_ for _ in ()).throw(AssertionError("prompt was called"))
    )
    assert (
        main(["generate", "demo", "--root", str(root), "--prompt", "test", "--budget", "0.1"]) == 3
    )
    assert "budget guard" in capsys.readouterr().err
    assert not (directory / "logs").exists()


def test_order_preserves_unknown_keys(tmp_path):
    directory = tmp_path / "demo"
    directory.mkdir()
    data = {
        "name": "demo",
        "scenes": [
            {
                "id": "a",
                "path": "a.png",
                "order": 0,
                "importance": 3,
                "role": "reference",
                "note": "",
                "scene_future": {"x": 1},
            }
        ],
        "knowledge": {},
        "schema_version": 1,
        "project_future": [1],
    }
    (directory / "project.json").write_text(__import__("json").dumps(data) + "\n", encoding="utf-8")
    assert main(["order", "demo", "--root", str(tmp_path), "--final", "a"]) == 0
    result = load(directory / "project.json").to_dict()
    assert result["project_future"] == [1] and result["scenes"][0]["scene_future"] == {"x": 1}
