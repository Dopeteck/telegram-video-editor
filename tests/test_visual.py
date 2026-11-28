import json

from video_editor.editorial import PunchInCue
from video_editor.visual import (
    FaceObservation,
    VisualAnalysis,
    apply_face_focus,
    choose_frame_timestamps,
    face_focus_at,
    parse_scene_times,
    parse_vision_response,
)


def test_parse_scene_times_filters_clip_edges_and_near_duplicates():
    log = (
        "pts_time:0.10 pts_time:4.20 pts_time:4.80 "
        "pts_time:9.75 pts_time:99.00"
    )
    assert parse_scene_times(log, 10.0) == (4.2,)


def test_choose_frame_timestamps_prioritises_scene_intervals_and_caps_samples():
    timestamps = choose_frame_timestamps(60, [10, 20, 30, 40, 50], max_frames=6)
    assert len(timestamps) == 6
    assert all(0 < value < 60 for value in timestamps)
    assert tuple(sorted(timestamps)) == timestamps


def test_parse_vision_response_maps_ordered_images_to_timestamps():
    content = json.dumps({
        "frames": [
            {
                "description": "A presenter speaks to camera.",
                "shot_type": "talking_head",
                "visible_subjects": ["one person"],
                "composition": "centre",
                "visual_interest": "The face is clearly visible.",
            },
            {
                "description": "A close view of a phone screen.",
                "shot_type": "object_closeup",
                "visible_subjects": ["phone"],
                "composition": "right",
            },
        ]
    })
    frames = parse_vision_response(content, [2.5, 7.0])
    assert frames[0].timestamp == 2.5
    assert frames[0].shot_type == "talking_head"
    assert frames[1].description == "A close view of a phone screen."
    assert frames[1].visual_interest == ""


def test_parse_vision_response_rejects_invalid_json():
    try:
        parse_vision_response("not json", [1.0])
    except ValueError as exc:
        assert "valid JSON" in str(exc)
    else:
        raise AssertionError("invalid JSON must be rejected")


def test_face_focus_uses_nearby_median_and_defaults_to_center():
    observations = [
        FaceObservation(10, 0.40, 0.42, 0.1, 0.2),
        FaceObservation(11, 0.44, 0.45, 0.1, 0.2),
        FaceObservation(12, 0.90, 0.90, 0.1, 0.2),
    ]
    assert face_focus_at(observations, 10.5) == (0.44, 0.45)
    assert face_focus_at(observations, 30) == (0.5, 0.5)


def test_apply_face_focus_attaches_face_position_to_punch_in():
    cue = PunchInCue(10, 14, 1.08, "Emphasise speaker")
    focused = apply_face_focus(
        [cue],
        [FaceObservation(12, 0.32, 0.38, 0.1, 0.2)],
    )
    assert focused[0].focus_x == 0.32
    assert focused[0].focus_y == 0.38
    assert focused[0].start == cue.start


def test_visual_analysis_manifest_and_planner_context_are_compact():
    analysis = VisualAnalysis(
        scene_changes=(5.0,),
        frames=(),
        faces=(FaceObservation(5.0, 0.4, 0.5, 0.1, 0.2),),
        vision_model="qwen3-vl:2b",
        vision_status="completed",
    )
    assert analysis.to_dict()["vision_model"] == "qwen3-vl:2b"
    assert analysis.planner_context()["face_positions"][0]["center_x"] == 0.4


def test_face_aware_zoom_uses_detected_focal_point_in_ffmpeg_crop():
    from video_editor.engine import build_filter_complex

    graph = build_filter_complex(
        [],
        1080,
        1920,
        punch_ins=[PunchInCue(2, 5, 1.08, "Keep speaker in view", 0.35, 0.40)],
        include_captions=False,
    )
    assert "0.3500*iw-ow/2" in graph
    assert "0.4000*ih-oh*0.42" in graph


def test_frame_extraction_uses_opencv_on_a_real_synthetic_video(tmp_path):
    import base64
    import shutil
    import subprocess

    import pytest
    from video_editor.visual import _extract_samples_and_faces

    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg is not installed")
    video = tmp_path / "visual-sample.mp4"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=360x640:rate=12:duration=3",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video),
        ],
        check=True,
        capture_output=True,
    )
    images, faces = _extract_samples_and_faces(video, 3.0, [0.5, 1.5])
    assert [round(item["timestamp"], 1) for item in images] == [0.5, 1.5]
    assert all(base64.b64decode(item["image"]) for item in images)
    assert isinstance(faces, tuple)
