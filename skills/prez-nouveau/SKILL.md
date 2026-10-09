---
name: prez-nouveau
description: Start a narrated-video project from a slide deck with the prez engine. Use when the user gives a deck (URL, claude.ai artifact link, HTML, PDF, image folder) and wants it narrated or turned into a video, or says "nouvelle présentation", "nouveau prez", "mets ma présentation en vidéo", "new prez project", "narrate my deck", "freeze the deck". Creates the project, gathers sources, freezes slides to PNG.
---

# prez-nouveau

Create the project and freeze the deck. Done when `prez status` lists every kept slide and the user has confirmed the count.

Run `prez` from the project directory, or pass `-C <dir>`.

## 0. Engine installed

Run `command -v prez`. If missing, ask the user before installing, then:

```
uv tool install git+https://github.com/Igosuki/prez-audio
uv tool run --from git+https://github.com/Igosuki/prez-audio playwright install chromium
```

`ffmpeg` must be on PATH (and `pdftoppm` for PDF decks); if not, tell the user and stop.

## 1. Get the deck as a local file

- claude.ai artifact link: the engine never talks to claude.ai. Create the project first (step 2) with `--deck <the artifact link>`, then fetch the page with the Artifact tool (`action: "read"`), save the HTML to `<dir>/deck/index.html`, write the link to `<dir>/deck/SOURCE.txt`, and set `deck.source = "deck/index.html"` in `prez.toml` (relative paths resolve against the project directory).
- Other URL, `.html`, `.pdf`, or image directory: pass it to `--deck` as is.

## 2. Create the project

Ask the user for: title, language (default `fr`), project directory.

```
prez new <dir> --deck <source> --title "<title>" --lang <lang>
```

If `<dir>` exists and is not empty, stop and ask before running.

## 3. Gather sources into `sources/`

Ask for each, and copy what the user provides:

- `transcript.md`: the talk as spoken (the most important input; the discours reuses its voice and anecdotes).
- `notes.md`: speaker notes.
- Figures and reference files (reports, spreadsheets) that the deck's numbers come from.

Missing transcript or notes: say so and continue only if the user confirms.

## 4. Voice and key

- Ask for the ElevenLabs `voice_id` of the user's cloned voice and set it with `prez voice set voice_id=<id>`. No voice yet: leave it and tell the user it is needed before `prez-production`.
- Key: run `prez key check` (env var or OS keyring; it never prints the key). If neither, stop and ask the user to run `! prez key set` themselves; the key goes to the OS keyring. Never write the key into any file and never echo it.
- Offline trial without a voice: `--backend fake` on `prez new`.

## 5. Freeze

Ask which slides are appendix (backup slides after the conclusion). Then:

```
prez freeze --exclude <e.g. 40-42>
prez status
```

Open two or three of `slides/slide-NN.png` to check they look right (full slide, fonts loaded, no half-played animation). If frozen slides are wrong, set `deck.selector` in `prez.toml` or adjust `exclude`, and freeze again.

## Stop point

Show the `prez status` table and the slide count. Ask: "N slides, is that the right set?" Wait for the answer. Then continue with **prez-discours**.
