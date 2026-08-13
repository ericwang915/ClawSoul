"""
Centralised configuration for HerAndHim.

All runtime data lives under ``~/.herandhim/`` (the *home* directory):

    ~/.herandhim/
      herandhim.json          ← config file
      context/                 ← sessions, logs, memory, skills, …
      daemon.log               ← daemon output
      herandhim.pid           ← daemon PID

Load order (later sources override earlier ones):
  1. ~/.herandhim/herandhim.json
  2. Environment variables (highest priority)
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

# Where everything lives: config, memory, photos, the SQLite store.
_HERANDHIM_BASE = Path(os.environ.get("HERANDHIM_HOME", Path.home() / ".herandhim"))

_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")

# Parsed-config cache.
_configs: dict[str, dict] = {}
_config_paths: dict[str, Path | None] = {}


# One install, one config — but the cache stays keyed so tests can swap
# HERANDHIM_HOME without leaking a previous parse into the next one.
_CACHE_KEY = ""


def _tenant_key() -> str:
    return _CACHE_KEY


def home() -> Path:
    """The HerAndHim home directory (``~/.herandhim`` unless overridden)."""
    return _HERANDHIM_BASE


def __getattr__(name: str):
    """Module-level dynamic attribute lookup (PEP 562).

    Exposes ``config.HERANDHIM_HOME`` as a *property* that resolves per-tenant
    on every access. Existing code reading ``config.HERANDHIM_HOME`` is auto-
    matically per-user without modification.
    """
    if name == "HERANDHIM_HOME":
        return home()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _strip_json5(text: str) -> str:
    """Strip // comments and trailing commas so standard json.loads works.

    Handles // inside quoted strings correctly (they are preserved).
    """
    result: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            # Consume the entire string literal (including escaped chars)
            j = i + 1
            while j < n:
                if text[j] == '\\':
                    j += 2
                elif text[j] == '"':
                    j += 1
                    break
                else:
                    j += 1
            result.append(text[i:j])
            i = j
        elif ch == '/' and i + 1 < n and text[i + 1] == '/':
            # Line comment — skip until end of line
            while i < n and text[i] != '\n':
                i += 1
        else:
            result.append(ch)
            i += 1
    text = "".join(result)
    text = _TRAILING_COMMA_RE.sub(r"\1", text)
    return text


def _find_config_file() -> Path | None:
    candidates = [
        home() / "herandhim.json",
        Path.cwd() / "herandhim.json",
    ]
    for p in candidates:
        if p.is_file():
            return p
    return None


def _deep_get(data: dict, *keys: str, default: Any = None) -> Any:
    current = data
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
        if current is None:
            return default
    return current


def load(path: str | Path | None = None, *, force: bool = False) -> dict:
    """Load and cache configuration for the current tenant.

    Parsed once and cached; pass ``force=True`` after writing the file.
    """
    key = _tenant_key()

    if key in _configs and not force:
        return _configs[key]

    config_path = Path(path) if path else _find_config_file()
    _config_paths[key] = config_path
    raw: dict = {}

    if config_path and config_path.is_file():
        text = config_path.read_text(encoding="utf-8")
        text = _strip_json5(text)
        raw = json.loads(text)

    _configs[key] = raw
    return raw


def get(*keys: str, env: str | None = None, default: Any = None) -> Any:
    """Get a config value for the current tenant. Env var takes priority over JSON.

    Examples
    --------
    config.get("llm", "provider", env="LLM_PROVIDER", default="deepseek")
    config.get("channels", "telegram", "token", env="TELEGRAM_BOT_TOKEN")
    """
    key = _tenant_key()
    if key not in _configs:
        load()

    if env:
        env_val = os.environ.get(env)
        if env_val is not None:
            return env_val

    val = _deep_get(_configs.get(key, {}), *keys, default=default)
    return val


def get_int(*keys: str, env: str | None = None, default: int = 0) -> int:
    """Get an integer config value."""
    val = get(*keys, env=env, default=default)
    return int(val) if val is not None else default


def get_str(*keys: str, env: str | None = None, default: str = "") -> str:
    """Get a string config value."""
    val = get(*keys, env=env, default=default)
    return str(val) if val is not None else default


def get_list(*keys: str, env: str | None = None, default: list | None = None) -> list:
    """Get a list value.  Env var is parsed as comma-separated."""
    key = _tenant_key()
    if key not in _configs:
        load()

    if env:
        env_val = os.environ.get(env)
        if env_val is not None and env_val.strip():
            return [v.strip() for v in env_val.split(",") if v.strip()]

    val = _deep_get(_configs.get(key, {}), *keys)
    if isinstance(val, list):
        return val
    return default or []


def get_int_list(*keys: str, env: str | None = None) -> list[int]:
    """Get a list of integers.  Env var is parsed as comma-separated ints."""
    raw = get_list(*keys, env=env)
    return [int(v) for v in raw] if raw else []


def config_path() -> Path | None:
    """Return the path to the loaded config file for the current tenant."""
    return _config_paths.get(_tenant_key())


def as_dict() -> dict:
    """Return a copy of the full loaded config dict for the current tenant."""
    key = _tenant_key()
    if key not in _configs:
        load()
    return dict(_configs.get(key, {}))


def get_bool(*keys: str, env: str | None = None, default: bool = False) -> bool:
    """Get a boolean config value."""
    val = get(*keys, env=env, default=default)
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.lower() in ("1", "true", "yes")
    return bool(val) if val is not None else default


def per_group_isolation() -> bool:
    """Return True if per-group context isolation is enabled."""
    return get_bool("isolation", "perGroup", default=True)


def group_context_dir(session_id: str) -> Path:
    """Return the per-group context directory for *session_id* (per-tenant).

    Maps ``session_id`` (e.g. ``telegram:123``) to a safe filesystem path
    under ``<tenant home>/context/groups/<safe_id>/``.
    """
    safe = re.sub(r"[^\w\-]", "_", session_id)
    return home() / "context" / "groups" / safe


def files_dir() -> Path:
    """Return the shared files directory for the current tenant."""
    d = home() / "context" / "files"
    d.mkdir(parents=True, exist_ok=True)
    return d


def clear_files() -> int:
    """Delete all files in the shared files directory. Returns count removed."""
    d = files_dir()
    count = 0
    for entry in d.iterdir():
        try:
            if entry.is_file():
                entry.unlink()
                count += 1
            elif entry.is_dir():
                import shutil
                shutil.rmtree(entry)
                count += 1
        except OSError:
            pass
    return count


def reset() -> None:
    """Clear the cached config (mainly for testing)."""
    global _config, _config_path
    _config = None
    _config_path = None
