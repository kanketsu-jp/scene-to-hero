import json
import os

import pytest
from PIL import Image

from scene_to_hero.project import Project, Scene, default_root, load, project_dir, save, scan_scenes


def test_roundtrip_and_scan(tmp_path):
    scenes = tmp_path / "in"; scenes.mkdir()
    for name in ("b.jpg", "a.png"): Image.new("RGB", (2, 2)).save(scenes / name)
    found = scan_scenes(scenes); assert [x.id for x in found] == ["scene-01", "scene-02"]
    project = Project("demo", found, {})
    path = tmp_path / "project.json"; save(project, path); assert load(path).to_dict() == project.to_dict()


@pytest.mark.parametrize("change", [lambda s: setattr(s[0], "importance", 0), lambda s: setattr(s[0], "importance", 6), lambda s: setattr(s[0], "role", "bad")])
def test_validation(change):
    scenes = [Scene("a", "a.png", 0), Scene("b", "b.png", 1)]
    change(scenes)
    with pytest.raises(ValueError): Project("demo", scenes, {}).validate()
    scenes = [Scene("a", "a.png", 0, role="final"), Scene("b", "b.png", 1, role="final")]
    with pytest.raises(ValueError): Project("demo", scenes, {}).validate()
    scenes = [Scene("a", "a.png", 0), Scene("a", "b.png", 1)]
    with pytest.raises(ValueError): Project("demo", scenes, {}).validate()


def test_name_and_home(tmp_path, monkeypatch):
    with pytest.raises(ValueError): project_dir("../x", tmp_path)
    monkeypatch.setenv("SCENE_TO_HERO_HOME", str(tmp_path)); assert default_root() == tmp_path
