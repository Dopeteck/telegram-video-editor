from pathlib import Path
from types import SimpleNamespace

import video_editor.engine as engine
from video_editor.editorial import (
    CalloutCue,
    PunchInCue,
    detect_burned_in_captions,
    validate_editorial_cues,
)


def test_callout_must_be_an_exact_transcript_excerpt():
    punch_ins, callouts, rejected = validate_editorial_cues(
        {
            "punch_ins": [{"start": 4, "end": 8, "zoom": 1.12}],
            "callouts": [
                {"start": 10, "end": 13, "text": "$1.6 trillion"},
                {"start": 15, "end": 18, "text": "$9 trillion"},
            ],
        },
        duration=30,
        transcript_text="The market cap is estimated at $1.6 trillion.",
    )
    assert len(punch_ins) == 1
    assert [cue.text for cue in callouts] == ["$1.6 trillion"]
    assert any("exact phrase" in reason for reason in rejected)



def test_callout_timing_must_match_when_the_phrase_is_spoken():
    words = (
        engine.TranscriptWord(10.2, 10.5, "The"),
        engine.TranscriptWord(10.5, 10.9, "market"),
        engine.TranscriptWord(10.9, 11.2, "cap"),
        engine.TranscriptWord(11.2, 11.6, "is"),
        engine.TranscriptWord(11.6, 12.0, "$1.6"),
        engine.TranscriptWord(12.0, 12.6, "trillion."),
    )
    transcript = [
        engine.TranscriptSegment(
            10.2, 12.6, "The market cap is $1.6 trillion.", words
        )
    ]
    payload = {
        "punch_ins": [],
        "callouts": [{"start": 1.0, "end": 4.0, "text": "$1.6 trillion"}],
    }
    _, callouts, rejected = validate_editorial_cues(
        payload,
        duration=30,
        transcript_text="The market cap is $1.6 trillion.",
        transcript_segments=transcript,
    )
    assert callouts == ()
    assert any("timing does not match" in reason for reason in rejected)

    payload["callouts"][0]["start"] = 10.5
    payload["callouts"][0]["end"] = 13.0
    _, callouts, rejected = validate_editorial_cues(
        payload,
        duration=30,
        transcript_text="The market cap is $1.6 trillion.",
        transcript_segments=transcript,
    )
    assert [cue.text for cue in callouts] == ["$1.6 trillion"]


def test_caption_detection_fails_safe_when_too_few_frames_are_readable(monkeypatch):
    monkeypatch.setattr("video_editor.editorial.shutil.which", lambda _: "/usr/bin/tesseract")

    def fake_run(command, **kwargs):
        if command[0] == "ffmpeg":
            return SimpleNamespace(stdout=b"")
        raise AssertionError("Tesseract must not run without an extracted frame")

    monkeypatch.setattr("video_editor.editorial.subprocess.run", fake_run)
    assert detect_burned_in_captions(Path("clip.mp4"), 90, sample_count=8) is None


def test_caption_detection_returns_false_only_after_enough_successful_ocr_samples(monkeypatch):
    monkeypatch.setattr("video_editor.editorial.shutil.which", lambda _: "/usr/bin/tesseract")

    def fake_run(command, **kwargs):
        if command[0] == "ffmpeg":
            return SimpleNamespace(stdout=b"frame")
        return SimpleNamespace(stdout="level\\ttext\\n")

    monkeypatch.setattr("video_editor.editorial.subprocess.run", fake_run)
    assert detect_burned_in_captions(Path("clip.mp4"), 90, sample_count=8) is False


def test_editorial_cues_reject_overlap_and_extreme_zoom():
    punch_ins, callouts, rejected = validate_editorial_cues(
        {
            "punch_ins": [
                {"start": 2, "end": 6, "zoom": 1.10},
                {"start": 5, "end": 8, "zoom": 1.12},
                {"start": 12, "end": 15, "zoom": 1.50},
            ],
            "callouts": [],
        },
        duration=20,
        transcript_text="A useful phrase appears here.",
    )
    assert len(punch_ins) == 1
    assert len(rejected) == 2


def test_caption_detection_fails_safe_when_ocr_is_unavailable(monkeypatch):
    monkeypatch.setattr("video_editor.editorial.shutil.which", lambda _: None)
    assert detect_burned_in_captions(Path("clip.mp4"), 90) is None


def test_auto_caption_policy_preserves_source_when_ocr_is_uncertain(monkeypatch):
    monkeypatch.setattr(engine, "detect_burned_in_captions", lambda *args: None)
    add_captions, decision = engine.resolve_caption_policy("auto", Path("clip.mp4"), 90)
    assert add_captions is False
    assert decision == "caption_detection_unavailable_preserved_source"


def test_auto_caption_policy_skips_new_text_when_source_already_has_subtitles(monkeypatch):
    monkeypatch.setattr(engine, "detect_burned_in_captions", lambda *args: True)
    add_captions, decision = engine.resolve_caption_policy("auto", Path("clip.mp4"), 90)
    assert add_captions is False
    assert decision == "existing_burned_in_captions_detected"


def test_auto_caption_policy_generates_only_when_no_existing_text_is_detected(monkeypatch):
    monkeypatch.setattr(engine, "detect_burned_in_captions", lambda *args: False)
    add_captions, decision = engine.resolve_caption_policy("auto", Path("clip.mp4"), 90)
    assert add_captions is True
    assert decision == "no_existing_burned_in_captions_detected"


def test_ass_captions_wrap_long_lines_inside_the_frame(tmp_path):
    path = tmp_path / "captions.ass"
    transcript = [
        engine.TranscriptSegment(
            0,
            4,
            "This is a deliberately long subtitle sentence that must wrap within a vertical video frame.",
        )
    ]
    engine.write_ass_captions(transcript, path, 720, 1280)
    result = path.read_text(encoding="utf-8")
    assert "WrapStyle: 0" in result
    assert r"\N" in result
    assert ",54,54," in result or ",48,48," in result


def test_filter_graph_has_speaker_punch_in_and_designed_callout_without_subtitles():
    graph = engine.build_filter_complex(
        [],
        1080,
        1920,
        punch_ins=[PunchInCue(8, 12, 1.12, "Key emphasis")],
        callouts=[CalloutCue(9, 12, "$1.6 trillion", "Important figure")],
        include_captions=False,
    )
    assert "scale=1210:2152" in graph
    assert "enable='between(t,8.000,12.000)'" in graph
    assert "drawtext=" in graph
    assert "$1.6 trillion" in graph
    assert "null[vout]" in graph
    assert "subtitles=captions.ass" not in graph
