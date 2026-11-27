"""R2 ingestion, local AI planning, editorial rendering, and FFmpeg output."""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import tempfile
import textwrap
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .editorial import (
    CalloutCue,
    PunchInCue,
    detect_burned_in_captions,
    validate_editorial_cues,
)

SUPPORTED_VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm"}
SYSTEM_PROMPT = """You are a senior short-form documentary editor, not a caption generator.
Create a restrained but noticeable editorial plan for the supplied finished clip.
Make useful decisions even when the B-roll catalogue is empty.
Use the transcript timestamps to choose the strongest moments; never invent facts.
Return JSON only:
{
  "title": "short editorial title",
  "broll_cues": [
    {"start": 12.0, "end": 17.0, "asset_id": "exact-catalogue-id", "reason": "why this shot supports the point"}
  ],
  "punch_ins": [
    {"start": 21.0, "end": 25.0, "zoom": 1.10, "reason": "specific moment of emphasis"}
  ],
  "callouts": [
    {"start": 32.0, "end": 35.0, "text": "exact words from transcript", "reason": "why this phrase deserves visual emphasis"}
  ]
}
Rules:
- B-roll: use only supplied catalogue IDs; never invent an asset. At most 6 cues, 2–8 seconds each, non-overlapping. Do not add generic filler.
- Punch-ins: choose at most 4 genuinely useful emphasis moments, 2–8 seconds each, zoom 1.05–1.18, with no overlap. Avoid random zooming.
- Callouts: choose at most 3 high-value facts, numbers, or concepts. Text must be an exact contiguous phrase from the transcript, at most 34 characters, one line, 1.5–5 seconds, and non-overlapping. Do not paraphrase, add claims, or repeat ordinary dialogue.
- A callout is a designed graphic emphasis, not a subtitle. Place it in the upper safe area; the renderer handles placement.
- Do not propose subtitles. Existing burned-in subtitles are preserved; new captions are added only after a separate detection check.
- If an edit does not improve clarity or emphasis, leave its cue list empty.
- Keep all cue times inside the clip and do not overlap cues of the same type."""


@dataclass(frozen=True)
class TranscriptSegment:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class BrollAsset:
    asset_id: str
    key: str
    description: str
    tags: tuple[str, ...]
    source_url: str
    license: str
    rights_verified: bool


@dataclass(frozen=True)
class BrollCue:
    start: float
    end: float
    asset_id: str
    reason: str


@dataclass(frozen=True)
class EditPlan:
    title: str
    broll_cues: tuple[BrollCue, ...]
    punch_ins: tuple[PunchInCue, ...] = ()
    callouts: tuple[CalloutCue, ...] = ()
    rejected_cues: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "broll_cues": [asdict(cue) for cue in self.broll_cues],
            "punch_ins": [asdict(cue) for cue in self.punch_ins],
            "callouts": [asdict(cue) for cue in self.callouts],
            "rejected_cues": list(self.rejected_cues),
        }


def parse_catalog(payload: dict[str, Any]) -> list[BrollAsset]:
    rows = payload.get("assets", []) if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError("B-roll catalogue must contain an 'assets' list.")
    assets: list[BrollAsset] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or row.get("rights_verified") is not True:
            continue
        asset_id = str(row.get("id") or "").strip()
        key = str(row.get("key") or "").strip()
        description = str(row.get("description") or "").strip()
        source_url = str(row.get("source_url") or "").strip()
        licence = str(row.get("license") or "").strip()
        parts = key.split("/")
        suffix = "." + key.rsplit(".", 1)[-1].lower() if "." in key else ""
        if (
            not asset_id or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", asset_id)
            or asset_id in seen
            or not key.startswith("broll/") or key.startswith("/")
            or ".." in parts or suffix not in SUPPORTED_VIDEO_SUFFIXES
            or not description or not source_url.startswith(("https://", "http://"))
            or not licence
        ):
            continue
        raw_tags = row.get("tags", [])
        tags = tuple(str(tag).strip().lower() for tag in raw_tags if str(tag).strip()) if isinstance(raw_tags, list) else ()
        assets.append(BrollAsset(asset_id, key, description, tags, source_url, licence, True))
        seen.add(asset_id)
    return assets


