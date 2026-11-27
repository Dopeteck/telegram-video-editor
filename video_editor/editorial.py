"""Safe editorial cue validation and burned-in subtitle detection."""

from __future__ import annotations

import csv
import io
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


@dataclass(frozen=True)
class PunchInCue:
    start: float
    end: float
    zoom: float
    reason: str


@dataclass(frozen=True)
class CalloutCue:
    start: float
    end: float
    text: str
    reason: str


def _normalise_phrase(value: str) -> str:
    value = value.casefold().replace("’", "'").replace("“", '"').replace("”", '"')
    value = re.sub(r"\s+", " ", value).strip()
    return value.strip(" \t\r\n\"'“”‘’.,!?;:")



def _phrase_tokens(value: str) -> list[str]:
    """Normalise spoken phrases consistently with Whisper's per-word tokens."""
    value = value.casefold().replace("’", "'")
    return re.findall(r"[a-z0-9]+(?:'[a-z0-9]+)?", value)


def _find_timed_phrase(
    phrase: str,
    transcript_segments: Sequence[Any],
) -> tuple[float, float] | None:
    """Find the closest timed occurrence of a phrase in a timestamped transcript."""
    target = _phrase_tokens(phrase)
    if not target:
        return None

    timed_tokens: list[tuple[str, float, float]] = []
    for segment in transcript_segments:
        words = getattr(segment, "words", ()) or ()
        if words:
            for word in words:
                word_text = str(getattr(word, "text", "") or "")
                start = getattr(word, "start", None)
                end = getattr(word, "end", None)
                if start is None or end is None:
                    continue
                for token in _phrase_tokens(word_text):
                    timed_tokens.append((token, float(start), float(end)))
        else:
            # Segment-level fallback keeps older callers usable, but the live
            # pipeline requests word timestamps so this is only a compatibility path.
            for token in _phrase_tokens(str(getattr(segment, "text", "") or "")):
                timed_tokens.append((
                    token,
                    float(getattr(segment, "start", 0.0)),
                    float(getattr(segment, "end", 0.0)),
                ))

    matches: list[tuple[float, float]] = []
    for index in range(len(timed_tokens) - len(target) + 1):
        window = timed_tokens[index:index + len(target)]
        if [item[0] for item in window] == target:
            matches.append((window[0][1], window[-1][2]))
    return matches[0] if matches else None


def validate_editorial_cues(
    payload: dict,
    duration: float,
    transcript_text: str,
    *,
    transcript_segments: Sequence[Any] | None = None,
    max_punch_ins: int = 4,
    max_callouts: int = 3,
) -> tuple[tuple[PunchInCue, ...], tuple[CalloutCue, ...], tuple[str, ...]]:
    """Validate the model's non-B-roll edits against the clip and transcript."""
    rejected: list[str] = []

    punch_rows = payload.get("punch_ins", [])
    if not isinstance(punch_rows, list):
        punch_rows = []
        rejected.append("punch_ins: malformed list")
    punch_candidates: list[PunchInCue] = []
    for index, row in enumerate(punch_rows):
        if len(punch_candidates) >= max_punch_ins:
            rejected.append(f"punch-in {index + 1}: over the {max_punch_ins}-cue limit")
            continue
        if not isinstance(row, dict):
            rejected.append(f"punch-in {index + 1}: malformed row")
            continue
        try:
            start, end = float(row["start"]), float(row["end"])
            zoom = float(row["zoom"])
        except (KeyError, TypeError, ValueError):
            rejected.append(f"punch-in {index + 1}: missing or invalid fields")
            continue
        if start < 0 or end <= start or end > duration:
            rejected.append(f"punch-in {index + 1}: timestamps are outside the clip")
            continue
        if not 2.0 <= end - start <= 8.0:
            rejected.append(f"punch-in {index + 1}: duration must be 2–8 seconds")
            continue
        if not 1.05 <= zoom <= 1.18:
            rejected.append(f"punch-in {index + 1}: zoom must be between 1.05 and 1.18")
            continue
        punch_candidates.append(PunchInCue(
            start, end, zoom, str(row.get("reason") or "Emphasis punch-in")[:180]
        ))

    punch_candidates.sort(key=lambda cue: cue.start)
    punch_ins: list[PunchInCue] = []
    for cue in punch_candidates:
        if punch_ins and cue.start < punch_ins[-1].end:
            rejected.append(f"punch-in at {cue.start:.2f}s: overlaps another punch-in")
        else:
            punch_ins.append(cue)

    callout_rows = payload.get("callouts", [])
    if not isinstance(callout_rows, list):
        callout_rows = []
        rejected.append("callouts: malformed list")
    source_text = _normalise_phrase(transcript_text)
    callout_candidates: list[CalloutCue] = []
    for index, row in enumerate(callout_rows):
        if len(callout_candidates) >= max_callouts:
            rejected.append(f"callout {index + 1}: over the {max_callouts}-cue limit")
            continue
        if not isinstance(row, dict):
            rejected.append(f"callout {index + 1}: malformed row")
            continue
        try:
            start, end = float(row["start"]), float(row["end"])
            phrase = str(row["text"] or "").strip()
        except (KeyError, TypeError, ValueError):
            rejected.append(f"callout {index + 1}: missing or invalid fields")
            continue
        if start < 0 or end <= start or end > duration:
            rejected.append(f"callout {index + 1}: timestamps are outside the clip")
            continue
        if not 1.5 <= end - start <= 5.0:
            rejected.append(f"callout {index + 1}: duration must be 1.5–5 seconds")
            continue
        if not phrase or len(phrase) > 34 or "\n" in phrase or "\r" in phrase:
            rejected.append(f"callout {index + 1}: text must be 1–34 characters on one line")
            continue
        if _normalise_phrase(phrase) not in source_text:
            rejected.append(f"callout {index + 1}: text is not an exact phrase from the transcript")
            continue
        if transcript_segments:
            spoken_window = _find_timed_phrase(phrase, transcript_segments)
            if spoken_window is None:
                rejected.append(f"callout {index + 1}: spoken phrase could not be located in timestamped words")
                continue
            spoken_start, spoken_end = spoken_window
            # Allow a small timing tolerance around Whisper's word boundaries,
            # but never let a real phrase be displayed over an unrelated moment.
            if end < spoken_start - 0.75 or start > spoken_end + 0.75:
                rejected.append(f"callout {index + 1}: cue timing does not match when the phrase is spoken")
                continue
        callout_candidates.append(CalloutCue(
            start, end, phrase, str(row.get("reason") or "Key phrase")[:180]
        ))

    callout_candidates.sort(key=lambda cue: cue.start)
    callouts: list[CalloutCue] = []
    for cue in callout_candidates:
        if callouts and cue.start < callouts[-1].end:
            rejected.append(f"callout at {cue.start:.2f}s: overlaps another callout")
        else:
            callouts.append(cue)

    return tuple(punch_ins), tuple(callouts), tuple(rejected)


