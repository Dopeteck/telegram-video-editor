from video_editor.engine import parse_catalog, validate_edit_plan, BrollAsset


def _asset(asset_id="city"):
    return BrollAsset(
        asset_id=asset_id,
        key=f"broll/{asset_id}.mp4",
        description="City traffic at night",
        tags=("city", "night"),
        source_url="https://example.com/source",
        license="Owned footage",
        rights_verified=True,
    )


def test_catalog_only_accepts_rights_verified_assets_with_source_and_license():
    assets = parse_catalog({"assets": [
        {"id": "ok", "key": "broll/ok.mp4", "description": "Traffic",
         "source_url": "https://example.com/source", "license": "Owned",
         "rights_verified": True},
        {"id": "unknown", "key": "broll/unknown.mp4", "description": "Unknown",
         "source_url": "https://example.com/source", "license": "Unknown",
         "rights_verified": False},
        {"id": "bad", "key": "broll/../outside.mp4", "description": "Bad path",
         "source_url": "https://example.com/source", "license": "Owned",
         "rights_verified": True},
        {"id": "../escape", "key": "broll/escape.mp4", "description": "Bad ID",
         "source_url": "https://example.com/source", "license": "Owned",
         "rights_verified": True},
    ]})
    assert [asset.asset_id for asset in assets] == ["ok"]


def test_plan_keeps_only_known_valid_non_overlapping_cues():
    plan = validate_edit_plan({
        "title": "A city after dark",
        "broll_cues": [
            {"start": 5, "end": 10, "asset_id": "city"},
            {"start": 8, "end": 12, "asset_id": "city"},
            {"start": 15, "end": 19, "asset_id": "invented"},
        ],
    }, duration=30, assets=[_asset()])
    assert plan.title == "A city after dark"
    assert len(plan.broll_cues) == 1
    assert len(plan.rejected_cues) == 2


def test_plan_rejects_cue_outside_clip_duration():
    plan = validate_edit_plan(
        {"title": "Test", "broll_cues": [{"start": 28, "end": 32, "asset_id": "city"}]},
        duration=30, assets=[_asset()],
    )
    assert plan.broll_cues == ()



def test_missing_broll_catalogue_falls_back_to_caption_only():
    from video_editor.engine import load_broll_catalog

    class MissingObject:
        def get_object(self, **kwargs):
            raise type("MissingKey", (Exception,), {
                "response": {"Error": {"Code": "NoSuchKey"}}
            })("not found")

    assert load_broll_catalog(MissingObject(), "bucket", "broll/catalog.json") == []


def test_empty_broll_library_still_gets_an_ai_editorial_plan(monkeypatch):
    import json
    import video_editor.engine as engine

    transcript = [
        engine.TranscriptSegment(0.0, 3.0, "This is a sample sentence."),
        engine.TranscriptSegment(3.0, 7.0, "The market cap is like $1.6 trillion."),
    ]
    payload = {
        "title": "A trillion-dollar market",
        "broll_cues": [],
        "punch_ins": [{"start": 3.0, "end": 7.0, "zoom": 1.10, "reason": "Emphasise the number"}],
        "callouts": [{"start": 3.0, "end": 6.0, "text": "$1.6 trillion", "reason": "Key market figure"}],
    }

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({"message": {"content": json.dumps(payload)}}).encode("utf-8")

    def fake_urlopen(request, timeout):
        request_body = json.loads(request.data.decode("utf-8"))
        assert request_body["messages"][0]["role"] == "system"
        assert json.loads(request_body["messages"][1]["content"])["available_rights_cleared_broll"] == []
        return FakeResponse()

    monkeypatch.setattr(engine.urllib.request, "urlopen", fake_urlopen)
    plan = engine.create_edit_plan(transcript, 10.0, [])
    assert plan.title == "A trillion-dollar market"
    assert plan.broll_cues == ()
    assert len(plan.punch_ins) == 1
    assert plan.callouts[0].text == "$1.6 trillion"


def test_broll_requires_transcript_evidence_and_matching_asset_metadata():
    from video_editor.engine import TranscriptSegment, TranscriptWord

    asset = BrollAsset(
        asset_id="social-feed",
        key="broll/social-feed.mp4",
        description="A person scrolling a social media feed on a phone",
        tags=("social", "media", "phone", "feed"),
        source_url="https://example.com/source",
        license="Owned footage",
        rights_verified=True,
    )
    transcript = [
        TranscriptSegment(
            10.0,
            16.0,
            "People scroll social media feeds every day.",
            (
                TranscriptWord(10.0, 10.3, "People"),
                TranscriptWord(10.3, 10.8, "scroll"),
                TranscriptWord(10.8, 11.2, "social"),
                TranscriptWord(11.2, 11.7, "media"),
                TranscriptWord(11.7, 12.2, "feeds"),
                TranscriptWord(12.2, 12.5, "every"),
                TranscriptWord(12.5, 13.0, "day."),
            ),
        )
    ]
    plan = validate_edit_plan(
        {
            "title": "Social media habits",
            "broll_cues": [{
                "start": 10.5,
                "end": 14.0,
                "asset_id": "social-feed",
                "evidence": "social media feeds",
                "reason": "Show the exact subject being discussed",
            }],
        },
        duration=30,
        assets=[asset],
        transcript=transcript,
    )
    assert len(plan.broll_cues) == 1
    assert plan.broll_cues[0].evidence == "social media feeds"

    mismatched = validate_edit_plan(
        {
            "title": "Social media habits",
            "broll_cues": [{
                "start": 10.5,
                "end": 14.0,
                "asset_id": "social-feed",
                "evidence": "the weather is changing",
            }],
        },
        duration=30,
        assets=[asset],
        transcript=transcript,
    )
    assert mismatched.broll_cues == ()
    assert any("exact phrase" in reason for reason in mismatched.rejected_cues)


