# Telegram Video Editor

A **separate post-processing system** for finished clips produced by Chopify. It turns an already-rendered MP4 into a more deliberate short-form edit using local AI, licensed/owned B-roll, readable captions, and FFmpeg.

> **Isolation rule:** this repository does not modify, call, dispatch, or depend on `Dopeteck/telegram-video-clipper` workflows. The clipper remains the source of the original clips. This editor reads a completed R2 object and writes a new file under `edited/`; it never overwrites the original.

## First version

- Manual GitHub Actions trigger; no automatic coupling to the clipper.
- Reads a completed clip from the existing private R2 bucket.
- Transcribes the short clip locally with Faster-Whisper (no paid transcription API).
- Uses local Ollama model `qwen3:4b-instruct-2507-q4_K_M` to plan edits even when the B-roll library is empty.
- Selects B-roll **only** from an asset catalogue whose entries record a source, licence, and `rights_verified: true`.
- Uses B-roll only when its catalogue description/tags match at least two distinctive concepts in an exact, time-aligned transcript quote. Landscape footage is shown in full over a blurred background instead of being center-cropped. Automatic zooms are limited to one subtle emphasis move; a weak visual cue is rejected.
- Detects likely burned-in subtitles with Tesseract OCR. In `auto` mode it preserves existing captions and adds new ones only when no existing text is detected; uncertain OCR fails safe and preserves the source.
- When captions are needed, uses Faster-Whisper word timestamps to render short karaoke-style caption beats with an active-word highlight and safe frame margins. It also normalises dialogue loudness and uploads a separate MP4 plus an edit-plan JSON manifest.
- Delivers the edited result into Telegram automatically when Telegram secrets are configured.

If the B-roll catalogue is missing or empty, the local AI planner still chooses useful punch-ins and transcript-grounded callouts. It does not invent footage or download random stock video. In the manual workflow, `caption_mode` defaults to `auto`; `preserve` never adds subtitles, and `force` is only for a source known to have no burned-in captions.

## Workflow

1. A clip is produced by the existing clipper and uploaded to R2 under `jobs/<job_id>/<filename>.mp4`.
2. Open **Actions → Edit finished clip → Run workflow** in this repository.
3. Enter the exact R2 object key in `clip_key`.
4. The editor transcribes, plans, renders, and writes a new result to `edited/<workflow_run_id>/`.
5. The local AI planner proposes contextual B-roll, emphasis punch-ins, and exact-phrase callouts; deterministic validation rejects unsafe timestamps, invented B-roll IDs, callout text absent from the transcript, and callouts timed over the wrong spoken phrase.
6. The renderer checks for source subtitles before adding any new captions, applies the selected visual edits, and normalises dialogue loudness.
7. If Telegram secrets are configured, the editor sends the MP4 to your Telegram chat. Files within 49 MB are sent as native Telegram videos. Larger files arrive as a private download button with a 24-hour signed R2 URL.
8. The original clip remains unchanged.

## Repository secrets

Add these secrets in **Settings → Secrets and variables → Actions**:

**R2 access**
- `R2_ACCOUNT_ID`
- `R2_ACCESS_KEY_ID`
- `R2_SECRET_ACCESS_KEY`

**Telegram delivery**
- `TELEGRAM_BOT_TOKEN`: the token for your existing Telegram bot (the same bot configured in your Cloudflare Worker).
- `TELEGRAM_CHAT_ID`: your private Telegram chat ID, or an authorized channel/group ID.

For delivery to a private chat, open your bot and send `/start` once so Telegram permits the bot to message you. If Telegram secrets are not configured, the edit still completes and remains in R2, but no Telegram message is sent.

The R2 credentials need permission to read completed clips and B-roll assets, and write under the `edited/` prefix. Do not paste credentials into workflow inputs, code, issues, or chat.

## B-roll catalogue

Upload a JSON catalogue to R2 at `broll/catalog.json`. Start from [broll/catalog.example.json](broll/catalog.example.json). Each entry needs a unique ID, a key under `broll/`, a description, tags, a source URL, a licence note, and `rights_verified: true` only after you have checked the actual usage rights. Upload the matching video file to the specified key.

## Local development

Python 3.11+, FFmpeg (with the `subtitles` filter), and Ollama are required for a full render.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
ollama pull qwen3:4b-instruct-2507-q4_K_M
pytest
```

The R2 workflow is manual-only. No clip-processing run is started by a push or pull request.

## Quality roadmap

1. **Current quality pass:** evidence-backed B-roll selection, full-frame B-roll composition with a blurred fill, fewer automatic punch-ins, transcript-grounded callout graphics, subtitle detection/preservation, safe caption wrapping, and dialogue loudness normalisation.
2. **Next visual pass:** face-aware portrait reframing, scene-aware cut timing, smoother transitions, and a preview/approval step.
3. Curated rights-cleared B-roll and optional licensed sound effects/music.
4. Review UX: compare original and edited versions, approve/reject before publishing.
5. Optional automation only after repeatable quality reviews; the clipper stays unchanged.

Editing can improve clarity, retention, and originality, but cannot guarantee recommendation reach or monetization. The source clip and every added asset still need appropriate rights and meaningful editorial value.
