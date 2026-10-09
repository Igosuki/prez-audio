# prez-audio — design

Turns a slide deck into a narrated video. Claude Code drives it through three skills; a Python
engine (`prez` CLI) does the deterministic work. A human intervenes at two points only: validating
the script (*discours*) and choosing the voice by ear.

```
deck URL/file ──► 1. freeze ──► 2. discours ──► 3. voice ──► 4. montage ──► video.mp4 + player.html
                  (prez-nouveau)  (prez-discours)   (prez-production ─────────────────►)
                                   ▲ human validates  ▲ human picks 1 of 4 auditions
```

Core rule: **fix a sentence in the discours, never in the video.** Every artifact carries a
fingerprint of its inputs; the engine only regenerates what is stale.

## Split of responsibilities

| Who | Does | Never does |
|---|---|---|
| `prez` engine (Python) | freeze deck, keep state, call TTS/STT APIs, ffmpeg, build HTML pages | talk to claude.ai, write prose |
| Skills (Claude Code) | write the discours from transcript + notes + slide images, publish review page / player as claude.ai artifacts, read comments, apply edits, run `prez` | edit audio/video by hand, bypass guardrails |
| Human | validate discours, pick voice | — |

## Project layout (one directory per video, meant to live in a git repo)

```
<project>/
  prez.toml                 config (committed)
  sources/                  transcript.md, notes.md, figures… (committed, user inputs)
  deck/                     frozen copy of the deck source if HTML (committed)
  slides/slide-01.png …     one 1920x1080 PNG per kept slide (committed)
  slides/manifest.json      [{"n":1,"title":"…","sha256":"…","file":"slide-01.png"}] (committed)
  discours/slide-01.md …    spoken text, one file per slide — source of truth (committed)
  state/validated.json      {"01": "<text_fp>", …} (committed)
  state/voice.json          chosen voice settings provenance (committed)
  audition/                 variant-N.mp3, audition.html (ignored)
  audio/slide-01.mp3 + .json  TTS output + meta {audio_fp, text_fp, duration, alignment} (ignored)
  build/clips/slide-01.mp4 + .json  per-slide clip + meta {clip_fp, duration} (ignored)
  out/<slug>.mp4 .srt .vtt chapters.json player.html review.html (ignored)
```

Slide numbers are 1-based, zero-padded to 2 digits in filenames and state keys (`01`); 3 digits if
the deck has ≥ 100 slides. CLI slide selectors accept `3`, `3,5`, `3-7`, `all`.

## prez.toml

```toml
title = "Reboot with AI"
slug = "reboot-with-ai"
lang = "fr"                       # BCP-47-ish, passed to TTS/STT

[deck]
source = "https://example.com/deck.html"   # URL, .html path, .pdf path, or directory of images
exclude = [40, 41, 42]            # appendix slides dropped at freeze time (source numbering)
selector = ""                     # optional CSS selector for slide elements
width = 1920
height = 1080

[voice]
backend = "elevenlabs"            # "elevenlabs" | "fake" (offline, for tests/demo)
voice_id = ""
model_id = "eleven_multilingual_v2"
stability = 0.5
similarity_boost = 0.75
style = 0.0
speed = 1.0
use_speaker_boost = true

[render]
lead_in = 0.4                     # seconds of silence before each slide's speech
tail = 0.8                        # seconds after
loudness = -16.0                  # integrated LUFS target (loudnorm)
burn_subtitles = false            # true: hard-sub; false: soft mov_text track only
crf = 20
```

The ElevenLabs API key is never stored in the project: `ELEVENLABS_API_KEY` env var, else the OS
keyring (`keyring.get_password("prez-audio", "elevenlabs")`). `prez key set`
stores it in the keyring; `prez key check` reports where a key was found without printing it.

## Fingerprints and guardrails

- `text_fp(n)` = sha256 of the normalized TTS text of `discours/slide-NN.md` (see below).
- `audio_fp(n)` = sha256(text_fp ‖ canonical JSON of voice params: backend, voice_id, model_id,
  stability, similarity_boost, style, speed, use_speaker_boost, lang).
  Neighbor slides are **not** part of it: editing slide 7 re-voices slide 7 only.
- `clip_fp(n)` = sha256(audio_fp ‖ mp3 sha256 ‖ slide image sha256 ‖ render params), so re-voicing the same text with `--force` still rebuilds the clip.

Guardrails, enforced in the engine (raise `GuardError`, CLI exits 2 with a clear message):
1. **No voice without validated text** — `synth` refuses slide n unless `text_fp(n) == validated[n]`.
2. **No video with a stale voice** — `render` refuses unless every slide's audio meta has
   `audio_fp == expected audio_fp(n)`.
3. **Only modified slides are redone** — `synth`/`render` skip slides whose fingerprint matches;
   `--force` overrides.
4. **The deck reads no local file** — at freeze time the browser aborts any `file://` request
   outside the deck's own directory, and the deck is loaded from a frozen local copy when possible.
5. **Only Claude talks to claude.ai** — the engine has no claude.ai code path; publishing is done
   by the skills through Claude Code's Artifact tool.

## Discours file format

