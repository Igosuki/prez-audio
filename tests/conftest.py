import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from prezaudio.cli import cli
from prezaudio.fingerprint import file_sha256
from prezaudio.project import Project, write_json

COLORS = ["red", "green", "blue", "yellow"]


def make_png(path: Path, color: str) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"color=c={color}:s=64x36", "-frames:v", "1", str(path)], check=True)


def make_project(root: Path, slides: int = 3) -> Project:
    r = CliRunner().invoke(cli, ["new", str(root), "--deck", "x.html", "--title", "Demo Deck",
                                 "--lang", "en", "--backend", "fake"])
    assert r.exit_code == 0, r.output
    p = Project(root)
    p.set_value("deck", "width", 64)
    p.set_value("deck", "height", 36)
    p.set_value("render", "lead_in", 0.1)
    p.set_value("render", "tail", 0.1)
    p.save()
    p.slides_dir.mkdir()
    manifest = []
    for n in range(1, slides + 1):
        f = p.slides_dir / f"slide-{n:02d}.png"
        make_png(f, COLORS[n % len(COLORS)])
        manifest.append({"n": n, "source_n": n, "title": f"Title {n}", "file": f.name,
                         "sha256": file_sha256(f)})
    write_json(p.manifest_path, manifest)
    return p


@pytest.fixture
def project(tmp_path) -> Project:
    return make_project(tmp_path / "proj")


@pytest.fixture
def invoke(project):
    def run(*args, **kw):
        return CliRunner().invoke(cli, ["-C", str(project.root), *args], **kw)
    return run