def validate_edit_plan(
    payload: dict[str, Any],
    duration: float,
    assets: list[BrollAsset],
    *,
    transcript: list[TranscriptSegment] | None = None,
    max_cues: int = 6,
    min_cue_seconds: float = 2.0,
    max_cue_seconds: float = 8.0,
) -> EditPlan:
    if not isinstance(payload, dict):
        raise ValueError("AI edit plan must be a JSON object.")
    title = str(payload.get("title") or "Edited clip").strip()[:100] or "Edited clip"
    rows = payload.get("broll_cues", [])
    if not isinstance(rows, list):
        raise ValueError("AI edit plan 'broll_cues' must be a list.")
    allowed = {asset.asset_id for asset in assets if asset.rights_verified}
    accepted: list[BrollCue] = []
    rejected: list[str] = []
    for index, row in enumerate(rows):
        if len(accepted) >= max_cues:
            rejected.append(f"cue {index + 1}: over the {max_cues}-cue limit")
            continue
        if not isinstance(row, dict):
            rejected.append(f"cue {index + 1}: malformed row")
            continue
        try:
            start, end = float(row["start"]), float(row["end"])
            asset_id = str(row["asset_id"])
        except (KeyError, TypeError, ValueError):
            rejected.append(f"cue {index + 1}: missing or invalid fields")
            continue
        if asset_id not in allowed:
            rejected.append(f"cue {index + 1}: asset is not rights-cleared in the catalogue")
            continue
        if start < 0 or end <= start or end > duration:
            rejected.append(f"cue {index + 1}: timestamps are outside the clip")
            continue
        if not min_cue_seconds <= end - start <= max_cue_seconds:
            rejected.append(f"cue {index + 1}: cutaway length is outside allowed limits")
            continue
        accepted.append(BrollCue(start, end, asset_id, str(row.get("reason") or "Contextual cutaway")[:180]))
    accepted.sort(key=lambda cue: cue.start)
    non_overlapping: list[BrollCue] = []
    for cue in accepted:
        if non_overlapping and cue.start < non_overlapping[-1].end:
            rejected.append(f"{cue.asset_id}: overlapping cue rejected")
        else:
            non_overlapping.append(cue)

    transcript_text = " ".join(segment.text for segment in (transcript or []))
    punch_ins, callouts, editorial_rejections = validate_editorial_cues(
        payload, duration, transcript_text
    )
    rejected.extend(editorial_rejections)
    return EditPlan(title, tuple(non_overlapping), punch_ins, callouts, tuple(rejected))


def load_broll_catalog(client, bucket: str, catalog_key: str) -> list[BrollAsset]:
    """Load the user's cleared B-roll catalogue; a missing key means no B-roll yet."""
    try:
        response = client.get_object(Bucket=bucket, Key=catalog_key)
    except Exception as exc:
        error = getattr(exc, "response", {}) or {}
        code = str(((error.get("Error") or {}).get("Code")) or "")
        if code in {"NoSuchKey", "404", "NotFound"}:
            return []
        raise RuntimeError(f"Could not read B-roll catalogue '{catalog_key}': {exc}") from exc

    try:
        payload = json.loads(response["Body"].read().decode("utf-8"))
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"B-roll catalogue '{catalog_key}' is not valid UTF-8 JSON.") from exc
    return parse_catalog(payload)


def transcribe_video(video_path: Path, model_size: str = "small") -> tuple[list[TranscriptSegment], str | None]:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError("faster-whisper is not installed; install requirements.txt.") from exc
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segments, info = model.transcribe(str(video_path), vad_filter=True, beam_size=3)
    transcript = [
        TranscriptSegment(max(0.0, float(s.start)), max(float(s.start), float(s.end)), str(s.text or "").strip())
        for s in segments if str(s.text or "").strip()
    ]
    if not transcript:
        raise RuntimeError("Whisper found no usable speech in the finished clip.")
    return transcript, getattr(info, "language", None)


