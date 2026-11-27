from video_editor.telegram_delivery import DIRECT_VIDEO_LIMIT, delivery_mode


def test_telegram_delivery_uses_native_video_below_safe_upload_limit():
    assert delivery_mode(DIRECT_VIDEO_LIMIT) == "video"
    assert delivery_mode(DIRECT_VIDEO_LIMIT + 1) == "download_link"


def test_telegram_delivery_never_tries_to_upload_oversized_video():
    assert delivery_mode(100 * 1024 * 1024) == "download_link"
