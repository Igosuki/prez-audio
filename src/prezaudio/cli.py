import dataclasses
import json
import os
import subprocess
import tomllib
from pathlib import Path

import click

from prezaudio import discours as disc
from prezaudio import montage, pages, voice
from prezaudio.project import PrezError, Project, parse_ints, read_json, write_json
from prezaudio.status import slide_status
from prezaudio.tts.elevenlabs import (ElevenLabsClient, ElevenLabsPermissionError, get_api_key)
from prezaudio.verify import VerifyRow, wer, word_diff

PROJECT_TOML = """\
title = "{title}"
slug = "{slug}"
lang = "{lang}"

[deck]
source = "{source}"
exclude = []
selector = ""
width = 1920
height = 1080

[voice]
backend = "{backend}"
voice_id = ""
model_id = "eleven_multilingual_v2"
stability = 0.5
similarity_boost = 0.75
style = 0.0
speed = 1.0
use_speaker_boost = true

[render]
lead_in = 0.4
tail = 0.8
loudness = -16.0
burn_subtitles = false
crf = 20
"""
VOICE_KEYS = {"backend", "voice_id", "model_id", "stability", "similarity_boost", "style",
              "speed", "use_speaker_boost"}
STRING_KEYS = {"backend", "voice_id", "model_id"}
EXIT_SKIPPED = 3


class PrezGroup(click.Group):
    def invoke(self, ctx):
        try:
            return super().invoke(ctx)
        except PrezError as e:
            click.echo(f"error: {e}", err=True)
            ctx.exit(2)


def proj(ctx: click.Context) -> Project:
    return Project(ctx.obj)


@click.group(cls=PrezGroup)
@click.option("-C", "--directory", default=".", type=click.Path(file_okay=False),
              help="Project directory.")
@click.pass_context
def cli(ctx, directory):
    ctx.obj = directory


