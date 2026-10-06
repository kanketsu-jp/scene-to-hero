"""Apply optional intro blur, hold, and switch-image finishing touches."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import cv2
import numpy as np
from PIL import Image


@dataclass(frozen=True)
class IntroBlur:
    sigma: float
    hold_frames: int = 0
    ramp_frames: int = 12
    curve: str = "smoothstep"


@dataclass
class FinishResult:
    out_path: Path
    input_frame_count: int
    output_frame_count: int
    width: int
    height: int
    fps: float
    hold_frames: int
    switched: bool
    intro_blur_frames: int
    crop_box: tuple[int, int, int, int] | None


def _tool(name: str, supplied: str | None, env_name: str) -> str:
    value = supplied or os.environ.get(env_name) or shutil.which(name)
    if not value:
        raise FileNotFoundError(f"{name} was not found")
    return value


def _run(runner, command: list[str]):
    return runner(command, check=True, capture_output=True, text=True)


def focus_weight(index: int, hold: int, ramp: int, curve: str) -> float:
    if curve not in {"smoothstep", "linear"}:
        raise ValueError("curve must be smoothstep or linear")
    if index < hold:
        return 1.0
    if index >= hold + ramp:
        return 0.0
    u = (index - hold) / ramp
    if curve == "linear":
        return 1.0 - u
    return 1.0 - u * u * (3.0 - 2.0 * u)


def fit_cover(image, size) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    width, height = int(size[0]), int(size[1])
    if isinstance(image, Image.Image):
        pil = image.convert("RGB")
    else:
        array = np.asarray(image)
        if array.ndim == 2:
            array = np.repeat(array[..., None], 3, axis=2)
        if array.shape[-1] == 4:
            array = array[..., :3]
        pil = Image.fromarray(np.asarray(array, dtype=np.uint8), "RGB")
    scale = max(width / pil.width, height / pil.height)
    resized_size = (max(width, round(pil.width * scale)), max(height, round(pil.height * scale)))
    resized = pil.resize(resized_size, Image.Resampling.LANCZOS)
    left = (resized.width - width) // 2
    top = (resized.height - height) // 2
    box = (left, top, left + width, top + height)
    return np.asarray(resized.crop(box), dtype=np.uint8).copy(), box


def blur_frame(frame: np.ndarray, sigma: float) -> np.ndarray:
    source = np.asarray(frame, dtype=np.uint8)
    if sigma <= 0:
        return source.copy()
    values = source.astype(np.float64) / 255.0
    linear = np.where(
        values <= 0.04045,
        values / 12.92,
        ((values + 0.055) / 1.055) ** 2.4,
    )
    padding = int(np.ceil(4 * sigma)) + 2
    padded = cv2.copyMakeBorder(linear, padding, padding, padding, padding, cv2.BORDER_REFLECT)
    blurred = cv2.GaussianBlur(padded, (0, 0), sigmaX=float(sigma), borderType=cv2.BORDER_REFLECT)
    height, width = source.shape[:2]
    blurred = blurred[padding : padding + height, padding : padding + width]
    encoded = np.where(
        blurred <= 0.0031308,
        blurred * 12.92,
        1.055 * np.maximum(blurred, 0) ** (1 / 2.4) - 0.055,
    )
    return np.clip(np.rint(encoded * 255.0), 0, 255).astype(np.uint8)


def build_decode_command(ffmpeg, video, out_pattern) -> list[str]:
    return [
        ffmpeg,
        "-y",
        "-v",
        "error",
        "-i",
        str(video),
        "-an",
        "-fps_mode",
        "passthrough",
        str(out_pattern),
    ]


def build_encode_command(
    ffmpeg, in_pattern, out_path, fps, *, crf=12, encode_args=None
) -> list[str]:
    args = (
        list(encode_args)
        if encode_args is not None
        else ["-c:v", "libx264", "-crf", str(crf), "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
    )
    return [
        ffmpeg,
        "-y",
        "-v",
        "error",
        "-framerate",
        str(fps),
        "-i",
        str(in_pattern),
        "-an",
        *args,
        str(out_path),
    ]


def _probe(video, runner, ffprobe):
    command = [
        ffprobe,
        "-v",
        "error",
        "-count_frames",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,r_frame_rate,nb_read_frames",
        "-of",
        "json",
        str(video),
    ]
    data = json.loads(_run(runner, command).stdout)["streams"][0]
    return (
        int(data["width"]),
        int(data["height"]),
        float(Fraction(data["r_frame_rate"])),
        int(data["nb_read_frames"]),
    )


def finish(
    video,
    out,
    *,
    hold_frames: int = 0,
    switch_image=None,
    intro_blur: IntroBlur | None = None,
    crf: int = 12,
    encode_args: list[str] | None = None,
    overwrite: bool = False,
    runner=subprocess.run,
    ffmpeg: str | None = None,
    ffprobe: str | None = None,
) -> FinishResult:
    out_path = Path(out)
    if out_path.exists() and not overwrite:
        raise FileExistsError(str(out_path))
    if hold_frames < 0:
        raise ValueError("hold_frames must be non-negative")
    if switch_image is not None and hold_frames < 1:
        raise ValueError("a switch image needs at least one appended frame")
    if switch_image is not None and not Path(switch_image).exists():
        raise FileNotFoundError(str(switch_image))
    if intro_blur is not None:
        if intro_blur.sigma <= 0:
            raise ValueError("intro blur sigma must be positive")
        if intro_blur.hold_frames < 0:
            raise ValueError("intro blur hold_frames must be non-negative")
        if intro_blur.ramp_frames < 1:
            raise ValueError("intro blur ramp_frames must be positive")
        if intro_blur.curve not in {"smoothstep", "linear"}:
            raise ValueError("curve must be smoothstep or linear")

    ffmpeg = _tool("ffmpeg", ffmpeg, "SCENE_TO_HERO_FFMPEG")
    ffprobe = _tool("ffprobe", ffprobe, "SCENE_TO_HERO_FFPROBE")
    width, height, fps, frame_count = _probe(video, runner, ffprobe)
    if width % 2 or height % 2:
        raise ValueError("video width and height must be even")
    if intro_blur is not None and intro_blur.hold_frames + intro_blur.ramp_frames > frame_count:
        raise ValueError("intro focus pull must fit inside the source video")

    crop_box = None
    switch_frame = None
    if switch_image is not None:
        switch_frame, crop_box = fit_cover(Image.open(switch_image), (width, height))
    intro_blur_frames = 0
    with tempfile.TemporaryDirectory() as temp:
        temp_path = Path(temp)
        input_pattern = temp_path / "%08d.png"
        decoded_path = temp_path / "decoded"
        decoded_path.mkdir()
        decoded_pattern = decoded_path / "%08d.png"
        _run(runner, build_decode_command(ffmpeg, video, decoded_pattern))
        decoded = sorted(decoded_path.glob("*.png"))
        if len(decoded) != frame_count:
            raise RuntimeError("decoded frame count does not match ffprobe")
        last_frame = None
        for index, source_path in enumerate(decoded):
            destination = temp_path / f"{index + 1:08d}.png"
            if intro_blur is None:
                shutil.copyfile(source_path, destination)
                last_frame = np.asarray(
                    Image.open(source_path).convert("RGB"), dtype=np.uint8
                ).copy()
                continue
            weight = focus_weight(
                index,
                intro_blur.hold_frames,
                intro_blur.ramp_frames,
                intro_blur.curve,
            )
            if weight == 0.0:
                shutil.copyfile(source_path, destination)
                last_frame = np.asarray(
                    Image.open(source_path).convert("RGB"), dtype=np.uint8
                ).copy()
            else:
                last_frame = np.asarray(
                    Image.open(source_path).convert("RGB"), dtype=np.uint8
                ).copy()
                last_frame = blur_frame(last_frame, intro_blur.sigma * weight)
                Image.fromarray(last_frame, "RGB").save(destination)
                intro_blur_frames += 1
        held = switch_frame if switch_image is not None else last_frame
        for _ in range(hold_frames):
            Image.fromarray(held, "RGB").save(temp_path / f"{frame_count + _ + 1:08d}.png")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        _run(
            runner,
            build_encode_command(
                ffmpeg,
                input_pattern,
                out_path,
                fps,
                crf=crf,
                encode_args=encode_args,
            ),
        )
    output_width, output_height, _output_fps, output_count = _probe(out_path, runner, ffprobe)
    if output_count != frame_count + hold_frames:
        raise RuntimeError("encoded frame count does not match expected count")
    return FinishResult(
        out_path,
        frame_count,
        output_count,
        output_width,
        output_height,
        fps,
        hold_frames,
        switch_image is not None,
        intro_blur_frames,
        crop_box,
    )
