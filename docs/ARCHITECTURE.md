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
        +--> Local Ollama editorial plan (runs even without B-roll)
                    |
                    v
       Validate B-roll, punch-ins, and transcript-exact callouts
                    |
          +---------+----------------------+
          |                                |
          v                                v
 Download selected, cleared assets   OCR source for burned-in captions
          |                                |
          +---------------+----------------+
                          |
                          v
           FFmpeg visual edit + loudness pass
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
- Punch-ins are restricted to modest zoom values and bounded durations. Callouts must be exact contiguous phrases from the transcript.
- The default caption policy uses OCR to detect existing burned-in text. If OCR is unavailable or uncertain, it preserves the source rather than risking duplicate captions.
- B-roll audio is never mixed into the output. Dialogue stays on the original audio stream and receives loudness normalisation.
- The model produces a plan; deterministic validation and FFmpeg perform the render.

## Render policy

The editor preserves the source clip's frame dimensions and dialogue. B-roll is fitted to the frame as a cutaway; modest punch-ins and exact-transcript callouts can still be applied when the B-roll library is empty. New captions are generated only when the selected caption policy allows them and the default OCR check does not find existing burned-in text. If there is no relevant cleared footage, the planner must not insert unrelated filler footage.
