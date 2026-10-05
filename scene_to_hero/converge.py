"""Converge the end of a video toward a chosen final image."""

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
from PIL import Image, ImageDraw, ImageFont


@dataclass
class ConvergeResult:
    out_path: Path
    frame_count: int
    width: int
    height: int
    fps: float
    frames: int
    flow_method: str
    flow_replaced_percent: float
    crop_box: tuple[int, int, int, int]
    errors: list[float]
    error_before: float
    error_after_correction: float
    psnr_before: float
    psnr_after_correction: float


def _tool(name: str, supplied: str | None, env_name: str) -> str:
    value = supplied or os.environ.get(env_name) or shutil.which(name)
    if not value:
        raise FileNotFoundError(f"{name} was not found")
    return value


def _run(runner, command: list[str]):
    return runner(command, check=True, capture_output=True, text=True)


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


def compute_flow(
    target, last, *, preset="medium", failure_px=30.0
) -> tuple[np.ndarray, str, float]:
    target = np.asarray(target, dtype=np.uint8)
    last = np.asarray(last, dtype=np.uint8)
    target_gray = cv2.cvtColor(target, cv2.COLOR_RGB2GRAY)
    last_gray = cv2.cvtColor(last, cv2.COLOR_RGB2GRAY)
    names = {
        "ultrafast": "DISOPTICAL_FLOW_PRESET_ULTRAFAST",
        "fast": "DISOPTICAL_FLOW_PRESET_FAST",
        "medium": "DISOPTICAL_FLOW_PRESET_MEDIUM",
    }
    if preset not in names:
        raise ValueError("preset must be ultrafast, fast, or medium")
    if hasattr(cv2, "DISOpticalFlow_create") and hasattr(cv2, names[preset]):
        dis = cv2.DISOpticalFlow_create(getattr(cv2, names[preset]))
        dis.setVariationalRefinementIterations(2)
        flow = dis.calc(target_gray, last_gray, None)
        method = "DIS"
    else:
        flow = cv2.calcOpticalFlowFarneback(target_gray, last_gray, None, 0.5, 5, 25, 5, 7, 1.5, 0)
        method = "Farneback"
    flow = np.asarray(flow, dtype=np.float32)
    magnitude = np.sqrt(np.sum(flow * flow, axis=2))
    failed = magnitude > float(failure_px)
    replaced = float(np.count_nonzero(failed)) / failed.size * 100.0
    if np.any(failed):
        x = cv2.medianBlur(flow[..., 0], 5)
        y = cv2.medianBlur(flow[..., 1], 5)
        flow[..., 0][failed] = x[failed]
        flow[..., 1][failed] = y[failed]
    return flow, method, replaced


def correct_frames(frames_rgb, target, flow, count) -> list[np.ndarray]:
    n = len(frames_rgb)
    if count < 0 or count > n:
        raise ValueError("frames must be between 0 and the frame count")
    result = [np.asarray(frame, dtype=np.uint8).copy() for frame in frames_rgb]
    start = n - count
    height, width = target.shape[:2]
    xx, yy = np.meshgrid(np.arange(width, dtype=np.float32), np.arange(height, dtype=np.float32))
    for i in range(start, n):
        t = (i - start) / max(1, count - 1)
        weight = t * t * (3.0 - 2.0 * t)
        if weight == 0:
            continue
        result[i] = cv2.remap(
            result[i],
            xx + flow[..., 0] * weight,
            yy + flow[..., 1] * weight,
            cv2.INTER_CUBIC,
            borderMode=cv2.BORDER_REPLICATE,
        )
    return result


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
    frames = int(data["nb_read_frames"])
    fps = float(Fraction(data["r_frame_rate"]))
    return int(data["width"]), int(data["height"]), fps, frames


def _mean_error(a, b) -> float:
    return float(np.abs(a.astype(np.int16) - b.astype(np.int16)).mean())


def _psnr(a, b) -> float:
    mse = float(np.mean((a.astype(np.float64) - b.astype(np.float64)) ** 2))
    return float("inf") if mse == 0 else float(10 * np.log10((255.0 * 255.0) / mse))


