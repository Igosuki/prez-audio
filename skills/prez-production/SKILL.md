---
name: prez-production
description: Produce the narrated video from a validated prez project, and handle later edits. Use when the user says "lance la production", "choisis la voix", "génère la voix", "monte la vidéo", "fais le rendu", "corrige une phrase dans la vidéo", "produce the video", "audition the voices", "render", "re-render", "change the voice settings", "fix a sentence in the video", or after prez-discours validation. Covers voice audition, synth, verify, render, player, and the edit loop.
---

# prez-production

Voice, video and player from validated text. Done when `out/<slug>.mp4` and `out/player.html` exist and `prez status` shows every audio and clip fresh.

The engine refuses to voice unvalidated text and to render with a stale voice. If a command exits with code 2, read its message and fix the cause (usually `prez-discours` validation); do not look for a way around it.

## 1. Audition the voice

`prez status` first. The audition makes 4 ElevenLabs generations; tell the user and wait for their OK, then:

```
prez audition --slide 1 --variants 4
```

Publish `audition/audition.html` with the Artifact tool, or give its local path. Each variant voices slide 1 with different settings.

### Stop point: the user chooses by ear

Ask which variant they prefer. Do not pick for them, even if asked which is "best"; describe the settings differences if they ask. Then:

```
prez voice choose K
```

## 2. Synthesize

Tell the user this calls ElevenLabs and costs credits, with the slide count. After they agree:

```
prez synth
```

Never pass `--force` unless the user asks: it re-voices every slide and spends credits.

If it refuses slides, they are not validated: go back to **prez-discours**.

## 3. Verify

```
prez verify
```

Report the result exactly. If it prints SKIPPED (for example the API key lacks speech-to-text permission), say the automatic check did not run and that nothing was verified; offer to have the user listen to the slides that matter most. Never describe SKIPPED as passed. On a mismatch above the threshold, show the slide and the differing words, and propose a discours edit (see the edit loop).

## 4. Render and player

Rendering is long on a big deck (up to about 1h30 for a full deck). Keep the machine awake:

- Linux: `systemd-inhibit --what=sleep prez render`
- macOS: `caffeinate -i prez render`

Run it with Bash `run_in_background` and check `prez status` for completion. Ask before using `--burn-subs`. Then:

```
prez player
```

Optionally publish `out/player.html` as a claude.ai artifact. Give the user the paths of `out/<slug>.mp4`, `.srt`, `.vtt` and `player.html`.

Offer the project directory too (discours, state, config: the machine) and suggest committing it to git; generated folders are ignored.

## Edit loop

The user wants a sentence changed, in the discours or the video:

1. Edit `discours/slide-NN.md` only. Never touch audio or video.
2. Show the user the new text and ask for approval. Stop until they give it.
3. `prez validate NN`
4. `prez status`: only that slide should be stale. If more slides are stale, stop and ask the user before `prez synth`.
5. `prez synth`, `prez verify NN`, `prez render` (long-render advice above applies), `prez player`.

Check `prez status` again afterwards: exactly the edited slides were redone, everything else untouched. If more slides were redone, tell the user why (voice settings or slide image changed).
