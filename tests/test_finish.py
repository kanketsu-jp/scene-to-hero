import shutil
import subprocess
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

from scene_to_hero.converge import fit_cover as converge_fit_cover
from scene_to_hero.finish import (
    IntroBlur,
    blur_frame,
    build_decode_command,
    build_encode_command,
    finish,
    focus_weight,
)

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
LOSSLESS = ["-c:v", "png", "-pix_fmt", "rgb24"]


def make_media(tmp_path, static=False):
    if not FFMPEG or not FFPROBE:
        pytest.skip("ffmpeg and ffprobe are required")
    rng = np.random.default_rng(27)
    base = rng.integers(0, 256, (160, 240), dtype=np.uint8)
    base = cv2.GaussianBlur(base, (0, 0), 3)
    base = cv2.normalize(base, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    frames = []
    for index in range(36):
        shift = (0, 0) if static else (index // 3, index % 5)
        frames.append(np.repeat(np.roll(base, shift, axis=(0, 1))[..., None], 3, axis=2))
    frame_dir = tmp_path / ("static-frames" if static else "frames")
    frame_dir.mkdir()
    for index, frame in enumerate(frames, 1):
        Image.fromarray(frame).save(frame_dir / f"{index:08d}.png")
    source = tmp_path / ("static.mov" if static else "source.mov")
    subprocess.run(
        [
            FFMPEG,
            "-y",
            "-v",
            "error",
            "-framerate",
            "24",
            "-i",
            str(frame_dir / "%08d.png"),
            *LOSSLESS,
            str(source),
        ],
        check=True,
    )
    return source, frames


def decode(video, directory):
    directory.mkdir()
    subprocess.run(
        [FFMPEG, "-y", "-v", "error", "-i", str(video), "-vsync", "0", str(directory / "%08d.png")],
        check=True,
    )
    return [
        np.asarray(Image.open(path).convert("RGB"), dtype=np.uint8)
        for path in sorted(directory.glob("*.png"))
    ]


def lapvar(frame):
    gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def test_pass_through_and_hold(tmp_path):
    source, frames = make_media(tmp_path)
    output = tmp_path / "pass.mov"
    result = finish(source, output, encode_args=LOSSLESS, ffmpeg=FFMPEG, ffprobe=FFPROBE)
    actual = decode(output, tmp_path / "pass-decoded")
    assert len(actual) == 36 and result.width == 240 and result.height == 160
    assert all(np.array_equal(a, b) for a, b in zip(actual, frames))
    held_output = tmp_path / "held.mov"
    held = finish(
        source, held_output, hold_frames=5, encode_args=LOSSLESS, ffmpeg=FFMPEG, ffprobe=FFPROBE
    )
    held_frames = decode(held_output, tmp_path / "held-decoded")
    assert len(held_frames) == 41 and held.output_frame_count == 41 and held.hold_frames == 5
    assert all(np.array_equal(a, b) for a, b in zip(held_frames[:36], frames))
    assert all(np.array_equal(frame, frames[-1]) for frame in held_frames[36:])
    assert held.switched is False


def test_switch_and_intro_blur(tmp_path):
    source, frames = make_media(tmp_path)
    switch_path = tmp_path / "switch.png"
    switch = np.zeros((100, 100, 3), dtype=np.uint8)
    switch[..., 0] = np.arange(100, dtype=np.uint8)[None, :]
    switch[..., 1] = np.arange(100, dtype=np.uint8)[:, None]
    Image.fromarray(switch).save(switch_path)
    output = tmp_path / "switch.mov"
    result = finish(
        source,
        output,
        hold_frames=3,
        switch_image=switch_path,
        encode_args=LOSSLESS,
        ffmpeg=FFMPEG,
        ffprobe=FFPROBE,
    )
    actual = decode(output, tmp_path / "switch-decoded")
    expected, box = converge_fit_cover(Image.open(switch_path), (240, 160))
    assert np.max(np.abs(actual[-1].astype(np.int16) - expected.astype(np.int16))) == 0
    assert all(np.array_equal(frame, expected) for frame in actual[-3:])
    assert np.array_equal(actual[35], frames[-1]) and result.switched and result.crop_box == box
    assert all(np.array_equal(a, b) for a, b in zip(actual[:36], frames))
    with pytest.raises(ValueError):
        finish(
            source, tmp_path / "bad.mov", switch_image=switch_path, ffmpeg=FFMPEG, ffprobe=FFPROBE
        )
    blur_output = tmp_path / "blur.mov"
    blur_result = finish(
        source,
        blur_output,
        intro_blur=IntroBlur(8, 4, 10),
        encode_args=LOSSLESS,
        ffmpeg=FFMPEG,
        ffprobe=FFPROBE,
    )
    blurred = decode(blur_output, tmp_path / "blur-decoded")
    assert len(blurred) == 36 and lapvar(blurred[0]) < 0.1 * lapvar(frames[0])
    assert not np.array_equal(blurred[0], frames[0])
    assert all(np.array_equal(blurred[i], frames[i]) for i in range(14, 36))
    assert blur_result.intro_blur_frames == 14


@pytest.mark.parametrize("sigma,hold,ramp", [(8, 4, 10), (6, 2, 8)])
@pytest.mark.parametrize("curve", ["smoothstep", "linear"])
def test_static_blur_amount(sigma, hold, ramp, curve, tmp_path):
    source, frames = make_media(tmp_path, static=True)
    output = tmp_path / f"{curve}-{sigma}.mov"
    finish(
        source,
        output,
        intro_blur=IntroBlur(sigma, hold, ramp, curve),
        encode_args=LOSSLESS,
        ffmpeg=FFMPEG,
        ffprobe=FFPROBE,
    )
    actual = decode(output, tmp_path / f"decoded-{curve}-{sigma}")
    values = [lapvar(frame) for frame in actual]
    assert all(values[i] <= values[i + 1] + 1e-9 for i in range(35))
    assert all(np.array_equal(actual[i], actual[0]) for i in range(hold + 1))
    assert values[0] < 0.1 * lapvar(frames[0])
    for index in range(hold, hold + ramp - 1):
        if sigma * focus_weight(index, hold, ramp, curve) >= 1.0:
            assert values[index + 1] > values[index]


def test_all_three_and_pure_helpers(tmp_path):
    source, frames = make_media(tmp_path)
    switch_path = tmp_path / "switch.png"
    Image.fromarray(np.full((100, 100, 3), (30, 90, 180), dtype=np.uint8)).save(switch_path)
    output = tmp_path / "all.mov"
    finish(
        source,
        output,
        hold_frames=4,
        switch_image=switch_path,
        intro_blur=IntroBlur(6, 2, 8),
        encode_args=LOSSLESS,
        ffmpeg=FFMPEG,
        ffprobe=FFPROBE,
    )
    actual = decode(output, tmp_path / "all-decoded")
    expected, _ = converge_fit_cover(Image.open(switch_path), (240, 160))
    assert len(actual) == 40 and not np.array_equal(actual[0], frames[0])
    assert all(np.array_equal(actual[i], frames[i]) for i in range(10, 36))
    assert all(np.array_equal(frame, expected) for frame in actual[36:])
    assert focus_weight(4, 4, 10, "linear") == 1.0
    assert focus_weight(14, 4, 10, "linear") == 0.0
    assert focus_weight(9, 4, 10, "linear") == 0.5
    assert focus_weight(9, 4, 10, "smoothstep") == 0.5
    with pytest.raises(ValueError):
        focus_weight(1, 0, 2, "bad")
    constant = np.full((8, 8, 3), 100, dtype=np.uint8)
    assert np.array_equal(blur_frame(constant, 0), constant)
    assert np.max(np.abs(blur_frame(constant, 3).astype(int) - constant.astype(int))) <= 1


def test_validation_and_default_encode(tmp_path):
    source, _ = make_media(tmp_path)
    with pytest.raises(ValueError):
        finish(source, tmp_path / "x.mov", hold_frames=-1, ffmpeg=FFMPEG, ffprobe=FFPROBE)
    with pytest.raises(ValueError):
        finish(source, tmp_path / "x.mov", intro_blur=IntroBlur(0), ffmpeg=FFMPEG, ffprobe=FFPROBE)
    with pytest.raises(ValueError):
        finish(
            source,
            tmp_path / "x.mov",
            intro_blur=IntroBlur(1, ramp_frames=0),
            ffmpeg=FFMPEG,
            ffprobe=FFPROBE,
        )
    with pytest.raises(ValueError):
        finish(
            source,
            tmp_path / "x.mov",
            intro_blur=IntroBlur(1, curve="bad"),
            ffmpeg=FFMPEG,
            ffprobe=FFPROBE,
        )
    with pytest.raises(ValueError):
        finish(
            source,
            tmp_path / "x.mov",
            intro_blur=IntroBlur(1, 30, 10),
            ffmpeg=FFMPEG,
            ffprobe=FFPROBE,
        )
    with pytest.raises(FileNotFoundError):
        finish(
            source,
            tmp_path / "x.mov",
            hold_frames=1,
            switch_image=tmp_path / "missing.png",
            ffmpeg=FFMPEG,
            ffprobe=FFPROBE,
        )
    output = tmp_path / "default.mp4"
    finish(source, output, hold_frames=3, ffmpeg=FFMPEG, ffprobe=FFPROBE)
    assert output.exists()
    with pytest.raises(FileExistsError):
        finish(source, output, ffmpeg=FFMPEG, ffprobe=FFPROBE)
    overwritten = finish(source, output, overwrite=True, ffmpeg=FFMPEG, ffprobe=FFPROBE)
    assert overwritten.output_frame_count == 36


def test_command_builders_and_runner_injection(tmp_path):
    decode_command = build_decode_command("ffmpeg", "in.mov", "frames/%08d.png")
    assert (
        "-an" in decode_command
        and "-fps_mode" in decode_command
        and "passthrough" in decode_command
    )
    default = build_encode_command("ffmpeg", "frames/%08d.png", "out.mov", 24)
    custom = build_encode_command(
        "ffmpeg", "frames/%08d.png", "out.mov", 24, encode_args=["-c:v", "png"]
    )
    assert (
        "-framerate" in default
        and "libx264" in default
        and "-crf" in default
        and "-pix_fmt" in default
    )
    assert "libx264" not in custom
    source, _ = make_media(tmp_path)
    calls = []

    def recording_runner(command, **kwargs):
        calls.append(command)
        return subprocess.run(command, check=kwargs.pop("check", False), **kwargs)

    finish(
        source,
        tmp_path / "recorded.mov",
        encode_args=LOSSLESS,
        runner=recording_runner,
        ffmpeg=FFMPEG,
        ffprobe=FFPROBE,
    )
    media_calls = [call for call in calls if Path(call[0]).name in {"ffmpeg", "ffprobe"}]
    assert any("-fps_mode" in call for call in media_calls)
    assert any("-framerate" in call for call in media_calls)
    assert next(i for i, call in enumerate(media_calls) if "-fps_mode" in call) < next(
        i for i, call in enumerate(media_calls) if "-framerate" in call
    )


def test_no_coupling_and_file_import(tmp_path):
    import ast

    import scene_to_hero.finish as module

    tree = ast.parse(Path(module.__file__).read_text())
    assert all(
        not (node.module or "").startswith("scene_to_hero")
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
    )
    assert not any(
        alias.name.startswith("scene_to_hero")
        for node in tree.body
        if isinstance(node, ast.Import)
        for alias in node.names
    )
