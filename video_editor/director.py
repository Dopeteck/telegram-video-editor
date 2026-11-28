"""Adaptive editorial direction and transcript-grounded motion graphic cues."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Sequence

from .editorial import _find_timed_phrase


CONTENT_TYPES = {
    "gaming", "sports", "tutorial", "education", "business", "news",
    "interview", "vlog", "travel", "music", "product", "entertainment",
    "documentary", "other",
}
VISUAL_MODES = {"impact", "instructional", "editorial", "cinematic", "minimal"}
ENERGY_LEVELS = {"low", "medium", "high"}
GRAPHIC_KINDS = {"impact", "stat", "quote", "step"}

# The profile determines a visual language; the graphic cue determines what
# the audience should notice. Neither is allowed to introduce new facts.
@dataclass(frozen=True)
class EditorialProfile:
    content_type: str = "other"
    visual_mode: str = "minimal"
    energy: str = "medium"
    rationale: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class GraphicCue:
    start: float
    end: float
    kind: str
    headline: str
    detail: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clean(value: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _phrase_is_spoken_at(
    phrase: str,
    transcript_segments: Sequence[Any],
    start: float,
    end: float,
) -> bool:
    if not phrase or not transcript_segments:
        return False
    spoken_window = _find_timed_phrase(phrase, transcript_segments, near=start)
    if spoken_window is None:
        return False
    spoken_start, spoken_end = spoken_window
    # Do not let a graphic appear several seconds before its words are spoken
    # or linger over an unrelated sentence. A one-second lead/lag allows the
    # designed treatment to breathe without breaking transcript alignment.
    return (
        end > spoken_start
        and start < spoken_end
        and start >= spoken_start - 1.0
        and end <= spoken_end + 1.0
    )


def validate_editorial_direction(
    profile_payload: Any,
    graphic_rows: Any,
    duration: float,
    transcript_segments: Sequence[Any],
    *,
    max_graphics: int = 3,
) -> tuple[EditorialProfile, tuple[GraphicCue, ...], tuple[str, ...]]:
    """Validate an adaptive visual direction and all on-screen words.

    A graphic is accepted only when its headline (and optional detail) is an
    exact, time-aligned phrase from the source transcript. This keeps the
    renderer from inventing claims or displaying words over unrelated speech.
    """
    rejected: list[str] = []
    raw = profile_payload if isinstance(profile_payload, dict) else {}
    content_type = str(raw.get("content_type") or "other").strip().lower()
    visual_mode = str(raw.get("visual_mode") or "minimal").strip().lower()
    energy = str(raw.get("energy") or "medium").strip().lower()
    if content_type not in CONTENT_TYPES:
        rejected.append(f"editorial profile: unsupported content_type '{content_type}'")
        content_type = "other"
    if visual_mode not in VISUAL_MODES:
        rejected.append(f"editorial profile: unsupported visual_mode '{visual_mode}'")
        visual_mode = "minimal"
    if energy not in ENERGY_LEVELS:
        rejected.append(f"editorial profile: unsupported energy '{energy}'")
        energy = "medium"
    profile = EditorialProfile(
        content_type=content_type,
        visual_mode=visual_mode,
        energy=energy,
        rationale=_clean(raw.get("rationale"), 240),
    )

    if not isinstance(graphic_rows, list):
        return profile, (), tuple(rejected + ["graphic_cues: malformed list"])

    accepted: list[GraphicCue] = []
    for index, row in enumerate(graphic_rows):
        if len(accepted) >= max_graphics:
            rejected.append(f"graphic {index + 1}: over the {max_graphics}-cue limit")
            continue
        if not isinstance(row, dict):
            rejected.append(f"graphic {index + 1}: malformed row")
            continue
        try:
            start, end = float(row["start"]), float(row["end"])
        except (KeyError, TypeError, ValueError):
            rejected.append(f"graphic {index + 1}: missing or invalid timestamps")
            continue
        kind = str(row.get("kind") or "").strip().lower()
        headline = _clean(row.get("headline"), 48)
        detail = _clean(row.get("detail"), 72)
        if kind not in GRAPHIC_KINDS:
            rejected.append(f"graphic {index + 1}: unsupported graphic kind")
            continue
        if start < 0 or end <= start or end > duration:
            rejected.append(f"graphic {index + 1}: timestamps are outside the clip")
            continue
        if not 1.6 <= end - start <= 4.5:
            rejected.append(f"graphic {index + 1}: duration must be 1.6–4.5 seconds")
            continue
        if not headline or "\n" in headline or "\r" in headline:
            rejected.append(f"graphic {index + 1}: headline must be a non-empty single line")
            continue
        if not _phrase_is_spoken_at(headline, transcript_segments, start, end):
            rejected.append(f"graphic {index + 1}: headline is not an exact phrase spoken at this time")
            continue
        if detail and not _phrase_is_spoken_at(detail, transcript_segments, start, end):
            rejected.append(f"graphic {index + 1}: detail is not an exact phrase spoken at this time")
            continue
        if kind == "stat" and not re.search(r"(?:\d|[$€£₦%])", headline):
            rejected.append(f"graphic {index + 1}: stat headline must contain a spoken number or symbol")
            continue
        accepted.append(GraphicCue(
            start=start,
            end=end,
            kind=kind,
            headline=headline,
            detail=detail,
            reason=_clean(row.get("reason") or "Highlight a key moment", 180),
        ))

    accepted.sort(key=lambda cue: cue.start)
    non_overlapping: list[GraphicCue] = []
    for cue in accepted:
        if non_overlapping and cue.start < non_overlapping[-1].end:
            rejected.append(f"graphic at {cue.start:.2f}s: overlaps another graphic")
        else:
            non_overlapping.append(cue)

    return profile, tuple(non_overlapping), tuple(rejected)
