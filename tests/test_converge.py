import json
import shutil
import subprocess
from fractions import Fraction

import cv2
import numpy as np
import pytest
from PIL import Image

from scene_to_hero.converge import (
    build_decode_command,
    build_encode_command,
    contact_sheet,
    converge,
    fit_cover,
)

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


def test_command_builders():
    decode = build_decode_command("ffmpeg", "in.mov", "frames/%08d.png")
    assert "-an" in decode and "-fps_mode" in decode and "passthrough" in decode
    default = build_encode_command("ffmpeg", "frames/%08d.png", "out.mov", 24)
    assert "-framerate" in default and "libx264" in default and "-crf" in default
    custom = build_encode_command(
        "ffmpeg", "frames/%08d.png", "out.mov", 24, encode_args=["-c:v", "png"]
    )
    assert "png" in custom and "libx264" not in custom


def test_fit_cover():
    image = np.zeros((20, 40, 3), dtype=np.uint8)
    fitted, box = fit_cover(image, (20, 20))
    assert fitted.shape == (20, 20, 3)
    assert 0 <= box[0] <= box[2] and 0 <= box[1] <= box[3]
    assert box[2] - box[0] == 20 and box[3] - box[1] == 20


@pytest.fixture
def media(tmp_path):
    if not FFMPEG or not FFPROBE:
        pytest.skip("ffmpeg and ffprobe are required")
    rng = np.random.default_rng(27)
    base = rng.integers(0, 256, (160, 240), dtype=np.uint8)
    base = cv2.GaussianBlur(base, (0, 0), 3)
    base = cv2.normalize(base, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    frames = []
    for index in range(36):
        frames.append(
            np.repeat(
                np.roll(base, shift=(index // 3, index % 5), axis=(0, 1))[..., None], 3, axis=2
            )
        )
    frame_dir = tmp_path / "frames"
    frame_dir.mkdir()
    for index, frame in enumerate(frames, 1):
        Image.fromarray(frame).save(frame_dir / f"{index:08d}.png")
    source = tmp_path / "source.mov"
    import subprocess

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
            "-c:v",
            "png",
            "-pix_fmt",
            "rgb24",
            str(source),
        ],
        check=True,
    )
    final = np.roll(frames[-1], shift=(2, 3), axis=(0, 1))
    final_path = tmp_path / "final.png"
    Image.fromarray(final).save(final_path)
    return source, final_path, frames[-1]


@pytest.fixture
def static_tail_media(tmp_path):
    if not FFMPEG or not FFPROBE:
        pytest.skip("ffmpeg and ffprobe are required")
    rng = np.random.default_rng(27)
    base = rng.integers(0, 256, (160, 240), dtype=np.uint8)
    base = cv2.GaussianBlur(base, (0, 0), 3)
    base = cv2.normalize(base, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    frames = []
    for index in range(24):
        frames.append(
            np.repeat(
                np.roll(base, shift=(index // 3, index % 5), axis=(0, 1))[..., None], 3, axis=2
            )
        )
    frames.extend([frames[23]] * 12)
    frame_dir = tmp_path / "frames"
    frame_dir.mkdir()
    for index, frame in enumerate(frames, 1):
        Image.fromarray(frame).save(frame_dir / f"{index:08d}.png")
    source = tmp_path / "source.mov"
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
            "-c:v",
            "png",
            "-pix_fmt",
            "rgb24",
            str(source),
        ],
        check=True,
    )
    final = np.roll(frames[-1], shift=(2, 3), axis=(0, 1))
    final_path = tmp_path / "final.png"
    Image.fromarray(final).save(final_path)
    return source, final_path, frames[-1]


def test_converge_png_and_frames(media, tmp_path):
    source, final, _last = media
    output = tmp_path / "out.mov"
    result = converge(
        source,
        final,
        output,
        frames=12,
        encode_args=["-c:v", "png", "-pix_fmt", "rgb24"],
        ffmpeg=FFMPEG,
        ffprobe=FFPROBE,
    )
    assert result.frame_count == 36 and result.frames == 12
    assert result.error_after_correction < result.error_before
    output_frames = _decode(output, tmp_path / "decoded-output")
    expected, _ = fit_cover(Image.open(final), (240, 160))
    assert np.max(np.abs(output_frames[-1].astype(np.int16) - expected.astype(np.int16))) == 0
    input_frames = _decode(source, tmp_path / "decoded-input")
    for index in range(36 - 12):
        assert np.array_equal(output_frames[index], input_frames[index])
    probe = subprocess.run(
        [
            FFPROBE,
            "-v",
            "error",
            "-count_frames",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,nb_read_frames,r_frame_rate",
            "-of",
            "json",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    stream = json.loads(probe.stdout)["streams"][0]
    assert (stream["width"], stream["height"], stream["nb_read_frames"]) == (240, 160, "36")
    assert Fraction(stream["r_frame_rate"]) == Fraction(24, 1)


def test_converge_monotonic_on_static_tail(static_tail_media, tmp_path):
    source, final, _last = static_tail_media
    output = tmp_path / "out.mov"
    result = converge(
        source,
        final,
        output,
        frames=12,
        encode_args=["-c:v", "png", "-pix_fmt", "rgb24"],
        ffmpeg=FFMPEG,
        ffprobe=FFPROBE,
    )
    assert len(result.errors) == 12
    assert all(
        next_error <= error + 0.05 for error, next_error in zip(result.errors, result.errors[1:])
    ), result.errors
    assert result.errors[0] > result.errors[-1]
    assert result.error_after_correction < result.error_before


def test_converge_output_and_contact_sheet(media, tmp_path):
    source, final, _last = media
    output = tmp_path / "out.mov"
    converge(
        source,
        final,
        output,
        frames=4,
        encode_args=["-c:v", "png", "-pix_fmt", "rgb24"],
        ffmpeg=FFMPEG,
        ffprobe=FFPROBE,
    )
    with pytest.raises(FileExistsError):
        converge(
            source,
            final,
            output,
            frames=4,
            encode_args=["-c:v", "png", "-pix_fmt", "rgb24"],
            ffmpeg=FFMPEG,
            ffprobe=FFPROBE,
        )
    sheet = contact_sheet(
        [("before", source), ("after", output)],
        tmp_path / "sheet.jpg",
        final_image_path=final,
        count=4,
        ffmpeg=FFMPEG,
        ffprobe=FFPROBE,
    )
    assert Image.open(sheet).width >= 5 * 240
    with pytest.raises(FileExistsError):
        contact_sheet([("before", source)], sheet, count=2, ffmpeg=FFMPEG, ffprobe=FFPROBE)


def test_frames_argument_changes_only_expected_suffix(media, tmp_path):
    source, final, _last = media
    input_frames = _decode(source, tmp_path / "input")
    for count, expected_changed in ((4, 3), (12, 11)):
        output = tmp_path / f"out-{count}.mov"
        converge(
            source,
            final,
            output,
            frames=count,
            encode_args=["-c:v", "png", "-pix_fmt", "rgb24"],
            ffmpeg=FFMPEG,
            ffprobe=FFPROBE,
        )
        output_frames = _decode(output, tmp_path / f"output-{count}")
        changed = sum(
            not np.array_equal(before, after) for before, after in zip(input_frames, output_frames)
        )
        assert changed == expected_changed
        assert np.array_equal(output_frames[36 - count], input_frames[36 - count])


def test_default_encoding_preserves_video_shape_and_is_close(media, tmp_path):
    source, final, _last = media
    output = tmp_path / "default.mp4"
    converge(source, final, output, ffmpeg=FFMPEG, ffprobe=FFPROBE)
    frames = _decode(output, tmp_path / "default-frames")
    expected, _ = fit_cover(Image.open(final), (240, 160))
    assert np.abs(frames[-1].astype(np.int16) - expected.astype(np.int16)).mean() <= 3.0
    probe = subprocess.run(
        [
            FFPROBE,
            "-v",
            "error",
            "-count_frames",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,nb_read_frames,r_frame_rate",
            "-of",
            "json",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    stream = json.loads(probe.stdout)["streams"][0]
    assert (stream["width"], stream["height"], stream["nb_read_frames"]) == (240, 160, "36")
    assert Fraction(stream["r_frame_rate"]) == Fraction(24, 1)


def test_invalid_frames(media, tmp_path):
    source, final, _last = media
    with pytest.raises(ValueError):
        converge(source, final, tmp_path / "bad.mov", frames=0, ffmpeg=FFMPEG, ffprobe=FFPROBE)


def _decode(video, directory):
    directory.mkdir()
    subprocess.run(
        [FFMPEG, "-y", "-v", "error", "-i", str(video), str(directory / "%08d.png")], check=True
    )
    return [
        np.asarray(Image.open(path).convert("RGB"), dtype=np.uint8)
        for path in sorted(directory.glob("*.png"))
    ]
