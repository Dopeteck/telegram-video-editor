# Telegram Video Editor

A **separate post-processing system** for finished clips produced by Chopify. It turns an already-rendered MP4 into a more deliberate short-form edit using local AI, licensed/owned B-roll, readable captions, and FFmpeg.

> **Isolation rule:** this repository does not modify, call, dispatch, or depend on `Dopeteck/telegram-video-clipper` workflows. The clipper remains the source of the original clips. This editor reads a completed R2 object and writes a new file under `edited/`; it never overwrites the original.

## First version

- Manual GitHub Actions trigger; no automatic coupling to the clipper.
- Reads a completed clip from the existing private R2 bucket.
- Transcribes the short clip locally with Faster-Whisper (no paid transcription API).
- Uses local Ollama model `qwen3:4b-instruct-2507-q4_K_M` to propose an edit plan.
- Selects B-roll **only** from an asset catalogue whose entries record a source, licence, and `rights_verified: true`.
- Uses FFmpeg to insert relevant full-frame B-roll cutaways while preserving the clip's original dialogue/audio.
- Burns in clean captions and uploads a separate MP4 plus an edit-plan JSON manifest.

The editor skips B-roll when no suitable licensed/owned asset is available. If the catalogue is missing or empty, it still produces a captioned edit with the original audio; it will not download random footage from the internet or invent asset URLs.

## Workflow

1. A clip is produced by the existing clipper and uploaded to R2 under `jobs/<job_id>/<filename>.mp4`.
2. Open **Actions → Edit finished clip → Run workflow** in this repository.
3. Enter the exact R2 object key in `clip_key`.
4. The editor transcribes, plans, renders, and writes a new result to `edited/<workflow_run_id>/`.
5. Review the rendered MP4 before publishing. The original object remains unchanged.

## Repository secrets

Add these three secrets in **Settings → Secrets and variables → Actions**:

- `R2_ACCOUNT_ID`
- `R2_ACCESS_KEY_ID`
- `R2_SECRET_ACCESS_KEY`

The workflow uses the existing bucket name `telegram-video-clipper-uploads`. The R2 credentials need permission to read completed clips and B-roll assets, and write under the `edited/` prefix. Do not paste credentials into workflow inputs, code, issues, or chat.

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

## Roadmap

1. Foundation: safe R2 input/output, local transcription, strict edit-plan validation, B-roll cutaways, captions, tests.
2. Visual polish: animated word emphasis, controlled punch-ins, smoother transitions, optional licensed sound effects/music.
3. Curated B-roll library with usage-rights records.
4. Review UX: compare original and edited versions, approve/reject before publishing.
5. Optional automation only after quality is proven; the clipper stays unchanged.

Editing can improve clarity, retention, and originality, but cannot guarantee recommendation reach or monetization. The source clip and every added asset still need appropriate rights and meaningful editorial value.
