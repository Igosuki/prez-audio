import dataclasses
import datetime

from prezaudio import pages
from prezaudio.guards import require_validated
from prezaudio.fingerprint import sha256_bytes, text_fp as fp_text
from prezaudio.project import PrezError, Project, read_json, write_bytes_atomic, write_json
from prezaudio.tts import TTSBackend, get_backend

CONTEXT_CHARS = 500
AUDITION_CHARS = 600
AUDITION_DEFAULTS = [(0.5, 0.0), (0.35, 0.3), (0.3, 0.5), (0.2, 0.7)]


def synth(project: Project, slides: list[int], *, force: bool = False,
          backend: TTSBackend | None = None, progress=None) -> list[int]:
    note = progress or (lambda n, msg: None)
    for n in slides:
        require_validated(project, n)
    nums = project.numbers()
    settings = project.voice_settings()
    done: list[int] = []
    for n in slides:
        if not force and project.audio_fresh(n):
            note(n, "audio fresh, skipped")
            continue
        backend = backend or get_backend(settings.backend)
        text = project.tts_text(n)
        require_validated(project, n, text)
        i = nums.index(n)
        prev = project.tts_text(nums[i - 1])[-CONTEXT_CHARS:] if i > 0 else None
        nxt = project.tts_text(nums[i + 1])[:CONTEXT_CHARS] if i + 1 < len(nums) else None
        project.audio_dir.mkdir(parents=True, exist_ok=True)
        if text:
            result = backend.synthesize(text, settings, previous_text=prev or None,
                                        next_text=nxt or None, lang=project.lang)
            write_bytes_atomic(project.audio_path(n), result.audio_bytes)
            duration, alignment = result.duration, [dataclasses.asdict(w) for w in result.alignment]
            audio_sha = sha256_bytes(result.audio_bytes)
        else:
            project.audio_path(n).unlink(missing_ok=True)
            duration, alignment, audio_sha = 0.0, [], ""
        write_json(project.audio_meta_path(n), {
            "audio_fp": project.audio_fp(n, text, settings), "text_fp": fp_text(text),
            "audio_sha256": audio_sha, "duration": duration, "alignment": alignment})
        note(n, "synthesized")
        done.append(n)
    return done


def variant_grid(count: int) -> list[tuple[float, float]]:
    if count == len(AUDITION_DEFAULTS):
        return list(AUDITION_DEFAULTS)
    (s0, y0), (s1, y1) = AUDITION_DEFAULTS[0], AUDITION_DEFAULTS[-1]
    if count == 1:
        return [(s0, y0)]
    return [(round(s0 + (s1 - s0) * k / (count - 1), 3), round(y0 + (y1 - y0) * k / (count - 1), 3))
            for k in range(count)]


def audition_text(project: Project, slide: int) -> str:
    text = project.tts_text(slide)
    if not text:
        raise PrezError(f"slide {slide} has no discours text to audition")
    if len(text) > AUDITION_CHARS:
        cut = text[:AUDITION_CHARS]
        ends = [m for m in (cut.rfind(c) for c in ".!?…") if m > 0]
        text = cut[: max(ends) + 1] if ends else cut
    return text


def audition(project: Project, slide: int, count: int = 4,
             backend: TTSBackend | None = None) -> list[dict]:
    if count < 1:
        raise PrezError("--variants must be >= 1")
    base = project.voice_settings()
    backend = backend or get_backend(base.backend)
    text = audition_text(project, slide)
    project.audition_dir.mkdir(parents=True, exist_ok=True)
    variants = []
    for k, (stability, style) in enumerate(variant_grid(count), 1):
        s = dataclasses.replace(base, stability=stability, style=style)
        result = backend.synthesize(text, s, lang=project.lang)
        name = f"variant-{k}.mp3"
        write_bytes_atomic(project.audition_dir / name, result.audio_bytes)
        variants.append({"k": k, "file": name, "slide": slide, **dataclasses.asdict(s)})
    write_json(project.audition_dir / "variants.json", variants)
    pages.write(project.audition_dir / "audition.html", pages.audition_page(project, variants, text))
    return variants


def choose(project: Project, k: int) -> dict:
    variants = read_json(project.audition_dir / "variants.json")
    if not variants:
        raise PrezError("no audition found; run `prez audition` first")
    match = next((v for v in variants if v["k"] == k), None)
    if match is None:
        raise PrezError(f"no variant {k} (have 1-{len(variants)})")
    chosen = {key: match[key] for key in ("stability", "style")}
    for key, value in chosen.items():
        project.set_value("voice", key, value)
    project.save()
    write_json(project.state_dir / "voice.json", {
        "chosen_variant": k, "settings": chosen, "audition_slide": match["slide"],
        "chosen_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "source": "prez audition"})
    return chosen


