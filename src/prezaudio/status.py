from prezaudio.project import Project


def slide_status(project: Project, n: int) -> dict:
    return {
        "n": n,
        "title": project.chapter_title(n),
        "discours": project.discours_path(n).exists(),
        "validated": project.validated().get(project.key(n)) == project.text_fp(n),
        "audio_fresh": project.audio_fresh(n),
        "clip_fresh": project.clip_fresh(n),
    }