def test_broll_is_rejected_when_spoken_topic_does_not_match_asset_tags():
    from video_editor.engine import TranscriptSegment

    plan = validate_edit_plan(
        {
            "title": "Social media habits",
            "broll_cues": [{
                "start": 10.5,
                "end": 14.0,
                "asset_id": "city",
                "evidence": "social media feeds",
            }],
        },
        duration=30,
        assets=[_asset()],
        transcript=[TranscriptSegment(10.0, 16.0, "People scroll social media feeds every day.")],
    )
    assert plan.broll_cues == ()
    assert any("does not match the asset description or tags" in reason for reason in plan.rejected_cues)


def test_broll_is_rejected_when_evidence_is_missing_or_out_of_time():
    from video_editor.engine import TranscriptSegment

    missing_evidence = validate_edit_plan(
        {"title": "Test", "broll_cues": [{"start": 1, "end": 4, "asset_id": "city"}]},
        duration=30,
        assets=[_asset()],
        transcript=[TranscriptSegment(10, 16, "A city street is busy with traffic.")],
    )
    assert missing_evidence.broll_cues == ()
    assert any("missing exact spoken evidence" in reason for reason in missing_evidence.rejected_cues)

    wrong_time = validate_edit_plan(
        {"title": "Test", "broll_cues": [{
            "start": 1, "end": 4, "asset_id": "city", "evidence": "city street",
        }]},
        duration=30,
        assets=[_asset()],
        transcript=[TranscriptSegment(10, 16, "A city street is busy with traffic.")],
    )
    assert wrong_time.broll_cues == ()
    assert any("not spoken during" in reason for reason in wrong_time.rejected_cues)


def test_broll_evidence_keeps_specific_topic_words_that_look_like_stopwords():
    from video_editor.engine import TranscriptSegment

    asset = BrollAsset(
        asset_id="like-button",
        key="broll/like-button.mp4",
        description="Close-up of a social media like button being tapped",
        tags=("like", "button", "social media"),
        source_url="https://example.com/source",
        license="Owned footage",
        rights_verified=True,
    )
    plan = validate_edit_plan(
        {
            "title": "The like button",
            "broll_cues": [{
                "start": 10,
                "end": 14,
                "asset_id": "like-button",
                "evidence": "the like button",
            }],
        },
        duration=30,
        assets=[asset],
        transcript=[TranscriptSegment(9, 15, "The like button changed everything.")],
    )
    assert len(plan.broll_cues) == 1


def test_speechless_clip_can_receive_visual_only_edit_plan(monkeypatch):
    import json
    import video_editor.engine as engine

    payload = {
        "title": "A decisive final move",
        "editorial_profile": {
            "content_type": "gaming",
            "visual_mode": "impact",
            "energy": "high",
            "rationale": "Fast action footage with no speech.",
        },
        "broll_cues": [],
        "punch_ins": [
            {"start": 10.0, "end": 14.0, "zoom": 1.08, "reason": "Emphasise the key action."}
        ],
        "callouts": [],
        "graphic_cues": [],
    }

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({"message": {"content": json.dumps(payload)}}).encode("utf-8")

    def fake_urlopen(request, timeout):
        prompt = json.loads(request.data.decode("utf-8"))["messages"][1]["content"]
        user_data = json.loads(prompt)
        assert user_data["speech_available"] is False
        assert user_data["transcript"] == ""
        assert "no usable speech" in user_data["instruction"]
        assert user_data["visual_analysis"]["sampled_frames"]
        return FakeResponse()

    monkeypatch.setattr(engine.urllib.request, "urlopen", fake_urlopen)
    visual_context = {
        "sampled_frames": [
            {"timestamp": 12.0, "description": "A player in a fast-paced game scene."}
        ],
        "scene_changes_seconds": [8.0, 12.0],
    }
    plan = engine.create_edit_plan(
        [], 30.0, [], visual_context=visual_context
    )
    assert plan.editorial_profile.content_type == "gaming"
    assert len(plan.punch_ins) == 1
    assert plan.graphic_cues == ()
    assert plan.callouts == ()


def test_transcription_returns_empty_result_for_speechless_video(monkeypatch, tmp_path):
    import sys
    from types import SimpleNamespace

    import video_editor.engine as engine

    class FakeWhisperModel:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, *args, **kwargs):
            return iter(()), SimpleNamespace(language=None)

    monkeypatch.setitem(
        sys.modules,
        "faster_whisper",
        SimpleNamespace(WhisperModel=FakeWhisperModel),
    )
    transcript, language = engine.transcribe_video(tmp_path / "music-only.mp4")
    assert transcript == []
    assert language is None