def _timestamp(seconds: float) -> str:
    minutes, remainder = divmod(max(0.0, seconds), 60.0)
    return f"{int(minutes):02d}:{remainder:05.2f}"


def create_edit_plan(
    transcript: list[TranscriptSegment],
    duration: float,
    assets: list[BrollAsset],
    *,
    model: str = "qwen3:4b-instruct-2507-q4_K_M",
    ollama_url: str = "http://127.0.0.1:11434",
    timeout_seconds: int = 300,
) -> EditPlan:
    if not transcript:
        raise ValueError("Cannot plan an edit without transcript segments.")
    transcript_text = "\n".join(
        f"[{_timestamp(row.start)} - {_timestamp(row.end)}] {row.text}" for row in transcript
    )
    catalogue = [
        {"id": a.asset_id, "description": a.description, "tags": list(a.tags)}
        for a in assets if a.rights_verified
    ]
    prompt = {
        "clip_duration_seconds": round(duration, 2),
        "transcript": transcript_text[:14000],
        "available_rights_cleared_broll": catalogue,
        "instruction": (
            "Return editorial decisions for this clip. Even with an empty B-roll list, "
            "consider restrained punch-ins and exact-transcript callouts. Do not invent facts."
        ),
    }
    body = {
        "model": model, "stream": False, "format": "json",
        "options": {"temperature": 0.15, "num_ctx": 8192, "num_predict": 1100},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ],
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
        raise RuntimeError(f"Ollama failed with HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"Cannot reach Ollama at {ollama_url}: {exc}") from exc
    try:
        payload = json.loads(result.get("message", {}).get("content", ""))
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Ollama did not return a valid JSON edit plan.") from exc
    return validate_edit_plan(payload, duration, assets, transcript=transcript)


def probe_video(path: Path) -> dict[str, Any]:
    command = ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    data = json.loads(result.stdout)
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    if not video:
        raise ValueError(f"No video stream found in {path}.")
    return {
        "width": int(video["width"]), "height": int(video["height"]),
        "duration": float(data.get("format", {}).get("duration") or 0),
        "has_audio": any(s.get("codec_type") == "audio" for s in data.get("streams", [])),
    }


def resolve_caption_policy(mode: str, video_path: Path, duration: float) -> tuple[bool, str]:
    """Decide whether to add captions, failing safe when existing text is uncertain."""
    if mode not in {"auto", "preserve", "force"}:
        raise ValueError("caption_mode must be one of: auto, preserve, force.")
    if mode == "preserve":
        return False, "preserve_source_captions"
    if mode == "force":
        return True, "forced_new_captions"
    detected = detect_burned_in_captions(video_path, duration)
    if detected is True:
        return False, "existing_burned_in_captions_detected"
    if detected is False:
        return True, "no_existing_burned_in_captions_detected"
    return False, "caption_detection_unavailable_preserved_source"


def _ass_timestamp(seconds: float) -> str:
    whole = max(0, int(seconds * 100))
    hours, remainder = divmod(whole, 360000)
    minutes, remainder = divmod(remainder, 6000)
    secs, centiseconds = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{centiseconds:02d}"


def write_ass_captions(transcript: list[TranscriptSegment], destination: Path, width: int, height: int) -> None:
    font_size = max(34, min(64, int(height * 0.032)))
    margin_h = max(48, int(width * 0.075))
    margin_v = max(96, int(height * 0.13))
    max_chars = max(16, int((width - 2 * margin_h) / (font_size * 0.52)))
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {width}", f"PlayResY: {height}", "WrapStyle: 0", "",
        "[V4+ Styles]",
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding",
        f"Style: Default,Liberation Sans,{font_size},&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,-1,0,1,3,1,2,{margin_h},{margin_h},{margin_v},1",
        "", "[Events]", "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text",
    ]
    for segment in transcript:
        if segment.end <= segment.start:
            continue
        caption = str(segment.text).replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ").replace("\r", " ")
        if caption:
            wrapped = textwrap.fill(
                caption, width=max_chars, break_long_words=True, break_on_hyphens=False
            ).replace("\n", r"\N")
            lines.append(
                f"Dialogue: 0,{_ass_timestamp(segment.start)},{_ass_timestamp(segment.end)},Default,,0,0,0,,{wrapped}"
            )
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _escape_drawtext(value: str) -> str:
    return (
        value.replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:")
        .replace(",", "\\,").replace("%", "\\%").replace("[", "\\[").replace("]", "\\]")
    )


