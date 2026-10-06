import json
import os
import re
from pathlib import Path

import pytest
from PIL import Image

from scene_to_hero.project import (
    Project,
    Scene,
    default_root,
    load,
    project_dir,
    resolve_scene_path,
    save,
    scan_scenes,
    validate_scene_path,
)

_LOGIC = Path(__file__).parents[1] / "scene_to_hero" / "ui" / "logic.js"
_VECTORS = json.loads(
    re.search(r"const scenePathVectors = (\{.*?\});", _LOGIC.read_text(), re.DOTALL).group(1)
)


def test_roundtrip_and_scan(tmp_path):
    scenes = tmp_path / "in"
    scenes.mkdir()
    for name in ("b.jpg", "a.png"):
        Image.new("RGB", (2, 2)).save(scenes / name)
    found = scan_scenes(scenes)
    assert [x.id for x in found] == ["scene-01", "scene-02"]
    project = Project("demo", found, {})
    path = tmp_path / "project.json"
    save(project, path)
    assert load(path).to_dict() == project.to_dict()


@pytest.mark.parametrize(
    "change",
    [
        lambda s: setattr(s[0], "importance", 0),
        lambda s: setattr(s[0], "importance", 6),
        lambda s: setattr(s[0], "role", "bad"),
    ],
)
def test_validation(change):
    scenes = [Scene("a", "a.png", 0), Scene("b", "b.png", 1)]
    change(scenes)
    with pytest.raises(ValueError):
        Project("demo", scenes, {}).validate()
    scenes = [Scene("a", "a.png", 0, role="final"), Scene("b", "b.png", 1, role="final")]
    with pytest.raises(ValueError):
        Project("demo", scenes, {}).validate()
    scenes = [Scene("a", "a.png", 0), Scene("a", "b.png", 1)]
    with pytest.raises(ValueError):
        Project("demo", scenes, {}).validate()


def test_name_and_home(tmp_path, monkeypatch):
    with pytest.raises(ValueError):
        project_dir("../x", tmp_path)
    monkeypatch.setenv("SCENE_TO_HERO_HOME", str(tmp_path))
    assert default_root() == tmp_path
    assert project_dir("demo", tmp_path / "demo") == tmp_path / "demo" / "demo"
    for name in (".", ".."):
        with pytest.raises(ValueError):
            project_dir(name, tmp_path)
        with pytest.raises(ValueError):
            Project(name, [], {}).validate()


@pytest.mark.parametrize("path", _VECTORS["reject"])
def test_scene_path_rejected(path):
    with pytest.raises(ValueError):
        validate_scene_path(path)


@pytest.mark.parametrize("path", _VECTORS["accept"])
def test_scene_path_accepted(path):
    assert validate_scene_path(path) == path


def test_resolve_scene_path_rejects_symlink_outside(tmp_path):
    directory = tmp_path / "project"
    scenes = directory / "scenes"
    scenes.mkdir(parents=True)
    outside = tmp_path / "outside.png"
    Image.new("RGB", (2, 2)).save(outside)
    os.symlink(outside, scenes / "link.png")
    with pytest.raises(ValueError, match="inside the project"):
        resolve_scene_path(directory, "scenes/link.png")


def test_unknown_keys_roundtrip_and_known_json_stays_unchanged(tmp_path):
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
                "future": {"nested": [1]},
            }
        ],
        "knowledge": {},
        "schema_version": 1,
        "project_future": ["x"],
    }
    path = tmp_path / "project.json"
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    project = load(path)
    save(project, path)
    assert load(path).to_dict() == data
    plain = {
        "name": "demo",
        "scenes": [
            {
                "id": "a",
                "path": "a.png",
                "order": 0,
                "importance": 3,
                "role": "reference",
                "note": "",
            }
        ],
        "knowledge": {},
        "schema_version": 1,
    }
    path.write_text(json.dumps(plain, indent=2) + "\n", encoding="utf-8")
    save(load(path), path)
    assert (
        path.read_text(encoding="utf-8") == json.dumps(plain, ensure_ascii=False, indent=2) + "\n"
    )