def detect_burned_in_captions(
    video_path: Path,
    duration: float,
    *,
    sample_count: int = 8,
) -> bool | None:
    """Return True/False for visible subtitles, or None when OCR cannot be run.

    Frames are sampled across the clip and only the lower half is inspected, where
    subtitles and karaoke captions are normally placed. Returning None is a
    fail-safe signal: preserve the source rather than risk adding duplicate text.
    """
    if not shutil.which("tesseract") or duration <= 0 or sample_count < 2:
        return None

    positive_samples = 0
    usable_samples = 0
    fractions = [
        0.10 + index * (0.80 / (sample_count - 1))
        for index in range(sample_count)
    ]
    try:
        for fraction in fractions:
            timestamp = max(0.0, min(duration - 0.05, duration * fraction))
            frame = subprocess.run(
                [
                    "ffmpeg", "-hide_banner", "-loglevel", "error",
                    "-ss", f"{timestamp:.3f}", "-i", str(video_path),
                    "-frames:v", "1",
                    "-vf", "crop=iw:ih*0.55:0:ih*0.45,scale=1280:-1:flags=lanczos,format=gray",
                    "-f", "image2pipe", "-vcodec", "png", "pipe:1",
                ],
                check=True,
                capture_output=True,
            ).stdout
            if not frame:
                continue
            ocr = subprocess.run(
                ["tesseract", "stdin", "stdout", "--psm", "6", "tsv"],
                input=frame,
                check=True,
                capture_output=True,
                text=True,
                timeout=25,
            ).stdout
            usable_samples += 1
            rows = csv.DictReader(io.StringIO(ocr), delimiter="\t")
            words: list[str] = []
            confidences: list[float] = []
            for row in rows:
                word = (row.get("text") or "").strip()
                try:
                    confidence = float(row.get("conf") or -1)
                except ValueError:
                    continue
                if word and confidence >= 40 and re.search(r"[A-Za-z0-9]", word):
                    words.append(word)
                    confidences.append(confidence)
            recognised = " ".join(words)
            compact = re.sub(r"[^A-Za-z0-9]", "", recognised)
            if len(words) >= 2 and len(compact) >= 8 and confidences:
                if sum(confidences) / len(confidences) >= 45:
                    positive_samples += 1
            if positive_samples >= 2:
                return True
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired, UnicodeDecodeError):
        return None

    # A negative decision is only reliable when enough frames were actually
    # extracted and OCR'd. Sparse/failed sampling must preserve the source.
    minimum_usable_samples = max(3, (sample_count + 1) // 2)
    if usable_samples < minimum_usable_samples:
        return None
    return False