def build_filter_complex(
    cues: list[BrollCue],
    width: int,
    height: int,
    punch_ins: list[PunchInCue] | None = None,
    callouts: list[CalloutCue] | None = None,
    *,
    include_captions: bool = True,
) -> str:
    filters = ["[0:v]format=yuv420p[base0]"]
    current = "base0"

    # Emphasis zooms affect the speaker footage first. Full-frame B-roll is
    # composited afterwards so a punch-in never accidentally zooms a cutaway.
    for index, cue in enumerate(punch_ins or [], start=1):
        start, end = f"{cue.start:.3f}", f"{cue.end:.3f}"
        zoom_width = math.ceil(width * cue.zoom / 2) * 2
        zoom_height = math.ceil(height * cue.zoom / 2) * 2
        fade_out_start = max(cue.start, cue.end - 0.22)
        base_label, zoom_source, zoom_label, output_label = (
            f"pbase{index}", f"pzoomsrc{index}", f"pzoom{index}", f"punch{index}"
        )
        filters.append(f"[{current}]split=2[{base_label}][{zoom_source}]")
        filters.append(
            f"[{zoom_source}]scale={zoom_width}:{zoom_height}:flags=lanczos,"
            f"crop={width}:{height}:(iw-ow)/2:(ih-oh)/2,format=yuva420p,"
            f"fade=t=in:st={start}:d=0.22:alpha=1,"
            f"fade=t=out:st={fade_out_start:.3f}:d=0.22:alpha=1[{zoom_label}]"
        )
        filters.append(
            f"[{base_label}][{zoom_label}]overlay=0:0:eof_action=pass:repeatlast=0:"
            f"enable='between(t,{start},{end})'[{output_label}]"
        )
        current = output_label

    # B-roll has priority over speaker zooms and is fitted to the whole canvas.
    for index, cue in enumerate(cues, start=1):
        start, end = f"{cue.start:.3f}", f"{cue.end:.3f}"
        duration = f"{cue.end - cue.start:.3f}"
        broll_label, output_label = f"broll{index}", f"v{index}"
        filters.append(
            f"[{index}:v]trim=duration={duration},setpts=PTS-STARTPTS+{start}/TB,"
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},setsar=1,format=yuv420p[{broll_label}]"
        )
        filters.append(
            f"[{current}][{broll_label}]overlay=0:0:eof_action=pass:repeatlast=0:"
            f"enable='between(t,{start},{end})'[{output_label}]"
        )
        current = output_label

    for index, cue in enumerate(callouts or [], start=1):
        safe_font_size = max(
            22, min(48, int(height * 0.026),
                    int((width * 0.82) / (max(1, len(cue.text)) * 0.58)))
        )
        text = _escape_drawtext(cue.text)
        output_label = f"callout{index}"
        filters.append(
            f"[{current}]drawtext=font='Liberation Sans':text='{text}':"
            f"fontcolor=white:fontsize={safe_font_size}:borderw=2:bordercolor=black@0.85:"
            f"box=1:boxcolor=black@0.72:boxborderw=18:"
            f"x=(w-text_w)/2:y=h*0.14:"
            f"enable='between(t,{cue.start:.3f},{cue.end:.3f})'[{output_label}]"
        )
        current = output_label

    if include_captions:
        filters.append(f"[{current}]subtitles=captions.ass[vout]")
    else:
        filters.append(f"[{current}]null[vout]")
    return ";".join(filters)

