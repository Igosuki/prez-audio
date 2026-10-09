import dataclasses
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from prezaudio.guards import require_fresh_audio
from prezaudio.media import ffmpeg, probe_duration
from prezaudio.project import SILENT_EXTRA, Project, write_json
from prezaudio.subtitles import Cue, build_cues, to_srt, to_vtt
from prezaudio.tts.base import WordTiming

FPS = 25
LANG3 = {"fr": "fra", "en": "eng", "es": "spa", "de": "deu", "it": "ita", "pt": "por",
         "nl": "nld", "ja": "jpn", "zh": "zho"}


@dataclass
class Timeline:
    chapters: list[dict]
    cues: list[Cue]
    total: float


@dataclass
class RenderResult:
    built: list[int]
    output: Path
    timeline: Timeline


def clip_duration(project: Project, n: int) -> float:
    cfg = project.render_cfg
    audio = (project.audio_meta(n) or {}).get("duration", 0)
    if audio == 0:
        return cfg["lead_in"] + cfg["tail"] + SILENT_EXTRA
    return cfg["lead_in"] + audio + cfg["tail"]


def build_clip(project: Project, n: int) -> None:
    cfg, deck = project.render_cfg, project.deck
    w, h = deck["width"], deck["height"]
    dur = clip_duration(project, n)
    audio_dur = (project.audio_meta(n) or {}).get("duration", 0)
    project.clips_dir.mkdir(parents=True, exist_ok=True)
    vf = (f"[0:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
          f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p[v]")
    if audio_dur:
        delay = round(cfg["lead_in"] * 1000)
        af = (f"[1:a]loudnorm=I={cfg['loudness']}:TP=-1.5:LRA=11,aresample=48000,"
              f"aformat=channel_layouts=stereo,adelay={delay}:all=1,apad[a]")
        audio_in = ["-i", project.audio_path(n)]
    else:
        af = "[1:a]aformat=channel_layouts=stereo[a]"
        audio_in = ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
    out = project.clip_path(n)
    ffmpeg("-loop", "1", "-framerate", FPS, "-i", project.image_path(n), *audio_in,
           "-filter_complex", f"{vf};{af}", "-map", "[v]", "-map", "[a]",
           "-t", f"{dur:.3f}", "-r", FPS, "-c:v", "libx264", "-tune", "stillimage",
           "-preset", "veryfast", "-crf", cfg["crf"], "-c:a", "aac", "-ar", 48000,
           "-b:a", "160k", out)
    write_json(project.clip_meta_path(n), {
        "clip_fp": project.clip_fp(n), "duration": probe_duration(out),
        "audio_duration": audio_dur, "lead_in": cfg["lead_in"]})


def build_timeline(project: Project, slides: list[int]) -> Timeline:
    chapters, cues, offset = [], [], 0.0
    for n in slides:
        meta = project.clip_meta(n)
        dur = meta["duration"]
        chapters.append({"n": n, "title": project.chapter_title(n),
                         "start": round(offset, 3), "end": round(offset + dur, 3)})
        words = [WordTiming(**x) for x in (project.audio_meta(n) or {}).get("alignment", [])]
        cues += build_cues(words, offset + meta["lead_in"], n)
        offset += dur
    return Timeline(chapters, cues, offset)


def _meta_escape(s: str) -> str:
    for ch in ("\\", "=", ";", "#", "\n"):
        s = s.replace(ch, "\\" + ch)
    return s


def ffmetadata(title: str, chapters: list[dict]) -> str:
    out = [";FFMETADATA1", f"title={_meta_escape(title)}"]
    for c in chapters:
        out += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={round(c['start'] * 1000)}",
                f"END={round(c['end'] * 1000)}", f"title={_meta_escape(c['title'])}"]
    return "\n".join(out) + "\n"


def _concat_list(paths: list[Path]) -> str:
    return "".join("file '{}'\n".format(str(p).replace("'", "'\\''")) for p in paths)


def render(project: Project, *, force: bool = False, burn: bool | None = None,
           progress=None) -> RenderResult:
    note = progress or (lambda n, msg: None)
    slides = project.numbers()
    for n in slides:
        require_fresh_audio(project, n)
    built = []
    for n in slides:
        if force or not project.clip_fresh(n):
            build_clip(project, n)
            built.append(n)
            note(n, "clip built")
        else:
            note(n, "clip fresh, skipped")
    tl = build_timeline(project, slides)
    burn = project.render_cfg["burn_subtitles"] if burn is None else burn

    out_dir, slug = project.out_dir, project.slug
    out_dir.mkdir(parents=True, exist_ok=True)
    build = project.root / "build"
    concat_txt, meta_txt = build / "concat.txt", build / "chapters.ffmetadata"
    concat_txt.write_text(_concat_list([project.clip_path(n) for n in slides]))
    meta_txt.write_text(ffmetadata(project.title, tl.chapters))
    srt, vtt, mp4 = out_dir / f"{slug}.srt", out_dir / f"{slug}.vtt", out_dir / f"{slug}.mp4"
    srt.write_text(to_srt(tl.cues))
    vtt.write_text(to_vtt(tl.cues))
    write_json(out_dir / "chapters.json", tl.chapters)
    write_json(out_dir / "cues.json", [dataclasses.asdict(c) for c in tl.cues])

    if burn and tl.cues:
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copy(srt, Path(tmp) / "subs.srt")
            ffmpeg("-f", "concat", "-safe", 0, "-i", concat_txt, "-i", meta_txt,
                   "-vf", "subtitles=subs.srt", "-map", "0:v", "-map", "0:a", "-map_chapters", 1,
                   "-c:v", "libx264", "-preset", "veryfast", "-crf", project.render_cfg["crf"],
                   "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart", mp4,
                   cwd=Path(tmp))
    elif tl.cues:
        ffmpeg("-f", "concat", "-safe", 0, "-i", concat_txt, "-i", srt, "-i", meta_txt,
               "-map", "0:v", "-map", "0:a", "-map", "1:s", "-map_chapters", 2,
               "-c", "copy", "-c:s", "mov_text",
               "-metadata:s:s:0", f"language={LANG3.get(project.lang.split('-')[0], 'und')}",
               "-movflags", "+faststart", mp4)
    else:
        ffmpeg("-f", "concat", "-safe", 0, "-i", concat_txt, "-i", meta_txt,
               "-map", "0:v", "-map", "0:a", "-map_chapters", 1, "-c", "copy",
               "-movflags", "+faststart", mp4)
    return RenderResult(built, mp4, tl)


