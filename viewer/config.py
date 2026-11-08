"""Configuration loading for the viewer."""
from __future__ import annotations

import configparser
import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


@dataclass(frozen=True)
class Config:
    host: str
    port: int
    claude_home: Path
    page_size: int
    max_block_chars: int
    poll_interval_ms: int
    cache_max_mb: int = 512


def default_config_path() -> Path:
    """Return CAV_CONFIG if set, else config.ini, else the shipped config.ini.example."""
    env = os.environ.get("CAV_CONFIG")
    if env:
        return Path(env).expanduser()
    local = PROJECT_ROOT / "config.ini"
    return local if local.is_file() else PROJECT_ROOT / "config.ini.example"


def load_config(path: Path | None = None) -> Config:
    """Load and validate configuration; raise ValueError on a non-loopback host."""
    path = path or default_config_path()
    parser = configparser.ConfigParser()
    if not parser.read(path, encoding="utf-8"):
        raise ValueError(f"cannot read config file: {path.name}")
    host = parser.get("server", "host", fallback="127.0.0.1").strip()
    if host not in LOOPBACK_HOSTS:
        raise ValueError(f"refusing non-loopback host: {host!r}")
    cache_max_mb = parser.getint("viewer", "cache_max_mb", fallback=512)
    if cache_max_mb <= 0:
        raise ValueError(f"cache_max_mb must be a positive integer: {cache_max_mb}")
    return Config(
        host=host,
        port=parser.getint("server", "port", fallback=8765),
        claude_home=Path(parser.get("claude", "home", fallback="~/.claude")).expanduser(),
        page_size=parser.getint("viewer", "page_size", fallback=50),
        max_block_chars=parser.getint("viewer", "max_block_chars", fallback=4000),
        poll_interval_ms=parser.getint("viewer", "poll_interval_ms", fallback=3000),
        cache_max_mb=cache_max_mb,
    )
