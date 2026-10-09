import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path

import tomlkit

from prezaudio import discours as disc
from prezaudio import fingerprint as fp
from prezaudio.tts.base import VoiceSettings

CONFIG = "prez.toml"

VOICE_DEFAULTS = {
    "backend": "elevenlabs",
    "voice_id": "",
    "model_id": "eleven_multilingual_v2",
    "stability": 0.5,
    "similarity_boost": 0.75,
    "style": 0.0,
    "speed": 1.0,
    "use_speaker_boost": True,
}
RENDER_DEFAULTS = {
    "lead_in": 0.4,
    "tail": 0.8,
    "loudness": -16.0,
    "burn_subtitles": False,
    "crf": 20,
}
DECK_DEFAULTS = {"source": "", "exclude": [], "selector": "", "width": 1920, "height": 1080}
SILENT_EXTRA = 2.0


class PrezError(Exception):
    pass


@dataclass
class Slide:
    n: int
    title: str
    file: str
    sha256: str
    source_n: int = 0


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except ValueError:
        raise PrezError(f"corrupt state file {path}; delete it or restore it from git") from None


def write_bytes_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def write_json(path: Path, data) -> None:
    write_bytes_atomic(path, (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode())


def parse_ints(spec: str) -> list[int]:
    out: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not m:
            raise PrezError(f"bad slide selector {part!r} (use 3, 3,5, 3-7 or all)")
        a, b = int(m[1]), int(m[2] or m[1])
        if b < a:
            raise PrezError(f"bad range {part!r}")
        out.extend(range(a, b + 1))
    return out


def parse_selector(sel: str, available: list[int]) -> list[int]:
    if sel.strip() == "all":
        return list(available)
    wanted = sorted(set(parse_ints(sel)))
    missing = [n for n in wanted if n not in available]
    if missing:
        raise PrezError(f"no such slide(s): {', '.join(map(str, missing))}")
    return wanted


class Project:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.config_path = self.root / CONFIG
        if not self.config_path.exists():
            raise PrezError(f"no {CONFIG} in {self.root} (run `prez new` or use -C <dir>)")
        self.reload()

    def reload(self) -> None:
        self.doc = tomlkit.parse(self.config_path.read_text())
        self.data = self.doc.unwrap()

    def save(self) -> None:
        self.config_path.write_text(tomlkit.dumps(self.doc))
        self.reload()

    def set_value(self, table: str, key: str, value) -> None:
        if table not in self.doc:
            self.doc[table] = tomlkit.table()
        self.doc[table][key] = value

    @property
    def title(self) -> str:
        return self.data.get("title") or self.root.name

    @property
    def slug(self) -> str:
        return self.data.get("slug") or re.sub(r"[^a-z0-9]+", "-", self.title.lower()).strip("-") or "video"

    @property
    def lang(self) -> str:
        return self.data.get("lang", "en")

    @property
    def deck(self) -> dict:
        return {**DECK_DEFAULTS, **self.data.get("deck", {})}

    @property
    def voice(self) -> dict:
        return {**VOICE_DEFAULTS, **self.data.get("voice", {})}

    @property
    def render_cfg(self) -> dict:
        return {**RENDER_DEFAULTS, **self.data.get("render", {})}

    @property
    def slides_dir(self) -> Path:
        return self.root / "slides"

    @property
    def manifest_path(self) -> Path:
        return self.slides_dir / "manifest.json"

    @property
    def discours_dir(self) -> Path:
        return self.root / "discours"

    @property
    def state_dir(self) -> Path:
        return self.root / "state"

    @property
    def audio_dir(self) -> Path:
        return self.root / "audio"

    @property
    def clips_dir(self) -> Path:
        return self.root / "build" / "clips"

    @property
    def out_dir(self) -> Path:
        return self.root / "out"

    @property
    def audition_dir(self) -> Path:
        return self.root / "audition"

    def slides(self) -> list[Slide]:
        raw = read_json(self.manifest_path)
        if raw is None:
            raise PrezError("no slides/manifest.json (run `prez freeze`)")
        return [Slide(n=s["n"], title=s.get("title", ""), file=s["file"], sha256=s["sha256"],
                      source_n=s.get("source_n", s["n"])) for s in raw]

    def numbers(self) -> list[int]:
        return [s.n for s in self.slides()]

    def select(self, sel: str = "all") -> list[int]:
        return parse_selector(sel, self.numbers())

    def slide(self, n: int) -> Slide:
        for s in self.slides():
            if s.n == n:
                return s
        raise PrezError(f"no such slide: {n}")

    def key(self, n: int) -> str:
        return f"{n:0{3 if len(self.slides()) >= 100 else 2}d}"

    def image_path(self, n: int) -> Path:
        return self.slides_dir / self.slide(n).file

    def discours_path(self, n: int) -> Path:
        return self.discours_dir / f"slide-{self.key(n)}.md"

    def audio_path(self, n: int) -> Path:
        return self.audio_dir / f"slide-{self.key(n)}.mp3"

    def audio_meta_path(self, n: int) -> Path:
        return self.audio_dir / f"slide-{self.key(n)}.json"

    def clip_path(self, n: int) -> Path:
        return self.clips_dir / f"slide-{self.key(n)}.mp4"

    def clip_meta_path(self, n: int) -> Path:
        return self.clips_dir / f"slide-{self.key(n)}.json"

    def raw_discours(self, n: int) -> str | None:
        p = self.discours_path(n)
        return p.read_text() if p.exists() else None

    def tts_text(self, n: int) -> str:
        return disc.tts_text(self.raw_discours(n) or "")

    def chapter_title(self, n: int) -> str:
        heading, _ = disc.split_title(self.raw_discours(n) or "")
        return heading or self.slide(n).title or f"Slide {n}"

    def text_fp(self, n: int) -> str:
        return fp.text_fp(self.tts_text(n))

    def voice_settings(self) -> VoiceSettings:
        v = self.voice
        return VoiceSettings(**{k: v[k] for k in VOICE_DEFAULTS})

    def audio_fp(self, n: int, text: str | None = None,
                 settings: VoiceSettings | None = None) -> str:
        text_fp = fp.text_fp(text) if text is not None else self.text_fp(n)
        settings = settings or self.voice_settings()
        return fp.audio_fp(text_fp, {**asdict(settings), "lang": self.lang})

    def render_params(self) -> dict:
        r = self.render_cfg
        d = self.deck
        return {"lead_in": r["lead_in"], "tail": r["tail"], "loudness": r["loudness"],
                "crf": r["crf"], "width": d["width"], "height": d["height"]}

    def clip_fp(self, n: int) -> str:
        audio_sha = (self.audio_meta(n) or {}).get("audio_sha256", "")
        return fp.clip_fp(self.audio_fp(n), f"{self.slide(n).sha256}:{audio_sha}",
                          self.render_params())

    def validated(self) -> dict:
        return read_json(self.state_dir / "validated.json", {})

    def set_validated(self, entries: dict[int, str]) -> None:
        cur = self.validated()
        cur.update({self.key(n): v for n, v in entries.items()})
        write_json(self.state_dir / "validated.json", cur)

    def audio_meta(self, n: int) -> dict | None:
        return read_json(self.audio_meta_path(n))

    def clip_meta(self, n: int) -> dict | None:
        return read_json(self.clip_meta_path(n))

    def audio_fresh(self, n: int) -> bool:
        meta = self.audio_meta(n)
        if not meta or meta.get("audio_fp") != self.audio_fp(n):
            return False
        if meta.get("duration", 0) == 0:
            return True
        path = self.audio_path(n)
        return path.exists() and fp.file_sha256(path) == meta.get("audio_sha256")

    def clip_fresh(self, n: int) -> bool:
        meta = self.clip_meta(n)
        return bool(meta) and meta.get("clip_fp") == self.clip_fp(n) and self.clip_path(n).exists()
