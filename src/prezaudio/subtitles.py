import textwrap
from dataclasses import dataclass

from prezaudio.tts.base import WordTiming

MAX_LINE = 42
MAX_LINES = 2
MAX_CUE_SECONDS = 6.0
_SENTENCE_END = (".", "!", "?", "…")


@dataclass
class Cue:
    start: float
    end: float
    text: str
    slide: int = 0


def wrap(text: str) -> str:
    return "\n".join(textwrap.wrap(text, MAX_LINE, break_long_words=False, break_on_hyphens=False))


def _fits(words: list[WordTiming]) -> bool:
    lines = textwrap.wrap(" ".join(w.word for w in words), MAX_LINE,
                          break_long_words=False, break_on_hyphens=False)
    return len(lines) <= MAX_LINES and words[-1].end - words[0].start <= MAX_CUE_SECONDS


def build_cues(words: list[WordTiming], offset: float, slide: int = 0) -> list[Cue]:
    cues: list[Cue] = []
    cur: list[WordTiming] = []

    def flush() -> None:
        if cur:
            cues.append(Cue(round(offset + cur[0].start, 3), round(offset + cur[-1].end, 3),
                            wrap(" ".join(w.word for w in cur)), slide))
            cur.clear()

    for w in words:
        if cur and not _fits([*cur, w]):
            flush()
        cur.append(w)
        if w.word.endswith(_SENTENCE_END):
            flush()
    flush()
    return cues


def _ts(t: float, sep: str) -> str:
    ms = round(t * 1000)
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def to_srt(cues: list[Cue]) -> str:
    return "".join(f"{i}\n{_ts(c.start, ',')} --> {_ts(c.end, ',')}\n{c.text}\n\n"
                   for i, c in enumerate(cues, 1))


def to_vtt(cues: list[Cue]) -> str:
    body = "".join(f"{_ts(c.start, '.')} --> {_ts(c.end, '.')}\n{c.text}\n\n" for c in cues)
    return "WEBVTT\n\n" + body
