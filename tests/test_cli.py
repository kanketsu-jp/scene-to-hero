import os
from pathlib import Path

from PIL import Image

from scene_to_hero.cli import main


def test_cli_help_and_unimplemented(capsys):
    assert main(["--help"]) == 0
    assert main(["finish"]) == 2
    assert "not implemented yet" in capsys.readouterr().err


def test_cli_init_order_dry_run(tmp_path, monkeypatch, capsys):
    source = tmp_path / "input"; source.mkdir()
    for name in ("one.png", "two.png"): Image.new("RGB", (2, 2)).save(source / name)
    monkeypatch.setenv("SCENE_TO_HERO_HOME", str(tmp_path / "home"))
    assert main(["init", "demo", "--scenes", str(source)]) == 0
    assert main(["order", "demo", "--final", "scene-02"]) == 0
    monkeypatch.delenv("FAL_KEY", raising=False)
    assert main(["generate", "demo", "--prompt", "test", "--dry-run"]) == 0
    output = capsys.readouterr().out; assert "est" in output and "$" in output
    assert not (tmp_path / "home" / "demo" / "logs").exists()
    assert not (tmp_path / "home" / "demo" / "clips").exists()