def _slug(title: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "video"


@cli.command()
@click.argument("directory", type=click.Path(file_okay=False))
@click.option("--deck", "source", required=True, help="URL, .html/.pdf path, or image directory.")
@click.option("--title", default=None)
@click.option("--lang", default="fr", show_default=True)
@click.option("--backend", type=click.Choice(["elevenlabs", "kokoro", "fake"]), default="elevenlabs",
              show_default=True)
@click.option("--force", is_flag=True, help="Allow a non-empty existing directory.")
def new(directory, source, title, lang, backend, force):
    """Scaffold a project directory."""
    root = Path(directory)
    if (root / "prez.toml").exists():
        raise PrezError(f"{root / 'prez.toml'} already exists")
    if root.exists() and any(root.iterdir()) and not force:
        raise PrezError(f"{root} is not empty (use --force)")
    if "://" not in source and Path(source).exists():
        source = str(Path(source).resolve())
    title = title or root.resolve().name.replace("-", " ").replace("_", " ").title()
    root.mkdir(parents=True, exist_ok=True)
    (root / "sources").mkdir(exist_ok=True)
    (root / "prez.toml").write_text(PROJECT_TOML.format(
        title=title.replace('"', '\\"'), slug=_slug(title), lang=lang,
        source=source.replace("\\", "\\\\").replace('"', '\\"'), backend=backend))
    (root / ".gitignore").write_text("audition/\naudio/\nbuild/\nout/\n")
    click.echo(f"created {root}")


@cli.command()
@click.option("--exclude", default=None, help="Source slide numbers to drop, e.g. 40-42.")
@click.pass_context
def freeze(ctx, exclude):
    """Freeze the deck into slides/*.png and manifest.json."""
    from prezaudio.deck import freeze as do_freeze
    p = proj(ctx)
    if exclude is not None:
        p.set_value("deck", "exclude", parse_ints(exclude))
        p.save()
    d = p.deck
    if not d["source"]:
        raise PrezError("deck.source is empty in prez.toml")
    p.slides_dir.mkdir(exist_ok=True)
    source = d["source"]
    if "://" not in source and not Path(source).is_absolute():
        source = str(p.root / source)
    try:
        infos = do_freeze(source, p.slides_dir, width=d["width"], height=d["height"],
                          exclude=d["exclude"], selector=d["selector"], deck_dir=p.root)
    except (RuntimeError, ValueError) as e:
        raise PrezError(str(e)) from None
    except subprocess.CalledProcessError as e:
        raise PrezError(f"{Path(str(e.cmd[0])).name} failed: {(e.stderr or '').strip()[-500:]}") from None
    except Exception as e:
        if not _is_playwright_error(e):
            raise
        hint = ("; run `playwright install chromium`"
                if "Executable doesn't exist" in str(e) else "")
        raise PrezError(f"browser error while freezing: {str(e).strip().splitlines()[0]}{hint}") from None
    keep = {i.file for i in infos}
    for old in p.slides_dir.glob("slide-*.png"):
        if old.name not in keep:
            old.unlink()
    write_json(p.manifest_path, [dataclasses.asdict(i) for i in infos])
    click.echo(f"froze {len(infos)} slides")


def _is_playwright_error(e: Exception) -> bool:
    try:
        from playwright.sync_api import Error
    except ImportError:
        return False
    return isinstance(e, Error)


def _mark(ok: bool) -> str:
    return "yes" if ok else "no"


def _stale(fresh: bool) -> str:
    return "fresh" if fresh else "STALE"


@cli.command()
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def status(ctx, as_json):
    """Show per-slide pipeline state."""
    p = proj(ctx)
    rows = [slide_status(p, n) for n in p.numbers()]
    if as_json:
        click.echo(json.dumps(rows, indent=2, ensure_ascii=False))
        return
    click.echo(f"{'slide':>5}  {'discours':8}  {'validated':9}  {'audio':5}  {'clip':5}  title")
    for r in rows:
        click.echo(f"{r['n']:>5}  {_mark(r['discours']):8}  {_mark(r['validated']):9}  "
                   f"{_stale(r['audio_fresh']):5}  {_stale(r['clip_fresh']):5}  {r['title']}")


@cli.group(name="discours", cls=PrezGroup)
def discours_group():
    """Manage the spoken script."""


@discours_group.command("init")
@click.pass_context
def discours_init(ctx):
    """Create missing discours/slide-NN.md stubs."""
    p = proj(ctx)
    p.discours_dir.mkdir(exist_ok=True)
    created = 0
    for s in p.slides():
        path = p.discours_path(s.n)
        if not path.exists():
            path.write_text(disc.stub(s.title or f"Slide {s.n}"))
            created += 1
    click.echo(f"created {created} stubs")


@discours_group.command("text")
@click.argument("sel")
@click.pass_context
def discours_text(ctx, sel):
    """Print the normalized TTS text."""
    p = proj(ctx)
    for n in p.select(sel):
        click.echo(f"[{p.key(n)}] {p.tts_text(n)}")


@cli.command()
@click.argument("sel")
@click.pass_context
def validate(ctx, sel):
    """Record the current discours text as validated."""
    p = proj(ctx)
    nums = p.select(sel)
    missing = [n for n in nums if not p.discours_path(n).exists()]
    if missing:
        raise PrezError(f"no discours file for slide(s) {', '.join(map(str, missing))}")
    p.set_validated({n: p.text_fp(n) for n in nums})
    click.echo(f"validated {len(nums)} slide(s)")


@cli.command("review-page")
@click.option("-o", "--output", type=click.Path(dir_okay=False), default=None)
@click.pass_context
def review_page(ctx, output):
    """Write the self-contained review HTML."""
    p = proj(ctx)
    out = Path(output) if output else p.out_dir / "review.html"
    pages.write(out, pages.review_page(p))
    click.echo(str(out))


@cli.command()
@click.option("--slide", default=1, show_default=True)
@click.option("--variants", default=4, show_default=True)
@click.pass_context
def audition(ctx, slide, variants):
    """Render voice-setting variants of one slide."""
    p = proj(ctx)
    p.select(str(slide))
    voice.audition(p, slide, variants)
    click.echo(str(p.audition_dir / "audition.html"))


@cli.group(name="voice", cls=PrezGroup)
def voice_group():
    """Inspect or edit voice settings."""


@voice_group.command("show")
@click.pass_context
def voice_show(ctx):
    for k, v in proj(ctx).voice.items():
        click.echo(f"{k} = {json.dumps(v)}")


@voice_group.command("set")
@click.argument("assignments", nargs=-1, required=True)
@click.pass_context
def voice_set(ctx, assignments):
    """Set voice keys, e.g. stability=0.4."""
    p = proj(ctx)
    parsed = {}
    for a in assignments:
        key, sep, raw = a.partition("=")
        if not sep or key not in VOICE_KEYS:
            raise PrezError(f"bad assignment {a!r}; keys: {', '.join(sorted(VOICE_KEYS))}")
        if key in STRING_KEYS:
            parsed[key] = raw
            continue
        try:
            parsed[key] = tomllib.loads(f"v = {raw}")["v"]
        except tomllib.TOMLDecodeError:
            parsed[key] = raw
    for k, v in parsed.items():
        p.set_value("voice", k, v)
    p.save()


@voice_group.command("choose")
@click.argument("k", type=int)
@click.pass_context
def voice_choose(ctx, k):
    """Adopt audition variant K into prez.toml."""
    chosen = voice.choose(proj(ctx), k)
    click.echo(f"voice set: {json.dumps(chosen)}")


@cli.command()
@click.argument("sel", default="all")
@click.option("--force", is_flag=True)
@click.pass_context
def synth(ctx, sel, force):
    """Synthesize speech for stale slides."""
    p = proj(ctx)
    done = voice.synth(p, p.select(sel), force=force,
                       progress=lambda n, msg: click.echo(f"slide {n}: {msg}"))
    click.echo(f"synthesized {len(done)} slide(s): {', '.join(map(str, done)) or '-'}")


@cli.command()
@click.option("--force", is_flag=True)
@click.option("--burn-subs", "burn", is_flag=True, default=None)
@click.pass_context
def render(ctx, force, burn):
    """Build clips, concatenate, add subtitles and chapters."""
    p = proj(ctx)
    res = montage.render(p, force=force, burn=burn,
                         progress=lambda n, msg: click.echo(f"slide {n}: {msg}"))
    click.echo(f"clips rebuilt: {', '.join(map(str, res.built)) or '-'}")
    click.echo(str(res.output))


@cli.command()
@click.option("-o", "--output", type=click.Path(dir_okay=False), default=None)
@click.pass_context
def player(ctx, output):
    """Write the HTML player next to the rendered video."""
    p = proj(ctx)
    mp4 = p.out_dir / f"{p.slug}.mp4"
    chapters = read_json(p.out_dir / "chapters.json")
    cues = read_json(p.out_dir / "cues.json")
    if not mp4.exists() or chapters is None or cues is None:
        raise PrezError("no rendered video; run `prez render` first")
    out = Path(output) if output else p.out_dir / "player.html"
    pages.write(out, pages.player_page(p, mp4, chapters, cues, out.resolve().parent))
    click.echo(str(out))


@cli.command()
@click.argument("sel", default="all")
@click.option("--threshold", default=0.15, show_default=True, help="Max word error rate.")
@click.pass_context
def verify(ctx, sel, threshold):
    """Transcribe each slide's audio and compare with the text."""
    p = proj(ctx)
    rows = run_verify(p, p.select(sel), threshold)
    for r in rows:
        detail = f"WER {r.wer:.2f}" if r.wer is not None else r.reason
        click.echo(f"slide {r.slide}: {r.status} {detail}")
        if r.status == "FAIL":
            click.echo(f"  heard: {r.heard}")
            for d in r.diff:
                click.echo(f"  {d}")
    if any(r.status == "FAIL" for r in rows):
        ctx.exit(1)
    if any(r.status == "SKIPPED" for r in rows):
        ctx.exit(EXIT_SKIPPED)


def run_verify(p: Project, slides: list[int], threshold: float, client=None) -> list[VerifyRow]:
    rows = []
    for n in slides:
        text = p.tts_text(n)
        if not text:
            rows.append(VerifyRow(n, "OK", 0.0))
            continue
        if not p.audio_fresh(n):
            rows.append(VerifyRow(n, "SKIPPED", reason="audio missing or stale"))
            continue
        try:
            client = client or ElevenLabsClient(get_api_key())
            hyp = client.transcribe(p.audio_path(n).read_bytes(), lang=p.lang)
        except ElevenLabsPermissionError as e:
            rows.append(VerifyRow(n, "SKIPPED", reason=str(e)))
            continue
        except PrezError as e:
            rows.append(VerifyRow(n, "SKIPPED", reason=str(e)))
            continue
        score = wer(text, hyp)
        failed = score > threshold
        rows.append(VerifyRow(n, "FAIL" if failed else "OK", score, heard=hyp,
                              diff=word_diff(text, hyp) if failed else []))
    return rows


@cli.group(name="key", cls=PrezGroup)
def key_group():
    """Manage the ElevenLabs API key."""


@key_group.command("set")
@click.option("--value", default=None, hidden=True)
def key_set(value):
    """Store the ElevenLabs key in the OS keyring."""
    from prezaudio.tts.elevenlabs import KEYRING_SERVICE, KEYRING_USER
    value = value or click.prompt("ElevenLabs API key", hide_input=True)
    try:
        import keyring
        keyring.set_password(KEYRING_SERVICE, KEYRING_USER, value)
    except Exception as e:
        raise PrezError(f"cannot use the OS keyring ({type(e).__name__}); "
                        "set ELEVENLABS_API_KEY instead") from None
    click.echo("stored")


@key_group.command("check")
def key_check():
    """Say whether an ElevenLabs key is available, without printing it."""
    origin = "env" if os.environ.get("ELEVENLABS_API_KEY") else "keyring"
    get_api_key()
    click.echo(f"key found ({origin})")


def main():
    cli()


