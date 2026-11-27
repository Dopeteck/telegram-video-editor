# Telegram B-roll Uploader

A dedicated, private Telegram bot that saves short video assets into the existing Cloudflare R2 bucket and registers approved assets in `broll/catalog.json`.

This is a separate Cloudflare Worker and must use a **new Telegram bot token**. Do not reuse the token/webhook used by the existing Chopify/clipper bot: a Telegram bot can only have one active webhook URL.

## What it does

1. You send a video to the bot in a private Telegram chat.
2. The Worker downloads it through Telegram's Bot API and streams it into R2 under `broll/uploads/`.
3. The bot asks for a visible-content description, comma-separated tags, original source URL, and licence/permission note.
4. The bot displays a review card. Only after you press **I have checked the rights — publish** does it append an entry to `broll/catalog.json` with `rights_verified: true`.
5. The editor can then consider the asset during AI planning.

Unfinished uploads are staged in R2 under `broll/uploads/` and have a pending metadata record under `broll/_pending/`. They are not available to the editor until confirmed. Pending uploads expire after 24 hours when next accessed; use `/cancel` to delete one immediately.

## Telegram file-size limit

The standard Telegram Bot API `getFile` method currently supports downloading files up to 20 MB. The Worker enforces a 20,000,000-byte cap. Send videos as Telegram videos or as video documents in MP4, MOV, MKV, or WEBM formats. Larger files will need a Telegram Mini App that uploads directly to R2; that is a separate future enhancement.

## Deploy

### 1. Create a separate Telegram bot

In Telegram, open [@BotFather](https://t.me/BotFather), run `/newbot`, and follow the steps. Copy the bot token. Do **not** change the webhook for your existing clipper bot.

### 2. Get your Telegram numeric user ID

After deployment and webhook setup, send `/whoami` to the new bot. It returns your numeric Telegram user ID. Add only your ID to the Worker allowlist. The `/whoami` command is intentionally available before authorisation so the owner can bootstrap the allowlist; no uploads or catalogue changes are allowed before authorisation.

### 3. Deploy the Worker

From the repository root, using Wrangler with access to the Cloudflare account:

```bash
npx wrangler login
npx wrangler deploy --config workers/broll-uploader/wrangler.toml
```

The Wrangler config binds the Worker to the existing `telegram-video-clipper-uploads` R2 bucket as `BROLL_BUCKET`. The Cloudflare account used for deployment must have access to that bucket.

### 4. Add Worker secrets

Run these commands and enter each value when prompted:

```bash
npx wrangler secret put TELEGRAM_BOT_TOKEN --config workers/broll-uploader/wrangler.toml
npx wrangler secret put TELEGRAM_WEBHOOK_SECRET --config workers/broll-uploader/wrangler.toml
npx wrangler secret put ALLOWED_TELEGRAM_USER_IDS --config workers/broll-uploader/wrangler.toml
```

- `TELEGRAM_BOT_TOKEN`: the token from the **new** BotFather bot.
- `TELEGRAM_WEBHOOK_SECRET`: a long random string containing only letters, numbers, underscores, and hyphens. Telegram accepts 1–256 characters.
- `ALLOWED_TELEGRAM_USER_IDS`: comma-separated numeric Telegram user IDs. Start with only your own ID.

After setting secrets, deploy again:

```bash
npx wrangler deploy --config workers/broll-uploader/wrangler.toml
```

### 5. Register the webhook

Replace the placeholders with the deployed Worker URL, bot token, and the exact webhook secret configured above:

```bash
curl -X POST "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/setWebhook" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://telegram-broll-uploader.<YOUR_SUBDOMAIN>.workers.dev/telegram/webhook","secret_token":"<TELEGRAM_WEBHOOK_SECRET>","allowed_updates":["message","callback_query"]}'
```

Telegram should respond with `{"ok":true,"result":true,...}`. Confirm the Worker health endpoint returns JSON:

```
https://telegram-broll-uploader.<YOUR_SUBDOMAIN>.workers.dev/health
```

Then open the new bot, send `/whoami`, add the returned ID to `ALLOWED_TELEGRAM_USER_IDS`, and redeploy the Worker.

## Bot commands

- `/start` or `/help`: instructions.
- `/whoami`: numeric Telegram ID.
- `/status`: published catalogue count and pending upload status.
- `/cancel`: delete the current staged video and pending metadata.

## R2 layout

```
broll/
  catalog.json
  uploads/
    br-<id>.mp4
  _pending/
    <telegram-user-id>.json
```

The published catalogue format is compatible with `video_editor.engine.parse_catalog`.

## Security and rights

- The webhook validates Telegram's `X-Telegram-Bot-Api-Secret-Token` header.
- Only allowlisted Telegram IDs can upload, publish, cancel, or inspect the catalogue.
- The bot only responds in private chats.
- Uploads remain unindexed until the owner confirms the licence/permission.
- The owner confirmation is an explicit assertion, not automated legal verification. Check each asset's licence and permitted commercial/derivative uses yourself.
- This Worker never calls, edits, or dispatches the original clipper repository or workflow.