def render_video(
    input_path: Path,
    broll_paths: dict[str, Path],
    cues: list[BrollCue],
    transcript: list[TranscriptSegment],
    output_path: Path,
    *,
    punch_ins: list[PunchInCue] | None = None,
    callouts: list[CalloutCue] | None = None,
    add_captions: bool = False,
) -> Path:
    details = probe_video(input_path)
    if details["duration"] <= 0:
        raise ValueError("Input clip duration could not be determined.")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if add_captions:
        write_ass_captions(transcript, output_path.parent / "captions.ass", details["width"], details["height"])

    selected_cues, selected_paths = [], []
    for cue in cues:
        asset_path = broll_paths.get(cue.asset_id)
        if asset_path and asset_path.is_file():
            selected_cues.append(cue)
            selected_paths.append(asset_path)

    command = ["ffmpeg", "-hide_banner", "-y", "-i", str(input_path)]
    for path in selected_paths:
        command.extend(["-i", str(path)])
    command.extend([
        "-filter_complex", build_filter_complex(
            selected_cues, details["width"], details["height"],
            punch_ins=punch_ins, callouts=callouts, include_captions=add_captions,
        ),
        "-map", "[vout]", "-map", "0:a?", "-t", f"{details['duration']:.3f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
        "-movflags", "+faststart",
    ])
    if details["has_audio"]:
        command.extend(["-af", "loudnorm=I=-16:TP=-1.5:LRA=11"])
    command.append(str(output_path))
    subprocess.run(command, check=True, cwd=str(output_path.parent))
    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise RuntimeError("FFmpeg did not produce a rendered output.")
    return output_path


def create_r2_client():
    import boto3
    from botocore.config import Config

    account_id = os.environ.get("R2_ACCOUNT_ID", "").strip()
    access_key = os.environ.get("R2_ACCESS_KEY_ID", "").strip()
    secret_key = os.environ.get("R2_SECRET_ACCESS_KEY", "").strip()
    missing = [name for name, value in (
        ("R2_ACCOUNT_ID", account_id), ("R2_ACCESS_KEY_ID", access_key),
        ("R2_SECRET_ACCESS_KEY", secret_key),
    ) if not value]
    if missing:
        raise RuntimeError("Missing required environment variables: " + ", ".join(missing))
    return boto3.client(
        "s3",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="auto",
        config=Config(signature_version="s3v4", connect_timeout=20, read_timeout=180,
                      retries={"mode": "standard", "total_max_attempts": 5}),
    )


