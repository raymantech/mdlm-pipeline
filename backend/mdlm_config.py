import os
from pathlib import Path
from typing import Optional, Union


def _root_dir() -> Path:
    # backend/mdlm_config.py -> MDLM1.0/
    return Path(__file__).resolve().parent.parent


ROOT_DIR = _root_dir()
BACKEND_DIR = ROOT_DIR / "backend"
FRONTEND_DIR = ROOT_DIR / "frontend"


def _env_candidates() -> list[Path]:
    return [ROOT_DIR / ".env", BACKEND_DIR / ".env"]


def _parse_env_file(path: Path, override: bool) -> None:
    """Tiny .env parser (KEY=VALUE). Supports quotes. Ignores comments."""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip()
        if not key:
            continue
        # strip surrounding quotes
        if (val.startswith("\"") and val.endswith("\"")) or (val.startswith("'") and val.endswith("'")):
            val = val[1:-1]
        if override or key not in os.environ:
            os.environ[key] = val


def load_env(override: bool = True) -> Optional[Path]:
    """Load .env from ROOT_DIR/.env or BACKEND_DIR/.env. Returns the path used."""
    env_path = next((p for p in _env_candidates() if p.exists()), None)
    if not env_path:
        return None

    # Prefer python-dotenv if present
    try:
        from dotenv import load_dotenv  # type: ignore

        load_dotenv(dotenv_path=env_path, override=override)
    except Exception:
        _parse_env_file(env_path, override=override)

    return env_path


def resolve_path_from_root(p: Union[str, Path]) -> Path:
    pp = Path(p)
    if pp.is_absolute():
        return pp
    return (ROOT_DIR / pp).resolve()


def db_path() -> Path:
    load_env(override=True)
    raw = os.getenv("SQLITE_DB_PATH")
    if raw:
        return resolve_path_from_root(raw)
    return (BACKEND_DIR / "charts.db").resolve()


def frontend_json_path() -> Path:
    # where the dashboard reads data from
    return (FRONTEND_DIR / "data" / "merged_events_latest.json").resolve()
