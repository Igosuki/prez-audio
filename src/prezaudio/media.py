import json
import subprocess
import tempfile
from pathlib import Path

from prezaudio.project import PrezError


class MediaError(PrezError):
    pass


def run(cmd: list[str], *, cwd: Path | None = None) -> str:
    try:
        proc = subprocess.run(cmd, check=True, capture_output=True, text=True, cwd=cwd)
    except FileNotFoundError:
        raise MediaError(f"{cmd[0]} not found on PATH") from None
    except subprocess.CalledProcessError as e:
        raise MediaError(f"{cmd[0]} failed: {e.stderr.strip()[-800:]}") from None
    return proc.stdout


def ffmpeg(*args: str | Path, cwd: Path | None = None) -> None:
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *map(str, args)], cwd=cwd)


def probe_duration(path: Path) -> float:
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
               "-of", "default=nw=1:nk=1", str(path)])
    return float(out.strip())


def probe_duration_bytes(data: bytes, suffix: str = ".mp3") -> float:
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / f"a{suffix}"
        p.write_bytes(data)
        return probe_duration(p)


def probe(path: Path) -> dict:
    return json.loads(run(["ffprobe", "-v", "error", "-show_streams", "-show_chapters",
                           "-show_format", "-of", "json", str(path)]))
