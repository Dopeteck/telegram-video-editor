from video_editor.director import validate_editorial_direction
from video_editor.engine import TranscriptSegment, TranscriptWord


def _transcript():
    return [
        TranscriptSegment(
            2.0,
            8.0,
            "The real problem is not getting views, it is keeping attention.",
            (
                TranscriptWord(2.0, 2.3, "The"),
                TranscriptWord(2.3, 2.7, "real"),
                TranscriptWord(2.7, 3.1, "problem"),
                TranscriptWord(3.1, 3.3, "is"),
                TranscriptWord(3.3, 3.5, "not"),
                TranscriptWord(3.5, 4.0, "getting"),
                TranscriptWord(4.0, 4.4, "views,"),
                TranscriptWord(4.4, 4.6, "it"),
                TranscriptWord(4.6, 4.8, "is"),
                TranscriptWord(4.8, 5.3, "keeping"),
                TranscriptWord(5.3, 6.0, "attention."),
            ),
        ),
        TranscriptSegment(
            9.0,
            13.0,
            "We increased conversion by 35 percent.",
            (
                TranscriptWord(9.0, 9.4, "We"),
                TranscriptWord(9.4, 10.0, "increased"),
                TranscriptWord(10.0, 10.8, "conversion"),
                TranscriptWord(10.8, 11.0, "by"),
                TranscriptWord(11.0, 11.7, "35"),
                TranscriptWord(11.7, 12.2, "percent."),
            ),
        ),
    ]


def test_editorial_direction_accepts_grounded_content_specific_graphics():
    profile, graphics, rejected = validate_editorial_direction(
        {
            "content_type": "business",
            "visual_mode": "editorial",
            "energy": "high",
            "rationale": "A business explanation built around a measurable result.",
        },
        [
            {
                "start": 4.0,
                "end": 6.5,
                "kind": "impact",
                "headline": "keeping attention",
                "detail": "it is",
                "reason": "Emphasise the central argument.",
            },
            {
                "start": 10.8,
                "end": 13.0,
                "kind": "stat",
                "headline": "35 percent",
                "detail": "increased conversion",
                "reason": "Make the measurable result instantly legible.",
            },
        ],
        duration=20.0,
        transcript_segments=_transcript(),
    )
    assert profile.content_type == "business"
    assert profile.visual_mode == "editorial"
    assert [cue.kind for cue in graphics] == ["impact", "stat"]
    assert graphics[1].headline == "35 percent"
    assert rejected == ()


def test_editorial_direction_rejects_unspoken_or_mistimed_graphics():
    profile, graphics, rejected = validate_editorial_direction(
        {"content_type": "gaming", "visual_mode": "impact", "energy": "high"},
        [
            {
                "start": 2.5,
                "end": 5.0,
                "kind": "impact",
                "headline": "we made one million dollars",
            },
            {
                "start": 2.5,
                "end": 5.0,
                "kind": "quote",
                "headline": "35 percent",
            },
        ],
        duration=20.0,
        transcript_segments=_transcript(),
    )
    assert profile.content_type == "gaming"
    assert graphics == ()
    assert len(rejected) == 2


def test_stat_graphic_must_be_grounded_in_a_spoken_number():
    _, graphics, rejected = validate_editorial_direction(
        {"content_type": "education", "visual_mode": "instructional"},
        [
            {
                "start": 4.0,
                "end": 6.2,
                "kind": "stat",
                "headline": "keeping attention",
            }
        ],
        duration=20.0,
        transcript_segments=_transcript(),
    )
    assert graphics == ()
    assert any("spoken number" in reason for reason in rejected)


def test_editorial_direction_rejects_overlapping_graphics():
    _, graphics, rejected = validate_editorial_direction(
        {"content_type": "documentary", "visual_mode": "cinematic"},
        [
            {"start": 4.0, "end": 6.2, "kind": "quote", "headline": "keeping attention"},
            {"start": 4.5, "end": 6.5, "kind": "impact", "headline": "it is keeping"},
        ],
        duration=20.0,
        transcript_segments=_transcript(),
    )
    assert len(graphics) == 1
    assert any("overlaps" in reason for reason in rejected)


def test_engine_validates_and_preserves_adaptive_profile_and_graphics():
    from video_editor.engine import validate_edit_plan

    plan = validate_edit_plan(
        {
            "title": "Attention and conversion",
            "broll_cues": [],
            "punch_ins": [],
            "callouts": [],
            "editorial_profile": {
                "content_type": "business",
                "visual_mode": "editorial",
                "energy": "high",
                "rationale": "A concise business argument with a measurable result.",
            },
            "graphic_cues": [
                {
                    "start": 10.8,
                    "end": 13.0,
                    "kind": "stat",
                    "headline": "35 percent",
                    "detail": "",
                    "reason": "Give the spoken result a designed visual moment.",
                }
            ],
        },
        duration=20.0,
        assets=[],
        transcript=_transcript(),
    )
    assert plan.editorial_profile.content_type == "business"
    assert plan.editorial_profile.visual_mode == "editorial"
    assert len(plan.graphic_cues) == 1
    assert plan.to_dict()["graphic_cues"][0]["kind"] == "stat"


def test_graphic_filter_uses_content_category_and_visual_mode():
    from video_editor.director import EditorialProfile, GraphicCue
    from video_editor.engine import build_filter_complex

    graph = build_filter_complex(
        [],
        1080,
        1920,
        graphic_cues=[
            GraphicCue(
                10.0, 14.0, "stat", "35 percent",
                "increased conversion", "Emphasise the measured outcome",
            )
        ],
        editorial_profile=EditorialProfile("sports", "impact", "high"),
        include_captions=False,
    )
    assert "0xFFD84A" in graph
    assert "drawbox=" in graph
    assert "drawtext=" in graph
    assert "35 PERCENT" in graph
    assert "enable='between(t,10.000,14.000)'" in graph


def test_adaptive_graphic_filter_renders_with_ffmpeg(tmp_path):
    import shutil
    import subprocess

    import pytest
    from video_editor.director import EditorialProfile, GraphicCue
    from video_editor.engine import build_filter_complex

    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg is not installed")
    output = tmp_path / "graphic-overlay.mp4"
    graph = build_filter_complex(
        [],
        360,
        640,
        graphic_cues=[
            GraphicCue(
                0.4, 2.0, "impact", "KEEPING ATTENTION",
                "The real problem", "Emphasise the core message",
            )
        ],
        editorial_profile=EditorialProfile("education", "instructional", "medium"),
        include_captions=False,
    )
    result = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=360x640:rate=12:duration=2.5",
            "-filter_complex", graph, "-map", "[vout]", "-an",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert output.is_file() and output.stat().st_size > 0
