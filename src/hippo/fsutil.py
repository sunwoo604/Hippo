import os
import shutil
import tempfile
from pathlib import Path


def atomic_write_text(path: Path, text: str) -> None:
    """Write via a temp file + rename so readers never see a half-written file.

    Symlinks are followed, so a dotfile-managed ~/.claude/CLAUDE.md keeps
    pointing at its real target.
    """
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        if path.exists():
            shutil.copymode(path, tmp)
        else:
            os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