def converge(
    video_path,
    final_image_path,
    out_path,
    *,
    frames: int = 12,
    preset: str = "medium",
    flow_failure_px: float = 30.0,
    crf: int = 12,
    encode_args: list[str] | None = None,
    overwrite: bool = False,
    runner=subprocess.run,
    ffmpeg: str | None = None,
    ffprobe: str | None = None,
) -> ConvergeResult:
    out_path = Path(out_path)
    if out_path.exists() and not overwrite:
        raise FileExistsError(str(out_path))
    ffmpeg = _tool("ffmpeg", ffmpeg, "SCENE_TO_HERO_FFMPEG")
    ffprobe = _tool("ffprobe", ffprobe, "SCENE_TO_HERO_FFPROBE")
    width, height, fps, frame_count = _probe(video_path, runner, ffprobe)
    if width % 2 or height % 2:
        raise ValueError("video width and height must be even")
    if frames < 1 or frames > frame_count:
        raise ValueError("frames must be between 1 and the frame count")
    with tempfile.TemporaryDirectory() as temp:
        temp_path = Path(temp)
        input_pattern = temp_path / "%08d.png"
        _run(runner, build_decode_command(ffmpeg, video_path, input_pattern))
        decoded = sorted(temp_path.glob("*.png"))
        if len(decoded) != frame_count:
            raise ValueError("decoded frame count does not match ffprobe")
        source = [
            np.asarray(Image.open(path).convert("RGB"), dtype=np.uint8).copy() for path in decoded
        ]
        target, crop_box = fit_cover(Image.open(final_image_path), (width, height))
        flow, method, replaced = compute_flow(
            target, source[-1], preset=preset, failure_px=flow_failure_px
        )
        corrected = correct_frames(source, target, flow, frames)
        start = frame_count - frames
        errors = [_mean_error(corrected[i], target) for i in range(start, frame_count)]
        error_before = _mean_error(source[-1], target)
        error_after = errors[-1]
        psnr_before = _psnr(source[-1], target)
        psnr_after = _psnr(corrected[-1], target)
        corrected[-1] = target.copy()
        for index, frame in enumerate(corrected, 1):
            Image.fromarray(frame, "RGB").save(temp_path / f"{index:08d}.png")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        _run(
            runner,
            build_encode_command(
                ffmpeg, input_pattern, out_path, fps, crf=crf, encode_args=encode_args
            ),
        )
    return ConvergeResult(
        out_path,
        frame_count,
        width,
        height,
        fps,
        frames,
        method,
        replaced,
        crop_box,
        errors,
        error_before,
        error_after,
        psnr_before,
        psnr_after,
    )


def contact_sheet(
    videos,
    out_path,
    *,
    final_image_path=None,
    count: int = 12,
    tile_width: int = 240,
    overwrite: bool = False,
    runner=subprocess.run,
    ffmpeg: str | None = None,
    ffprobe: str | None = None,
) -> Path:
    out_path = Path(out_path)
    if out_path.exists() and not overwrite:
        raise FileExistsError(str(out_path))
    ffmpeg = _tool("ffmpeg", ffmpeg, "SCENE_TO_HERO_FFMPEG")
    ffprobe = _tool("ffprobe", ffprobe, "SCENE_TO_HERO_FFPROBE")
    rows = []
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        for row_index, (label, video) in enumerate(videos):
            width, height, _fps, total = _probe(video, runner, ffprobe)
            take = min(count, total)
            pattern = root / f"row{row_index}" / "%08d.png"
            pattern.parent.mkdir()
            _run(runner, build_decode_command(ffmpeg, video, pattern))
            paths = sorted(pattern.parent.glob("*.png"))[-take:]
            images = [Image.open(path).convert("RGB") for path in paths]
            if final_image_path is not None:
                fitted, _ = fit_cover(Image.open(final_image_path), (width, height))
                images.insert(0, Image.fromarray(fitted, "RGB"))
            scale = tile_width / width
            tile_h = max(1, round(height * scale))
            tiles = []
            for offset, image in enumerate(images):
                tile = image.resize((tile_width, tile_h), Image.Resampling.LANCZOS)
                draw = ImageDraw.Draw(tile)
                number = (
                    "final"
                    if final_image_path is not None and offset == 0
                    else str(total - take + offset - (1 if final_image_path is not None else 0))
                )
                draw.rectangle((0, 0, max(34, len(number) * 8 + 4), 14), fill="white")
                draw.text((2, 1), number, fill="black", font=ImageFont.load_default())
                tiles.append(tile)
            rows.append((str(label), tiles, tile_h))
        label_width = max([len(label) for label, _tiles, _h in rows] or [1]) * 8 + 12
        row_heights = [h + 18 for _label, _tiles, h in rows]
        canvas = Image.new(
            "RGB",
            (
                label_width + max([len(t) for _l, t, _h in rows] or [1]) * tile_width,
                sum(row_heights),
            ),
            "white",
        )
        draw = ImageDraw.Draw(canvas)
        y = 0
        for label, tiles, tile_h in rows:
            draw.text((2, y + 2), label, fill="black", font=ImageFont.load_default())
            for col, tile in enumerate(tiles):
                canvas.paste(tile, (label_width + col * tile_width, y + 18))
            y += tile_h + 18
        out_path.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(out_path, quality=95)
    return out_path
