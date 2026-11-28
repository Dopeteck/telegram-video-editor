"""Local visual inspection for short-form editorial planning.

This module uses FFmpeg scene-change timestamps, OpenCV frame sampling/face
localisation, and a local Ollama vision-language model. No external vision API
or remote asset service is used.
"""
from __future__ import annotations

import base64
import json
import re
import subprocess
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from statistics import median
from typing import Any, Sequence


@dataclass(frozen=True)
class FaceObservation:
    timestamp: float
    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class FrameInsight:
    index: int
    timestamp: float
    description: str
    shot_type: str = "unknown"
    visible_subjects: tuple[str, ...] = ()
    composition: str = "unknown"
    visual_interest: str = ""


@dataclass(frozen=True)
class VisualAnalysis:
    scene_changes: tuple[float, ...]
    frames: tuple[FrameInsight, ...]
    faces: tuple[FaceObservation, ...]
    vision_model: str
    vision_status: str
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "scene_changes": [round(value, 3) for value in self.scene_changes],
            "frames": [asdict(frame) for frame in self.frames],
            "faces": [asdict(face) for face in self.faces],
            "vision_model": self.vision_model,
            "vision_status": self.vision_status,
            "warnings": list(self.warnings),
        }

    def planner_context(self) -> dict[str, Any]:
        # Keep the planner prompt compact; the complete observations remain in
        # the manifest for inspection/debugging.
        sampled_faces = self.faces
        if len(sampled_faces) > 18:
            stride = max(1, len(sampled_faces) // 18)
            sampled_faces = sampled_faces[::stride][:18]
        return {
            "scene_changes_seconds": [round(value, 2) for value in self.scene_changes[:24]],
            "sampled_frames": [
                {
                    "timestamp": round(frame.timestamp, 2),
                    "description": frame.description,
                    "shot_type": frame.shot_type,
                    "visible_subjects": list(frame.visible_subjects),
                    "composition": frame.composition,
                    "visual_interest": frame.visual_interest,
                }
                for frame in self.frames
            ],
            "face_positions": [
                {
                    "timestamp": round(face.timestamp, 2),
                    "center_x": round(face.x, 3),
                    "center_y": round(face.y, 3),
                    "relative_size": round(face.width * face.height, 4),
                }
                for face in sampled_faces
            ],
            "vision_status": self.vision_status,
        }


def parse_scene_times(log_text: str, duration: float, *, min_gap: float = 1.25) -> tuple[float, ...]:
    """Parse FFmpeg showinfo timestamps emitted by the scene select filter."""
    raw = [
        float(value)
        for value in re.findall(r"pts_time:([0-9]+(?:[.][0-9]+)?)", log_text)
    ]
    accepted: list[float] = []
    for timestamp in sorted(raw):
        if timestamp <= 0.25 or timestamp >= duration - 0.25:
            continue
        if not accepted or timestamp - accepted[-1] >= min_gap:
            accepted.append(timestamp)
    return tuple(accepted[:30])


def detect_scene_changes(video_path: Path, duration: float, *, threshold: float = 0.32) -> tuple[float, ...]:
    """Detect hard visual changes with FFmpeg's scene score, without splitting media."""
    if duration <= 0:
        return ()
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "info", "-i", str(video_path),
        "-vf", f"select='gt(scene,{threshold:.2f})',showinfo",
        "-an", "-f", "null", "-",
    ]
    result = subprocess.run(
        command, check=False, capture_output=True, text=True,
        timeout=min(180, max(30, int(duration * 0.8))),
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "unknown FFmpeg error")[-800:]
        raise RuntimeError(f"FFmpeg scene detection failed: {detail}")
    return parse_scene_times(result.stderr + "\n" + result.stdout, duration)


