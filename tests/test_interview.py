import json

from PIL import Image

from scene_to_hero.cli import main
from scene_to_hero.interview import QUESTIONS, build_description
from scene_to_hero.project import Project, Scene, load, save


def _project(tmp_path, knowledge=None, *, final=True):
    directory = tmp_path / "home" / "demo"
    directory.mkdir(parents=True)
    Image.new("RGB", (2, 2)).save(directory / "final.png")
    save(
        Project(
            "demo",
            [Scene("final", "final.png", 0, role="final" if final else "reference")],
            knowledge or {},
        ),
        directory / "project.json",
    )
    return directory


def _answers_file(tmp_path, value):
    path = tmp_path / "answers.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _interactive(monkeypatch, values):
    iterator = iter(values)
    monkeypatch.setattr("builtins.input", lambda _prompt: next(iterator))


def test_interactive_round_trip_and_description(tmp_path, monkeypatch):
    monkeypatch.delenv("FAL_KEY", raising=False)
    directory = _project(tmp_path, {"subject": {}, "scenery": {}})
    _interactive(
        monkeypatch,
        [
            "cup",
            "blue",
            "ceramic",
            "small",
            "logo",
            "front",
            "",
            "",
            "studio",
            "left",
            "",
            "",
            "",
            "",
            "y",
        ],
    )
    assert main(["interview", "demo", "--root", str(tmp_path / "home")]) == 0
    knowledge = load(directory / "project.json").knowledge
    assert (
        knowledge["subject"]["description"]
        == "Shape: cup. Color: blue. Material: ceramic. Size: small. Printed marking, kept exactly: logo. Orientation: front."
    )
    assert knowledge["scenery"]["description"] == "Background: studio. Light: left."
    assert knowledge["subject"]["has_marking"] is True


def test_blank_marking_sets_false(tmp_path, monkeypatch):
    directory = _project(tmp_path, {"subject": {}, "scenery": {}})
    _interactive(
        monkeypatch, ["cup", "", "", "", "", "", "", "", "studio", "", "", "", "", "", "y"]
    )
    assert main(["interview", "demo", "--root", str(tmp_path / "home")]) == 0
    assert load(directory / "project.json").knowledge["subject"]["has_marking"] is False


def test_existing_values_and_unknown_keys_are_preserved(tmp_path, monkeypatch):
    knowledge = {"subject": {"shape": "old", "zz": 1}, "scenery": {}, "other": 5}
    directory = _project(tmp_path, knowledge)
    _interactive(
        monkeypatch,
        ["", "new color", "", "", "", "", "", "", "background", "", "", "", "", "", "y"],
    )
    assert main(["interview", "demo", "--root", str(tmp_path / "home")]) == 0
    result = load(directory / "project.json")
    assert result.knowledge["subject"]["shape"] == "old"
    assert result.knowledge["subject"]["color"] == "new color"
    assert result.knowledge["subject"]["zz"] == 1 and result.knowledge["other"] == 5


def test_clear_and_invalid_interactive_values_are_reasked(tmp_path, monkeypatch):
    _project(tmp_path, {"subject": {}, "scenery": {}})
    long_value = "x" * 501
    _interactive(
        monkeypatch,
        [
            "-",
            "shape",
            long_value,
            "blue",
            "",
            "",
            "",
            "",
            "",
            "",
            "background",
            "",
            "",
            "",
            "",
            "",
            "y",
        ],
    )
    assert main(["interview", "demo", "--root", str(tmp_path / "home")]) == 0
    result = load(tmp_path / "home" / "demo" / "project.json")
    assert result.knowledge["subject"]["shape"] == "shape"
    assert result.knowledge["subject"]["color"] == "blue"


def test_required_answers_are_rejected_without_write(tmp_path, monkeypatch):
    directory = _project(tmp_path, {"subject": {}, "scenery": {}})
    before = (directory / "project.json").read_bytes()
    path = _answers_file(
        tmp_path, {"subject": {"color": "blue"}, "scenery": {"background": "studio"}}
    )
    assert (
        main(
            ["interview", "demo", "--root", str(tmp_path / "home"), "--answers", str(path), "--yes"]
        )
        == 1
    )
    assert (directory / "project.json").read_bytes() == before


