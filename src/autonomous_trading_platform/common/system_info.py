import hashlib
import subprocess
from pathlib import Path


def get_git_commit() -> str:
    try:
        return (
            subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                stderr=subprocess.DEVNULL,
            )
            .decode()
            .strip()
        )
    except Exception:
        return "unknown"


def get_dependency_lock_hash() -> str | None:
    for candidate in ("poetry.lock", "requirements.txt", "Pipfile.lock"):
        path = Path(candidate)
        if path.exists():
            return hashlib.sha256(path.read_bytes()).hexdigest()
    return None
