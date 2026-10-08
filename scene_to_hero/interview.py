from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass

from .project import Project

MAX_VALUE_LENGTH = 500


@dataclass(frozen=True)
class Question:
    section: str
    key: str
    text: str
    required: bool
    prefix: str


QUESTIONS: tuple[Question, ...] = (
    Question(
        "subject", "shape", "What shape is the subject? (silhouette, proportions)", True, "Shape:"
    ),
    Question("subject", "color", "What color is it?", False, "Color:"),
    Question(
        "subject", "material", "What material and surface texture does it have?", False, "Material:"
    ),
    Question("subject", "size", "How large does it look in the frame?", False, "Size:"),
    Question(
        "subject",
        "marking",
        "Is there printed text or a logo on it? Give the exact characters. They are drawn during video generation, never added afterward. Leave blank if there is none.",
        False,
        "Printed marking, kept exactly:",
    ),
    Question(
        "subject",
        "orientation",
        "Any orientation constraint? (for example always facing the camera, must not rotate)",
        False,
        "Orientation:",
    ),
    Question(
        "subject",
        "fragile",
        "Any fragile parts that tend to break in generation? (thin, transparent, small)",
        False,
        "Fragile parts:",
    ),
    Question(
        "subject",
        "differences",
        "If there are several objects, how do they differ from each other?",
        False,
        "Differences between objects:",
    ),
    Question("scenery", "background", "What kind of background is it?", True, "Background:"),
    Question(
        "scenery",
        "light",
        "Which direction does the light come from, and what color temperature?",
        False,
        "Light:",
    ),
    Question(
        "scenery", "motion", "Which elements move? (cloth, smoke, water)", False, "Moving elements:"
    ),
    Question(
        "scenery", "foreground", "Any blurred foreground elements?", False, "Blurred foreground:"
    ),
    Question(
        "scenery",
        "avoid",
        "Which effects must be avoided? (for example lens flares)",
        False,
        "Avoid:",
    ),
    Question(
        "scenery", "color_treatment", "What color treatment is intended?", False, "Color treatment:"
    ),
)

NOTICE = "Answers are saved as plain text in project.json. Do not type secrets such as API keys."


def build_description(section: str, values: dict[str, str]) -> str:
    parts = []
    for question in QUESTIONS:
        if question.section != section:
            continue
        value = str(values.get(question.key, "")).strip()
        if value:
            value = value.rstrip(". \t\r\n")
            prefix = question.prefix
            parts.append(f"{prefix} {value}" + ".")
    return " ".join(parts)


def _validate_value(question: Question, value: str) -> str:
    value = value.strip()
    if len(value) > MAX_VALUE_LENGTH:
        raise ValueError(f"{question.key} is too long (maximum {MAX_VALUE_LENGTH} characters)")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        raise ValueError(f"{question.key} must not contain control characters")
    fal_key = os.environ.get("FAL_KEY", "")
    if len(fal_key) >= 8 and fal_key in value:
        raise ValueError(f"{question.key} must not contain the configured environment key")
    return value


def _merge_section(knowledge: dict, name: str, answers: dict[str, str], questions=None) -> dict:
    section = dict(knowledge.get(name) or {})
    selected = questions or tuple(question for question in QUESTIONS if question.section == name)
    for question in selected:
        value = answers.get(question.key, section.get(question.key, ""))
        if not isinstance(value, str):
            raise ValueError(f"{name}.{question.key} must be a string")  # noqa: TRY004
        value = _validate_value(question, value)
        if question.required and not value:
            raise ValueError(f"{name}.{question.key} is required")
        section[question.key] = value
    return section


def _answers_object(raw: dict) -> dict[str, dict[str, str]]:
    if not isinstance(raw, dict):
        raise ValueError("answers must be an object")  # noqa: TRY004
    result = {}
    for section, values in raw.items():
        if section not in {"subject", "scenery"}:
            raise ValueError(f"unknown answers section: {section}")
        if not isinstance(values, dict):
            raise ValueError(f"answers section {section} must be an object")  # noqa: TRY004
        known = {question.key for question in QUESTIONS if question.section == section}
        for key, value in values.items():
            if key not in known:
                raise ValueError(f"unknown answers key: {section}.{key}")
            if not isinstance(value, str):
                raise ValueError(f"answers value {section}.{key} must be a string")  # noqa: TRY004
        result[section] = values
    return result


def _prepare(project: Project, answers: dict[str, dict[str, str]]) -> tuple[dict, dict[str, str]]:
    knowledge = project.knowledge
    merged = {}
    old_descriptions = {}
    for name in ("subject", "scenery"):
        current = dict(knowledge.get(name) or {})
        old_descriptions[name] = current.get("description", "")
        merged[name] = _merge_section(knowledge, name, answers.get(name, {}))
        merged[name]["description"] = build_description(name, merged[name])
    marking = merged["subject"].get("marking", "")
    has_marking = bool(marking)
    merged["subject"]["has_marking"] = has_marking
    result = dict(knowledge)
    result.update(merged)
    return result, old_descriptions


def _print_knowledge(knowledge: dict) -> None:
    for name in ("subject", "scenery"):
        section = knowledge.get(name) or {}
        for question in QUESTIONS:
            if question.section == name and section.get(question.key):
                print(f"{name}.{question.key}: {section[question.key]}")
        print(f"{name}.description: {section.get('description', '')}")
        if name == "subject":
            print(f"has_marking: {bool(section.get('has_marking', False))}")


def run_interview(
    project: Project,
    *,
    ask: Callable[[str], str] | None = None,
    answers: dict[str, dict[str, str]] | None = None,
    yes: bool = False,
    print_: bool = False,
) -> int:
    if print_:
        _print_knowledge(project.knowledge)
        return 0
    if answers is None:
        ask = ask or input
        print(NOTICE)
        collected = {"subject": {}, "scenery": {}}
        for question in QUESTIONS:
            existing = (project.knowledge.get(question.section) or {}).get(question.key, "")
            prompt = f"{question.text}"
            if existing:
                prompt += f" [{existing}]"
            prompt += ": "
            while True:
                value = ask(prompt)
                if value == "-":
                    value = ""
                elif value == "" and existing:
                    value = existing
                try:
                    _merge_section(
                        project.knowledge, question.section, {question.key: value}, (question,)
                    )
                except ValueError as exc:
                    print(str(exc))
                    continue
                collected[question.section][question.key] = value
                break
        answers = collected
    else:
        answers = _answers_object(answers)
    knowledge, old_descriptions = _prepare(project, answers)
    for name in ("subject", "scenery"):
        print(f"{name}.description: {knowledge[name]['description']}")
        if old_descriptions[name] and old_descriptions[name] != knowledge[name]["description"]:
            print(f"replaces existing {name}.description")
    if not yes and (ask or input)("Save? [y/N] ").strip().lower() != "y":
        return 1
    project.knowledge = knowledge
    return 0


__all__ = [
    "MAX_VALUE_LENGTH",
    "NOTICE",
    "QUESTIONS",
    "Question",
    "build_description",
    "run_interview",
]