def test_answers_happy_path_enables_promptless_generate(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("FAL_KEY", raising=False)
    _project(tmp_path, {"subject": {}, "scenery": {}})
    answers = _answers_file(
        tmp_path, {"subject": {"shape": "cup"}, "scenery": {"background": "studio"}}
    )
    assert (
        main(
            [
                "interview",
                "demo",
                "--root",
                str(tmp_path / "home"),
                "--answers",
                str(answers),
                "--yes",
            ]
        )
        == 0
    )
    assert main(["generate", "demo", "--root", str(tmp_path / "home"), "--dry-run"]) == 0
    assert "billed_seconds" in capsys.readouterr().out


def test_unknown_answers_and_non_strings_do_not_write(tmp_path):
    directory = _project(tmp_path, {"subject": {}, "scenery": {}})
    before = (directory / "project.json").read_bytes()
    for data in ({"nope": {}}, {"subject": {"nope": "x"}}, {"subject": {"shape": 1}}):
        path = _answers_file(tmp_path, data)
        assert (
            main(
                [
                    "interview",
                    "demo",
                    "--root",
                    str(tmp_path / "home"),
                    "--answers",
                    str(path),
                    "--yes",
                ]
            )
            == 1
        )
        assert (directory / "project.json").read_bytes() == before


def test_decline_and_eof_do_not_write(tmp_path, monkeypatch):
    directory = _project(tmp_path, {"subject": {}, "scenery": {}})
    before = (directory / "project.json").read_bytes()
    answers = {"subject": {"shape": "cup"}, "scenery": {"background": "studio"}}
    path = _answers_file(tmp_path, answers)
    _interactive(monkeypatch, ["n"])
    assert (
        main(["interview", "demo", "--root", str(tmp_path / "home"), "--answers", str(path)]) == 1
    )
    assert (directory / "project.json").read_bytes() == before


def test_print_and_print_conflict(tmp_path, capsys):
    _project(
        tmp_path,
        {"subject": {"shape": "cup", "description": "Shape: cup."}, "scenery": {"description": ""}},
    )
    assert main(["interview", "demo", "--root", str(tmp_path / "home"), "--print"]) == 0
    assert "subject.shape: cup" in capsys.readouterr().out
    assert main(["interview", "demo", "--root", str(tmp_path / "home"), "--print", "--yes"]) == 2


def test_handwritten_description_is_replaced(tmp_path, capsys):
    _project(tmp_path, {"subject": {"description": "old"}, "scenery": {"description": "old scene"}})
    path = _answers_file(
        tmp_path, {"subject": {"shape": "cup"}, "scenery": {"background": "studio"}}
    )
    assert (
        main(
            ["interview", "demo", "--root", str(tmp_path / "home"), "--answers", str(path), "--yes"]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "replaces existing subject.description" in output
    result = load(tmp_path / "home" / "demo" / "project.json")
    assert result.knowledge["subject"]["description"] == "Shape: cup."


def test_fal_key_is_rejected_without_leaking(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FAL_KEY", "abcdefgh12345")
    _project(tmp_path, {"subject": {}, "scenery": {}})
    path = _answers_file(
        tmp_path, {"subject": {"shape": "abcdefgh12345"}, "scenery": {"background": "studio"}}
    )
    assert (
        main(
            ["interview", "demo", "--root", str(tmp_path / "home"), "--answers", str(path), "--yes"]
        )
        == 1
    )
    captured = capsys.readouterr()
    assert "abcdefgh12345" not in captured.out + captured.err
    assert "abcdefgh12345" not in (tmp_path / "home" / "demo" / "project.json").read_text()


def test_questions_and_skill_stay_in_sync(tmp_path):
    # The test file is in repository/tests; resolve through the current repository path.
    skill = (
        __import__("pathlib").Path(__file__).parents[1] / "skills" / "scene-to-hero" / "SKILL.md"
    )
    text = skill.read_text(encoding="utf-8")
    assert all(f"`{question.key}`" in text for question in QUESTIONS)
    assert all(
        word not in (question.text + question.key).lower()
        for question in QUESTIONS
        for word in ("api", "token", "secret", "password", "credential")
    )


def test_build_description_order_and_periods():
    assert (
        build_description("subject", {"shape": "cup...", "color": "blue"})
        == "Shape: cup. Color: blue."
    )
