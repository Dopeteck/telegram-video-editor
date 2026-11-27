# R2 setup

The workflow uses the existing private bucket `telegram-video-clipper-uploads`, but is independent of the clipper workflow.

## Secrets

In this repository, add:
- `R2_ACCOUNT_ID`
- `R2_ACCESS_KEY_ID`
- `R2_SECRET_ACCESS_KEY`

Use an R2 API token with object read/write access limited to this bucket where possible. The editor needs to read `jobs/*` and `broll/*`, and write `edited/*`. Do not grant broader account permissions than necessary.

## Input key

Completed clips are stored in this format:

```text
jobs/<job_id>/<clip-filename>.mp4
```

Use the exact key shown in the clipper output/Worker response, not a public URL.

## B-roll library

Upload the JSON catalogue to `broll/catalog.json` and upload each video to its matching `broll/... ` key. See `broll/catalog.example.json`.

Use only footage you own, have licensed, or otherwise have a documented legal basis to use. Keep source/creator and licence details in the catalogue.

## Output

```text
edited/<workflow_run_id>/<original-filename>.edited.mp4
edited/<workflow_run_id>/edit-plan.json
```

The original `jobs/...` object is never overwritten.
