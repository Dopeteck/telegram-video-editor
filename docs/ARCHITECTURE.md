# Architecture and safety boundaries

```text
Existing clipper (unchanged)
        |
        | finished MP4 already stored in R2
        v
Manual workflow_dispatch in telegram-video-editor
        |
        v
Download one exact R2 object key
        |
        +--> Faster-Whisper transcript with timestamps
        |
        +--> Read B-roll catalogue (metadata only)
                    |
                    v
              Local Ollama edit planner
                    |
                    v
          Validate cues against the catalogue
                    |
                    v
        Download only selected, rights-cleared assets
                    |
                    v
           FFmpeg render + original audio
                    |
                    v
       Upload to edited/<run_id>/ + plan manifest
```

## Hard boundaries

- No GitHub API call, dispatch event, webhook, branch write, or code change targets the clipper repository.
- The source MP4 is read-only. Output is always below `edited/`; the original key is never reused.
- B-roll must come from the user's own/cleared R2 library. No automatic stock-site scraping.
- An asset is eligible only when its catalogue row has a source URL, licence note, and `rights_verified: true`.
- AI-generated asset IDs not present in the catalogue are rejected. Invalid, out-of-range, overlapping cues are rejected.
- B-roll audio is never mixed into the output. The original clip's audio remains the audio track.
- The model produces a plan; deterministic validation and FFmpeg perform the render.

## Render policy

The editor preserves the source clip's frame dimensions and audio. B-roll is fitted to the frame as a cutaway. Styled captions are generated from the fresh transcript. If there is no relevant cleared footage, the output uses captions without unrelated filler footage.
