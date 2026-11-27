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


def test_empty_broll_library_skips_ollama_planning(monkeypatch):
    import video_editor.engine as engine

    def unexpected_call(*args, **kwargs):
        raise AssertionError("Ollama should not be called without B-roll assets")

    monkeypatch.setattr(engine.urllib.request, "urlopen", unexpected_call)
    transcript = [engine.TranscriptSegment(0.0, 3.0, "This is a sample sentence.")]
    plan = engine.create_edit_plan(transcript, 10.0, [])
    assert plan.title == "Edited clip"
    assert plan.broll_cues == ()
