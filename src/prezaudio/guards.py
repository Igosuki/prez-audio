from prezaudio import fingerprint as fp
from prezaudio.project import PrezError, Project


class GuardError(PrezError):
    pass


def require_validated(project: Project, n: int, text: str | None = None) -> None:
    current = fp.text_fp(text) if text is not None else project.text_fp(n)
    if project.validated().get(project.key(n)) != current:
        raise GuardError(
            f"slide {n}: discours is not validated (missing or changed since validation); "
            f"review it, then run `prez validate {n}`")


def require_fresh_audio(project: Project, n: int) -> None:
    if not project.audio_fresh(n):
        raise GuardError(f"slide {n}: audio is stale or missing; run `prez synth {n}`")
