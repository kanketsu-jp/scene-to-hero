from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

SCHEMA_VERSION = 1
_NAME = re.compile(r"^[A-Za-z0-9._-]+$")


@dataclass
class Scene:
    id: str
    path: str
    order: int
    importance: int = 3
    role: str = "reference"
    note: str = ""
    extra: dict = field(default_factory=dict)


@dataclass
class Project:
    name: str
    scenes: list[Scene]
    knowledge: dict
    schema_version: int = SCHEMA_VERSION
    extra: dict = field(default_factory=dict)

    def final_scene(self) -> Scene | None:
        finals = [scene for scene in self.scenes if scene.role == "final"]
        return finals[0] if finals else None

    def validate(self) -> None:
        if not _NAME.fullmatch(self.name):
            raise ValueError(
                "project name must contain only letters, numbers, dot, underscore, or hyphen"
            )
        ids = [scene.id for scene in self.scenes]
        orders = [scene.order for scene in self.scenes]
        if len(ids) != len(set(ids)):
            raise ValueError("scene ids must be unique")
        if len(orders) != len(set(orders)):
            raise ValueError("scene orders must be unique")
        for scene in self.scenes:
            if (
                isinstance(scene.importance, bool)
                or not isinstance(scene.importance, int)
                or not 1 <= scene.importance <= 5
            ):
                raise ValueError("importance must be between 1 and 5")
            if scene.role not in {"final", "reference"}:
                raise ValueError("scene role must be final or reference")
        if sum(scene.role == "final" for scene in self.scenes) > 1:
            raise ValueError("there can be at most one final scene")

    def to_dict(self) -> dict:
        result = {
            "name": self.name,
            "scenes": [],
            "knowledge": self.knowledge,
            "schema_version": self.schema_version,
        }
        for scene in self.scenes:
            item = {
                "id": scene.id,
                "path": scene.path,
                "order": scene.order,
                "importance": scene.importance,
                "role": scene.role,
                "note": scene.note,
            }
            item.update(scene.extra)
            result["scenes"].append(item)
        result.update(self.extra)
        return result

    @classmethod
    def from_dict(cls, d) -> Project:
        scene_keys = {"id", "path", "order", "importance", "role", "note"}
        scenes = []
        for item in d.get("scenes", []):
            known = {key: item[key] for key in scene_keys if key in item}
            scenes.append(
                Scene(
                    **known,
                    extra={key: value for key, value in item.items() if key not in scene_keys},
                )
            )
        project_keys = {"name", "scenes", "knowledge", "schema_version"}
        project = cls(
            name=d["name"],
            scenes=scenes,
            knowledge=d.get("knowledge", {}),
            schema_version=d.get("schema_version", SCHEMA_VERSION),
            extra={key: value for key, value in d.items() if key not in project_keys},
        )
        project.validate()
        return project


def default_root() -> Path:
    return Path(os.environ.get("SCENE_TO_HERO_HOME", Path.home() / "Downloads" / "scene-to-hero"))


def project_dir(name, root=None) -> Path:
    if not _NAME.fullmatch(name):
        raise ValueError("invalid project name")
    return (Path(root) if root is not None else default_root()) / name


def load(path: Path) -> Project:
    with Path(path).open(encoding="utf-8") as stream:
        return Project.from_dict(json.load(stream))


def save(project, path: Path) -> None:
    project.validate()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(project.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def scan_scenes(directory: Path) -> list[Scene]:
    paths = sorted(
        (
            item
            for item in Path(directory).iterdir()
            if item.is_file() and item.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
        ),
        key=lambda p: p.name,
    )
    return [
        Scene(id=f"scene-{index:02d}", path=path.name, order=index)
        for index, path in enumerate(paths, 1)
    ]
