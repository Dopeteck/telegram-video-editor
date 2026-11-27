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