Plain Markdown. TTS text = file content with HTML comments (`<!-- … -->`) removed (authoring
notes), Markdown emphasis markers stripped, whitespace collapsed. A first-line `# Title` heading,
if present, is not spoken (used as chapter title, overriding the manifest title). Empty text →
slide is shown silently for `lead_in + tail + 2s`.

## CLI surface (`prez`, click-based, run from inside a project dir or with `-C <dir>`)

```
prez new <dir> --deck <src> [--title T] [--lang fr] [--backend elevenlabs|fake]
                                         scaffold project, prez.toml, .gitignore, sources/
prez freeze [--exclude 40-42]            deck → slides/*.png + manifest.json
prez status [--json]                     table: slide, title, discours?, validated?, audio fresh?, clip fresh?
prez discours init                       create missing discours/slide-NN.md stubs ("# <title>\n\n")
prez discours text <sel>                 print normalized TTS text (debug)
prez validate <sel>                      record text_fp as validated
prez review-page [-o out/review.html]    self-contained HTML: one card per slide (embedded JPEG
                                         thumbnail ≤ 960px wide, discours text, status badge,
                                         id="slide-NN" anchor) — the page Claude publishes for comments
prez audition [--slide 1] [--variants N]  render N (default 4) voice-setting variants of one slide →
                                         audition/variant-K.mp3 + audition.html (side by side players,
                                         settings shown); does not require validation
prez voice show | set k=v… | choose K    inspect / edit [voice] / adopt audition variant K
prez synth [<sel>] [--force]             TTS for stale slides (guard 1), with previous_text/next_text
                                         context for intonation continuity
prez render [--force] [--burn-subs]      guard 2; clips for stale slides; concat; subtitles; chapters
prez player [-o out/player.html]         HTML player: video, chapter list, current sentence highlighted,
                                         speed buttons ×1 ×1.25 ×1.5 ×2 ×3 ×5 ×10
prez verify [<sel>] [--threshold 0.15]   STT each slide's audio, word error rate vs text; reports
                                         SKIPPED (not passed) when the API refuses
prez key set                             store ElevenLabs key in OS keyring
```

## Modules (`src/prezaudio/`)

| Module | Contents |
|---|---|
| `project.py` | `Project` (load/save prez.toml with tomlkit, paths, slide numbering, selector parsing, state files) |
| `fingerprint.py` | `text_fp`, `audio_fp`, `clip_fp`, `file_sha256` |
| `discours.py` | parse/normalize discours, sentence split, stubs |
| `guards.py` | `GuardError`, `require_validated`, `require_fresh_audio` |
| `deck.py` | `freeze(source, out_dir, *, width, height, exclude, selector) -> list[SlideInfo]` |
| `tts/base.py` | `TTSBackend` protocol: `synthesize(text, settings, *, previous_text, next_text, lang) -> TTSResult(audio_bytes mp3, alignment: list[WordTiming], duration)` |
| `tts/elevenlabs.py` | `/v1/text-to-speech/{voice_id}/with-timestamps`, char alignment → word timings; STT `/v1/speech-to-text` |
| `tts/fake.py` | offline: quiet tone via ffmpeg, ~0.35 s/word, evenly spread word timings |
| `subtitles.py` | word timings + clip offsets → cues (≤ 2 lines × 42 chars, ≤ 6 s, sentence-aware) → SRT, VTT |
| `montage.py` | ffmpeg: per-slide clip (still image + delayed/padded/loudnormed audio, libx264 stillimage, yuv420p, aac 48 kHz), concat demuxer `-c copy`, mux soft subs (mov_text) + MP4 chapters via ffmetadata, optional burn-in |
| `pages.py` | review page, audition page, player page (stdlib string templates, no JS deps) |
| `verify.py` | normalized WER, report |
| `cli.py` | click commands above |

## Deck freezing (`deck.py`)

Source kinds:
- **HTML (URL or file)** — Playwright Chromium, viewport `width×height`, `device_scale_factor=1`.
  URL sources are first downloaded to `deck/` (page HTML only; assets stay remote) when they are
  single-file; otherwise loaded directly. Slide detection, in order:
  1. `selector` if configured: each matching element, made the only visible one, screenshotted.
  2. reveal.js (`window.Reveal`): iterate `Reveal.getSlides()` via `Reveal.slide(h, v)`.
  3. Known patterns: `section.slide`, `.slide`, `[data-slide]`.
  4. Fallback: keyboard navigation — screenshot, press `ArrowRight`, wait for settle, stop when two
     consecutive screenshots are identical (cap 500).
  Fonts/animations: wait for `document.fonts.ready` and 300 ms settle per slide; inject CSS
  disabling transitions/animations.
- **PDF** — `pdftoppm -r` to 1920 px wide PNGs (poppler); letterbox to `width×height`.
- **Directory of images** — sorted, letterboxed via ffmpeg.

`exclude` drops source slides before renumbering; the manifest records `source_n` too. Titles come
from the first `h1/h2` in the slide element (HTML), else `Slide N`.

## Tests

pytest; `tests/` mirrors modules. Real ffmpeg on tiny inputs (64×36, <1 s); Playwright tests skip
when Chromium is unavailable; ElevenLabs HTTP mocked with `respx`. End-to-end test: `prez new` with
the fake backend on `examples/demo-deck/` → freeze → discours → validate → synth → render → player,
then edit one slide and assert only that slide is re-synthesized and re-clipped.
