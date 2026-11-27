# R2 and Telegram setup

The workflow uses the existing private bucket `telegram-video-clipper-uploads`, but is independent of the clipper workflow.

## R2 secrets

In this repository, add:
- `R2_ACCOUNT_ID`
- `R2_ACCESS_KEY_ID`
- `R2_SECRET_ACCESS_KEY`

Use an R2 API token with object read/write access limited to this bucket where possible. The editor needs to read `jobs/*` and `broll/*`, and write `edited/*`. Do not grant broader account permissions than necessary.

## Telegram secrets

Add:
- `TELEGRAM_BOT_TOKEN`: the existing bot token used by the Cloudflare Worker.
- `TELEGRAM_CHAT_ID`: the Telegram chat to receive edited videos.

Do not paste either value into code or chat. Start a private chat with the bot and send `/start` before expecting it to deliver a file.

The editor sends files up to 49 MB as native Telegram videos. Larger files are delivered as a private R2 download link that expires after 24 hours. Telegram's standard Bot API caps bot video uploads at 50 MB.

## Input key

Completed clips are stored in this format:

```text
jobs/<job_id>/<clip-filename>.mp4
```

Use the exact key shown in the clipper output/Worker response, not a public URL.

## B-roll library

Upload the JSON catalogue to `broll/catalog.json` and upload each video to its matching `broll/...` key. See `broll/catalog.example.json`.

Use only footage you own, have licensed, or otherwise have a documented legal basis to use. Keep source/creator and licence details in the catalogue.

## Output

```text
edited/<workflow_run_id>/<original-filename>.edited.mp4
edited/<workflow_run_id>/edit-plan.json
```

The original `jobs/...` object is never overwritten.
