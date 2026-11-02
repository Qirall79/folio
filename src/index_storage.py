from pathlib import Path
from filelock import FileLock


class IndexNotReadyError(RuntimeError):
    pass


PROJECT_ROOT = Path(__file__).resolve().parents[1]

INDEX_DIR = PROJECT_ROOT / "indexes"
INDEX_LOCK = FileLock(
    str(PROJECT_ROOT / "indexes.lock"),
    timeout=60,
)