def choose_frame_timestamps(
    duration: float,
    scene_changes: Sequence[float],
    *,
    max_frames: int = 6,
) -> tuple[float, ...]:
    """Choose scene-centred frames, filling gaps with evenly spaced samples."""
    if duration <= 0 or max_frames <= 0:
        return ()
    boundaries = [0.0, *sorted(
        value for value in scene_changes if 0 < value < duration
    ), duration]
    candidates: list[float] = []
    for start, end in zip(boundaries, boundaries[1:]):
        midpoint = (start + end) / 2
        if 0.05 < midpoint < duration - 0.05:
            candidates.append(midpoint)
    for index in range(max_frames * 2):
        timestamp = duration * (index + 1) / (max_frames * 2 + 1)
        if 0.05 < timestamp < duration - 0.05:
            candidates.append(timestamp)
    unique: list[float] = []
    for value in sorted(candidates):
        if not unique or value - unique[-1] > 0.35:
            unique.append(value)
    if len(unique) <= max_frames:
        return tuple(round(value, 3) for value in unique)
    selected_indices = [
        round(index * (len(unique) - 1) / (max_frames - 1))
        for index in range(max_frames)
    ] if max_frames > 1 else [len(unique) // 2]
    return tuple(round(unique[index], 3) for index in selected_indices)


def parse_vision_response(content: str, timestamps: Sequence[float]) -> tuple[FrameInsight, ...]:
    """Validate ordered frame descriptions returned by the local VLM."""
    try:
        payload = json.loads(content)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Vision model did not return valid JSON.") from exc
    rows = payload.get("frames") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError("Vision model response must contain a frames list.")
    insights: list[FrameInsight] = []
    for index, timestamp in enumerate(timestamps):
        row = rows[index] if index < len(rows) and isinstance(rows[index], dict) else {}
        subjects = row.get("visible_subjects", [])
        if not isinstance(subjects, list):
            subjects = []
        insights.append(FrameInsight(
            index=index,
            timestamp=float(timestamp),
            description=str(row.get("description") or "No clear visual description returned")[:500],
            shot_type=str(row.get("shot_type") or "unknown")[:40],
            visible_subjects=tuple(str(item)[:80] for item in subjects[:8]),
            composition=str(row.get("composition") or "unknown")[:80],
            visual_interest=str(row.get("visual_interest") or "")[:240],
        ))
    return tuple(insights)


def _extract_samples_and_faces(
    video_path: Path,
    duration: float,
    frame_timestamps: Sequence[float],
    *,
    max_face_samples: int = 120,
) -> tuple[list[dict[str, Any]], tuple[FaceObservation, ...]]:
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError("OpenCV is required for local frame sampling and face tracking.") from exc

    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError(f"OpenCV could not open video: {video_path}")
    cascade = cv2.CascadeClassifier(
        str(Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml")
    )
    if cascade.empty():
        capture.release()
        raise RuntimeError("OpenCV's frontal-face detector could not be loaded.")

    sample_set = {round(value, 2) for value in frame_timestamps}
    face_step = max(0.5, duration / max(1, max_face_samples))
    scan_times = [round(value, 2) for value in _frange(0.15, max(0.15, duration - 0.1), face_step)]
    timestamps = sorted(sample_set.union(scan_times))
    images: list[dict[str, Any]] = []
    faces: list[FaceObservation] = []
    try:
        for timestamp in timestamps:
            capture.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
            ok, frame = capture.read()
            if not ok or frame is None:
                continue
            height, width = frame.shape[:2]
            if width > 640:
                target_height = max(1, round(height * 640 / width))
                frame = cv2.resize(frame, (640, target_height), interpolation=cv2.INTER_AREA)
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            rectangles = cascade.detectMultiScale(
                gray, scaleFactor=1.1, minNeighbors=5,
                minSize=(max(24, int(frame.shape[1] * 0.035)), max(24, int(frame.shape[0] * 0.035))),
            )
            if len(rectangles):
                x, y, face_width, face_height = max(
                    rectangles, key=lambda rect: int(rect[2]) * int(rect[3])
                )
                frame_height, frame_width = frame.shape[:2]
                faces.append(FaceObservation(
                    timestamp=float(timestamp),
                    x=max(0.0, min(1.0, (float(x) + float(face_width) / 2) / frame_width)),
                    y=max(0.0, min(1.0, (float(y) + float(face_height) / 2) / frame_height)),
                    width=max(0.0, min(1.0, float(face_width) / frame_width)),
                    height=max(0.0, min(1.0, float(face_height) / frame_height)),
                ))
            if round(timestamp, 2) in sample_set:
                ok, encoded = cv2.imencode(
                    ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 72]
                )
                if ok:
                    images.append({
                        "timestamp": float(timestamp),
                        "image": base64.b64encode(encoded.tobytes()).decode("ascii"),
                    })
    finally:
        capture.release()
    return images, tuple(faces)


def _frange(start: float, stop: float, step: float):
    value = start
    while value <= stop:
        yield value
        value += step


def describe_frames_with_ollama(
    images: Sequence[dict[str, Any]],
    *,
    model: str,
    ollama_url: str,
    timeout_seconds: int = 300,
) -> tuple[FrameInsight, ...]:
    if not images:
        return ()
    timestamps = [float(item["timestamp"]) for item in images]
    mapping = "\n".join(
        f"Image {index}: timestamp {timestamp:.2f} seconds"
        for index, timestamp in enumerate(timestamps)
    )
    prompt = (
        "You are inspecting sampled frames from a short-form documentary video. "
        "Describe only visible evidence. Do not infer identities, speech, events outside the frame, "
        "or details you cannot see. For every image return a concise description, shot_type "
        "(talking_head, interview, screen_recording, object_closeup, broll, wide_scene, other), "
        "visible_subjects, composition (left, centre, right, wide, close, or mixed), and one "
        "useful visual-interest note. Return JSON only with this shape: "
        '{"frames":[{"description":"...","shot_type":"talking_head","visible_subjects":["person"],'
        '"composition":"centre","visual_interest":"..."}]}. '
        "Keep the frame array in exactly the supplied image order.\n" + mapping
    )
    body = {
        "model": model,
        "stream": False,
        "format": "json",
        "keep_alive": 0,
        "options": {"temperature": 0.1, "num_ctx": 8192, "num_predict": 900},
        "messages": [{
            "role": "user",
            "content": prompt,
            "images": [item["image"] for item in images],
        }],
    }
    request = urllib.request.Request(
        ollama_url.rstrip("/") + "/api/chat",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read(1200).decode("utf-8", errors="replace")
        raise RuntimeError(f"Ollama vision model failed with HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"Cannot reach Ollama vision model at {ollama_url}: {exc}") from exc
    content = result.get("message", {}).get("content", "")
    return parse_vision_response(content, timestamps)


def face_focus_at(
    observations: Sequence[FaceObservation],
    timestamp: float,
    *,
    window_seconds: float = 3.0,
) -> tuple[float, float]:
    """Return a smoothed normalised face centre near a planned emphasis moment."""
    nearby = [
        item for item in observations
        if abs(item.timestamp - timestamp) <= window_seconds
    ]
    if not nearby:
        return 0.5, 0.5
    # Prefer detections near the cue midpoint; median resists a stray false box.
    nearby.sort(key=lambda item: abs(item.timestamp - timestamp))
    nearby = nearby[:7]
    return (
        max(0.0, min(1.0, float(median(item.x for item in nearby)))),
        max(0.0, min(1.0, float(median(item.y for item in nearby)))),
    )


def apply_face_focus(punch_ins: Sequence[Any], observations: Sequence[FaceObservation]) -> tuple[Any, ...]:
    """Attach tracked face positions to zoom cues without changing their timing."""
    focused = []
    for cue in punch_ins:
        x, y = face_focus_at(observations, (float(cue.start) + float(cue.end)) / 2)
        focused.append(replace(cue, focus_x=x, focus_y=y))
    return tuple(focused)


def analyze_video(
    video_path: Path,
    duration: float,
    *,
    model: str = "qwen3-vl:2b",
    ollama_url: str = "http://127.0.0.1:11434",
    max_frames: int = 6,
    timeout_seconds: int = 300,
) -> VisualAnalysis:
    """Inspect scenes, sampled frames and face positions using local tools."""
    warnings: list[str] = []
    try:
        scene_changes = detect_scene_changes(video_path, duration)
    except Exception as exc:
        scene_changes = ()
        warnings.append(str(exc)[:500])
    frame_timestamps = choose_frame_timestamps(duration, scene_changes, max_frames=max_frames)
    images, faces = _extract_samples_and_faces(video_path, duration, frame_timestamps)
    try:
        frames = describe_frames_with_ollama(
            images, model=model, ollama_url=ollama_url, timeout_seconds=timeout_seconds
        )
        vision_status = "completed" if frames else "no_frames"
    except Exception as exc:
        frames = ()
        vision_status = "unavailable"
        warnings.append(str(exc)[:500])
    return VisualAnalysis(
        scene_changes=scene_changes,
        frames=frames,
        faces=faces,
        vision_model=model,
        vision_status=vision_status,
        warnings=tuple(warnings),
    )
