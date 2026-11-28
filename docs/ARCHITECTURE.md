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
- AI-generated asset IDs not present in the catalogue are rejected. B-roll also needs an exact spoken evidence quote at the proposed time and at least two distinctive concept matches against the asset description/tags; irrelevant or weak matches are rejected. Invalid, out-of-range, and overlapping cues are rejected.
- Automatic punch-ins are limited to one subtle move per clip so zooms do not substitute for editorial choices. Callouts must be exact contiguous phrases from the transcript and their on-screen time must overlap the matching spoken words.
- The default caption policy uses OCR to detect existing burned-in text. A negative detection is only trusted after enough frames are successfully sampled; if OCR is unavailable or uncertain, the source is preserved rather than risking duplicate captions.
- New captions use Faster-Whisper word timestamps in short karaoke-style beats with active-word highlighting, and are rendered only when the caption policy allows them.
- B-roll audio is never mixed into the output. Dialogue stays on the original audio stream and receives loudness normalisation.
- The model produces a plan; deterministic validation and FFmpeg perform the render.

## Render policy

The editor preserves the source clip's frame dimensions and dialogue. B-roll's full original frame is contain-scaled over a blurred, darkened fill so the subject is not cut off; it is inserted only when transcript evidence and catalogue metadata agree. At most one subtle punch-in is allowed, and exact-transcript callouts can still be applied when the B-roll library is empty. New captions are generated only when the selected caption policy allows them and the default OCR check does not find existing burned-in text. If there is no relevant cleared footage, the planner must not insert unrelated filler footage.
