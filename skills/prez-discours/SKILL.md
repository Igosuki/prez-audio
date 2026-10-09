---
name: prez-discours
description: Write and review the spoken script (discours) of a frozen prez project, slide by slide. Use when the user says "écris le discours", "rédige le texte à dire", "relis le discours", "commentaires sur la page de review", "valide le discours", "write the narration", "write the script for each slide", "review the discours", or after prez-nouveau. Covers drafting, the claude.ai review page, applying comments, and validation.
---

# prez-discours

Write `discours/slide-NN.md` for every slide, have the user review it on a claude.ai page, apply their comments, validate only on their explicit approval. Done when `prez status` shows every slide validated.

Rule: fix a sentence in the discours, never in the audio or video.

## 1. Draft

```
prez status
prez discours init
```

For each slide, in order: read `slides/slide-NN.png`, then the matching part of `sources/transcript.md` and `sources/notes.md`, then write `discours/slide-NN.md`.

Writing for the ear:

- The speaker's own voice. Reuse their images, anecdotes and turns of phrase from the transcript; do not paraphrase them into neutral prose.
- Short sentences. One idea each. Punctuation carries the pauses: commas, full stops, ellipses.
- Write numbers and acronyms as they are spoken: "quarante-deux pour cent", "S.N.C.F." or the spoken form the speaker uses, "deux mille vingt-six".
- No Markdown except the first line `# Title` (chapter title, not spoken). No lists, bold, links, parentheses, emoji.
- Every figure comes from `sources/`. Check each against its source file. A figure with no source is left out and listed in your message to the user.
- Target duration per slide: about 150 words per minute. Take the target from the transcript's timing for that slide when known; otherwise 20 to 60 seconds. Report the word count of slides far above or below.
- Slide that carries only an image or a transition: one sentence, or an empty body (shown silently).
- Authoring notes go in `<!-- comments -->`; they are not spoken.

Check the spoken text of a slide with `prez discours text NN`. Done with the draft when every `discours/slide-NN.md` exists and every number has been checked.

## 2. Review page

```
prez review-page
```

Publish `out/review.html` with the Artifact tool and keep the returned URL. If the Artifact tool is unavailable, give the local path instead.

## Stop point: user reviews

Tell the user: "Comment the discours card by card on the page. Say 'done' (or 'fini') when you are done." Wait.

## 3. Apply comments

Read the comments with the ArtifactComments tool. For each comment:

1. Find the slide from the card anchor (`slide-NN`).
2. Edit only that `discours/slide-NN.md`. Keep the speaker's voice.
3. Reply to the comment with what changed.

Then `prez review-page` and republish to the same artifact URL (pass `url`), so the user sees the new text. Repeat from the stop point until the user has no more comments.

## 4. Validate

Only when the user explicitly approves ("c'est validé", "validate all"). Ambiguous replies ("ok", "continue") are not approval: ask "Validate all N slides?" and wait.

```
prez validate all
prez status
```

Validate only the slides the user approved if their approval is partial (`prez validate 1-12`). Never validate on the user's behalf, and never because comments ran out. Editing a validated slide makes it stale; it needs the user's approval again.

Then continue with **prez-production**.
