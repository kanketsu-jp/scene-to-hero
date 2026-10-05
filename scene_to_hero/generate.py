from __future__ import annotations

import base64
import json
import math
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .budget import Budget, BudgetExceeded, billed_seconds
from .fal_client import FalClient

ENDPOINT = "minimax/h3-max/image-to-video"
DEFAULT_PRICE_PER_SECOND = 0.096
DURATION_RANGE = (0.92, 15.0)


@dataclass
class GenerateParams:
    start_image: Path
    prompt: str
    output_dir: Path
    name: str
    duration: float = 4.2
    seed: int | None = None
    resolution: str = "1080P"
    prompt_expansion_mode: str = "disabled"
    price_per_second: float = DEFAULT_PRICE_PER_SECOND
    fps: int = 24
    replace_last_frame: bool = True
    hold_frames: int = 0


def build_prompt(subject: str, scene: str, *, has_marking: bool = False) -> str:
    parts = [
        "One continuous shot, locked-off camera, starting exactly from this still image and holding perfectly still for the first 0.3 seconds.",
        subject,
        scene,
        ("Each subject keeps the printed marking of the starting image with exactly the same shape, size and characters, never melting, flickering or rewriting."
         if has_marking else "Plain unlabeled objects, no text, no logos."),
        "The main subjects always keep their fronts facing the camera: they never rotate around their vertical axis, never tilt more than 15 degrees, never change apparent size by more than 1.3 times, and move mainly up and down along gentle arcs; only extra out-of-focus elements are allowed to move widely.",
        "Then the subjects lift off into the air one by one, rising weightlessly as gravity fades, and drift upward and outward in different directions. Finally one extra out-of-focus element passes extremely close to the camera and sweeps across the frame.",
        "No lens flares, no light streaks.",
    ]
    return " ".join(part.strip() for part in parts if part.strip())


def estimate(params: GenerateParams) -> dict:
    seconds = billed_seconds(params.duration)
    est = round(seconds * params.price_per_second, 6)
    return {"endpoint": ENDPOINT, "billed_seconds": seconds, "unit_price_usd": params.price_per_second,
            "est_usd": est, "max_attempt_usd": round(est * 2, 6)}


def build_payload(params, image_url: str) -> dict:
    payload = {"prompt": params.prompt, "image_url": image_url, "resolution": params.resolution,
               "prompt_expansion_mode": params.prompt_expansion_mode, "duration": params.duration}
    if params.seed is not None:
        payload["seed"] = params.seed
    return payload


def build_reverse_command(ffmpeg, src, dst, fps=24, crf=10) -> list[str]:
    return [str(ffmpeg), "-y", "-v", "error", "-i", str(src), "-vf", "reverse", "-r", str(fps), "-an",
            "-c:v", "libx264", "-crf", str(crf), "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(dst)]


def build_replace_last_frame_command(ffmpeg, reversed_clip, still, dst, frame_count, hold_frames, width, height, fps) -> list[str]:
    keep = max(0, int(hold_frames))
    total = int(frame_count) + keep
    filter_complex = (f"[0:v]trim=end_frame={int(frame_count)-1},setpts=PTS-STARTPTS[r];"
                      f"[1:v]scale={int(width)}:{int(height)},trim=end_frame={keep + 1},setpts=PTS-STARTPTS[k];"
                      "[r][k]concat=n=2:v=1:a=0,format=yuv420p[v]")
    return [str(ffmpeg), "-y", "-v", "error", "-i", str(reversed_clip), "-framerate", str(fps), "-loop", "1",
            "-i", str(still), "-filter_complex", filter_complex, "-map", "[v]", "-frames:v", str(total),
            "-an", "-c:v", "libx264", "-crf", "16",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(dst)]


def _which(name, env_name):
    return os.environ.get(env_name) or __import__("shutil").which(name) or name


def probe_video(path):
    command = [_which("ffprobe", "SCENE_TO_HERO_FFPROBE"), "-v", "error", "-select_streams", "v:0",
               "-count_frames", "-show_entries", "stream=width,height,r_frame_rate,nb_read_frames,nb_frames,codec_name,pix_fmt",
               "-show_entries", "format=duration,size", "-of", "json", str(path)]
    completed = subprocess.run(command, check=True, capture_output=True, text=True)
    data = json.loads(completed.stdout)
    stream = data["streams"][0]
    numerator, denominator = (int(value) for value in stream.get("r_frame_rate", "0/1").split("/"))
    count = stream.get("nb_read_frames") or stream.get("nb_frames")
    return {"width": int(stream["width"]), "height": int(stream["height"]), "fps": numerator / denominator,
            "frame_count": int(count), "duration_seconds": float(data.get("format", {}).get("duration", 0))}


def _data_uri(path: Path) -> str:
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def generate(params: GenerateParams, budget: Budget, client: FalClient, *, dry_run=False,
             runner=subprocess.run, probe=probe_video) -> dict:
    if dry_run:
        return estimate(params)
    if not DURATION_RANGE[0] <= params.duration <= DURATION_RANGE[1]:
        raise ValueError(f"duration must be between {DURATION_RANGE[0]} and {DURATION_RANGE[1]}")
    output_dir = Path(params.output_dir)
    clips = output_dir / "clips"
    raw = clips / f"{params.name}_raw.mp4"
    reversed_clip = output_dir / f"{params.name}_reversed.mp4"
    final = output_dir / f"{params.name}.mp4"
    if reversed_clip.exists() or final.exists():
        raise FileExistsError("output file already exists")
    seconds = billed_seconds(params.duration)
    image_url = _data_uri(params.start_image)
    request_id = None
    last_error = None
    for attempt in (1, 2):
        allowed, cumulative, est = budget.reserve("generate", ENDPOINT, params.price_per_second, "second", seconds, attempt)
        if not allowed:
            reason = f"budget guard: {cumulative}+{est} > {getattr(budget, 'max_usd', 'limit')}"
            budget.record_result("generate", ENDPOINT, attempt, None, False, error=reason, api_called=False)
            raise BudgetExceeded(reason)
        try:
            if attempt == 2:
                image_url = client.upload_file(params.start_image)
            result = client.run(ENDPOINT, build_payload(params, image_url), raw)
            request_id = result.get("request_id")
            budget.record_result("generate", ENDPOINT, attempt, request_id, True,
                                 output_path=str(raw.relative_to(output_dir)), api_called=True)
            break
        except Exception as exc:
            last_error = exc
            budget.record_result("generate", ENDPOINT, attempt, request_id, False, error=exc, api_called=True)
            if attempt == 2:
                raise
    if not raw.exists():
        raise RuntimeError("generation did not produce a clip") from last_error
    ffmpeg = _which("ffmpeg", "SCENE_TO_HERO_FFMPEG")
    reverse_command = build_reverse_command(ffmpeg, raw, reversed_clip, params.fps)
    runner(reverse_command, check=True)
    if params.replace_last_frame:
        info = probe(reversed_clip)
        width = math.floor(info["width"] / 2) * 2
        height = math.floor(info["height"] / 2) * 2
        command = build_replace_last_frame_command(ffmpeg, reversed_clip, params.start_image, final,
                                                    info["frame_count"], params.hold_frames, width, height, params.fps)
        runner(command, check=True)
    else:
        if final.exists():
            raise FileExistsError(final)
        os.replace(reversed_clip, final)
    return {"path": str(final), "request_id": request_id, "estimate": estimate(params)}
