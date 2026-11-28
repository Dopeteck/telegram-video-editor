import shutil
import subprocess

import pytest

from video_editor.engine import (
    BrollCue,
    CalloutCue,
    PunchInCue,
    TranscriptSegment,
    TranscriptWord,
    build_filter_complex,
    probe_video,
    render_video,
)


def test_filter_graph_contains_timed_contain_broll_and_caption_layer():
    graph = build_filter_complex(
        [BrollCue(4.0, 9.0, "city", "City is discussed", "city traffic")],
        1080,
        1920,
    )
    assert "[1:v]trim=duration=5.000" in graph
    assert "enable='between(t,4.000,9.000)'" in graph
    assert "force_original_aspect_ratio=increase,crop=1080:1920,boxblur=24:2" in graph
    assert "force_original_aspect_ratio=decrease" in graph
    assert "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black@0" in graph
    assert "subtitles=captions.ass[vout]" in graph


def test_caption_only_render_does_not_add_unrelated_footage():
    graph = build_filter_complex([], 1080, 1920)
    assert graph == "[0:v]format=yuv420p[base0];[base0]subtitles=captions.ass[vout]"


def test_source_caption_preservation_can_skip_subtitle_filter():
    graph = build_filter_complex([], 1080, 1920, include_captions=False)
    assert graph == "[0:v]format=yuv420p[base0];[base0]null[vout]"


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg is not installed")
def test_ffmpeg_renders_zoom_callout_karaoke_captions_and_preserves_audio(tmp_path):
    source = tmp_path / "source.mp4"
    output = tmp_path / "edited.mp4"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=360x640:rate=24:duration=3",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=3",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            "-shortest", str(source),
        ],
        check=True,
        capture_output=True,
    )
    render_video(
        source,
        {},
        [],
        [TranscriptSegment(
            0, 3, "A synthetic test sentence.",
            (
                TranscriptWord(0.0, 0.25, "A"),
                TranscriptWord(0.25, 0.55, "synthetic"),
                TranscriptWord(0.55, 0.85, "test"),
                TranscriptWord(0.85, 1.2, "sentence."),
            ),
        )],
        output,
        punch_ins=[PunchInCue(0.5, 2.5, 1.10, "Test emphasis")],
        callouts=[CalloutCue(1.0, 2.8, "A synthetic fact", "Test graphic")],
        add_captions=True,
    )
    details = probe_video(output)
    assert details["width"] == 360
    assert details["height"] == 640
    assert details["has_audio"] is True
    assert 2.8 <= details["duration"] <= 3.1



@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg is not installed")
def test_ffmpeg_renders_landscape_broll_without_cropping_the_foreground(tmp_path):
    source = tmp_path / "portrait-source.mp4"
    broll = tmp_path / "landscape-broll.mp4"
    output = tmp_path / "edited-with-broll.mp4"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=360x640:rate=24:duration=3",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=3",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            "-shortest", str(source),
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=24:duration=2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(broll),
        ],
        check=True,
        capture_output=True,
    )
    render_video(
        source,
        {"landscape": broll},
        [BrollCue(0.5, 2.5, "landscape", "Matching cutaway", "social media")],
        [TranscriptSegment(0, 3, "A synthetic test sentence.")],
        output,
        add_captions=False,
    )
    details = probe_video(output)
    assert details["width"] == 360
    assert details["height"] == 640
    assert details["has_audio"] is True
    assert 2.8 <= details["duration"] <= 3.1
