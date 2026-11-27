"""Deliver the edited result to Telegram.

Files below 49 MB are uploaded as native Telegram videos. Larger files are
shared as a private, time-limited R2 download button because the standard
Telegram Bot API caps bot video uploads at 50 MB.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

import requests

from .engine import create_r2_client

DIRECT_VIDEO_LIMIT = 49_000_000
DOWNLOAD_LINK_TTL_SECONDS = 24 * 60 * 60


def delivery_mode(size_bytes: int) -> str:
    return "video" if size_bytes <= DIRECT_VIDEO_LIMIT else "download_link"


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return cleaned[:120] or "edited-video.mp4"


def _telegram_request(token: str, method: str, *, data: dict[str, Any], files=None) -> dict[str, Any]:
    response = requests.post(
        f"https://api.telegram.org/bot{token}/{method}",
        data=data,
        files=files,
        timeout=(20, 300),
    )
    response.raise_for_status()
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError("Telegram returned a non-JSON response.") from exc
    if not payload.get("ok"):
        raise RuntimeError(f"Telegram {method} failed: {payload.get('description', 'unknown error')}")
    return payload.get("result") or {}


def _read_title(client, bucket: str, manifest_key: str | None) -> str:
    if not manifest_key:
        return "Your edited video is ready"
    try:
        response = client.get_object(Bucket=bucket, Key=manifest_key)
        manifest = json.loads(response["Body"].read().decode("utf-8"))
        title = str(manifest.get("title") or "").strip()
        return title[:150] if title else "Your edited video is ready"
    except Exception:
        return "Your edited video is ready"


def deliver_output(
    output_key: str,
    *,
    manifest_key: str | None = None,
    token: str | None = None,
    chat_id: str | None = None,
) -> dict[str, Any]:
    token = (token if token is not None else os.environ.get("TELEGRAM_BOT_TOKEN", "")).strip()
    chat_id = (chat_id if chat_id is not None else os.environ.get("TELEGRAM_CHAT_ID", "")).strip()
    if not token or not chat_id:
        print(
            "::warning::Telegram delivery skipped: add TELEGRAM_BOT_TOKEN and "
            "TELEGRAM_CHAT_ID repository secrets to enable Telegram downloads.",
            file=sys.stderr,
        )
        return {"delivered": False, "mode": "skipped", "reason": "Telegram secrets not configured"}

    if not output_key.startswith("edited/") or ".." in output_key.split("/"):
        raise ValueError("Telegram delivery accepts only editor output keys below edited/.")

    bucket = os.environ.get("R2_BUCKET", "telegram-video-clipper-uploads").strip()
    client = create_r2_client()
    head = client.head_object(Bucket=bucket, Key=output_key)
    size_bytes = int(head.get("ContentLength") or 0)
    if size_bytes <= 0:
        raise RuntimeError(f"Edited output is missing or empty: {output_key}")

    filename = _safe_filename(Path(output_key).name)
    title = _read_title(client, bucket, manifest_key)
    caption = f"🎬 {title}"[:200]
    mode = delivery_mode(size_bytes)

    if mode == "video":
        with tempfile.TemporaryDirectory(prefix="telegram-editor-delivery-") as temp_dir:
            local_path = Path(temp_dir) / filename
            client.download_file(bucket, output_key, str(local_path))
            with local_path.open("rb") as video_file:
                result = _telegram_request(
                    token,
                    "sendVideo",
                    data={
                        "chat_id": chat_id,
                        "caption": caption,
                        "supports_streaming": "true",
                    },
                    files={"video": (filename, video_file, "video/mp4")},
                )
        message_id = result.get("message_id")
        print(f"Telegram native video delivery succeeded (message_id={message_id}).")
        return {
            "delivered": True,
            "mode": mode,
            "message_id": message_id,
            "size_bytes": size_bytes,
        }

    download_url = client.generate_presigned_url(
        "get_object",
        Params={
            "Bucket": bucket,
            "Key": output_key,
            "ResponseContentType": "video/mp4",
            "ResponseContentDisposition": f'attachment; filename="{filename}"',
        },
        ExpiresIn=DOWNLOAD_LINK_TTL_SECONDS,
    )
    result = _telegram_request(
        token,
        "sendMessage",
        data={
            "chat_id": chat_id,
            "text": (
                f"🎬 {title}\n\n"
                f"Your edited video is {size_bytes / (1024 * 1024):.1f} MB. "
                "Use the button below to download the full-quality MP4. "
                "This private link expires in 24 hours."
            ),
            "reply_markup": json.dumps({
                "inline_keyboard": [[{
                    "text": "⬇️ Download edited video",
                    "url": download_url,
                }]]
            }),
            "disable_web_page_preview": "true",
        },
    )
    message_id = result.get("message_id")
    print(f"Telegram download-link delivery succeeded (message_id={message_id}); signed URL omitted from logs.")
    return {
        "delivered": True,
        "mode": mode,
        "message_id": message_id,
        "size_bytes": size_bytes,
        "link_expires_in_seconds": DOWNLOAD_LINK_TTL_SECONDS,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Send one edited R2 output to Telegram.")
    parser.add_argument("--output-key", required=True)
    parser.add_argument("--manifest-key", default=None)
    args = parser.parse_args()
    try:
        result = deliver_output(args.output_key, manifest_key=args.manifest_key)
    except Exception as exc:
        print(f"Telegram delivery failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