def process_r2_clip(
    clip_key: str,
    job_id: str,
    *,
    catalog_key: str = "broll/catalog.json",
    output_prefix: str = "edited",
    model: str = "qwen3:4b-instruct-2507-q4_K_M",
    whisper_model: str = "small",
    ollama_url: str = "http://127.0.0.1:11434",
    caption_mode: str = "auto",
) -> dict[str, Any]:
    clip_key = clip_key.strip()
    if not clip_key or clip_key.startswith("/") or ".." in clip_key.split("/"):
        raise ValueError("clip_key must be a relative R2 key without '..' path parts.")
    if not clip_key.startswith("jobs/"):
        raise ValueError("Source clip key must be inside the original jobs/ prefix.")
    if PurePosixPath(clip_key).suffix.lower() != ".mp4":
        raise ValueError("Source clip must be an MP4 object.")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", str(job_id)):
        raise ValueError("job_id may contain only letters, numbers, underscores, and hyphens.")
    if not catalog_key.startswith("broll/") or ".." in catalog_key.split("/"):
        raise ValueError("catalog_key must be a relative object key inside broll/.")
    if output_prefix.strip("/") != "edited":
        raise ValueError("Output prefix is fixed to edited/ to protect original clip objects.")
    if caption_mode not in {"auto", "preserve", "force"}:
        raise ValueError("caption_mode must be one of: auto, preserve, force.")

    bucket = os.environ.get("R2_BUCKET", "telegram-video-clipper-uploads").strip()
    client = create_r2_client()
    with tempfile.TemporaryDirectory(prefix="video-editor-") as temp_dir:
        workdir = Path(temp_dir)
        clip_path = workdir / "input" / PurePosixPath(clip_key).name
        clip_path.parent.mkdir(parents=True, exist_ok=True)
        client.download_file(bucket, clip_key, str(clip_path))
        if not clip_path.is_file() or clip_path.stat().st_size == 0:
            raise RuntimeError(f"R2 download was empty: {clip_key}")

        details = probe_video(clip_path)
        transcript, language = transcribe_video(clip_path, whisper_model)
        assets = load_broll_catalog(client, bucket, catalog_key)
        plan = create_edit_plan(transcript, details["duration"], assets, model=model, ollama_url=ollama_url)

        broll_paths: dict[str, Path] = {}
        for cue in plan.broll_cues:
            asset = next(item for item in assets if item.asset_id == cue.asset_id)
            path = workdir / "broll" / f"{asset.asset_id}{PurePosixPath(asset.key).suffix}"
            path.parent.mkdir(parents=True, exist_ok=True)
            client.download_file(bucket, asset.key, str(path))
            if not path.is_file() or path.stat().st_size == 0:
                raise RuntimeError(f"Selected B-roll asset download was empty: {asset.asset_id}")
            broll_paths[asset.asset_id] = path

        add_captions, caption_decision = resolve_caption_policy(
            caption_mode, clip_path, details["duration"]
        )
        output_name = f"{clip_path.stem}.edited.mp4"
        output_path = workdir / "output" / output_name
        output_path.parent.mkdir(parents=True, exist_ok=True)
        render_video(
            clip_path, broll_paths, list(plan.broll_cues), transcript, output_path,
            punch_ins=list(plan.punch_ins), callouts=list(plan.callouts),
            add_captions=add_captions,
        )
        output_key = f"edited/{job_id}/{output_name}"
        if output_key == clip_key:
            raise RuntimeError("Output key unexpectedly matches the source key.")

        manifest = {
            "source_key": clip_key, "output_key": output_key,
            "duration_seconds": round(details["duration"], 3), "language": language,
            "model": model, "whisper_model": whisper_model, "title": plan.title,
            "caption_mode": caption_mode, "caption_decision": caption_decision,
            "broll_cues": [asdict(cue) for cue in plan.broll_cues],
            "punch_ins": [asdict(cue) for cue in plan.punch_ins],
            "callouts": [asdict(cue) for cue in plan.callouts],
            "rejected_cues": list(plan.rejected_cues),
            "broll_asset_sources": {
                cue.asset_id: {
                    "source_url": next(a.source_url for a in assets if a.asset_id == cue.asset_id),
                    "license": next(a.license for a in assets if a.asset_id == cue.asset_id),
                } for cue in plan.broll_cues
            },
        }
        client.upload_file(str(output_path), bucket, output_key, ExtraArgs={"ContentType": "video/mp4"})
        manifest_key = f"edited/{job_id}/edit-plan.json"
        client.put_object(
            Bucket=bucket, Key=manifest_key,
            Body=json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
            ContentType="application/json",
        )
        return {
            "source_key": clip_key, "output_key": output_key, "manifest_key": manifest_key,
            "title": plan.title, "broll_count": len(plan.broll_cues),
            "punch_in_count": len(plan.punch_ins), "callout_count": len(plan.callouts),
            "caption_decision": caption_decision,
            "duration_seconds": round(details["duration"], 3),
        }
