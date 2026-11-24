from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _p(env: str, default: str) -> Path:
    return Path(os.getenv(env, default)).resolve()


@dataclass
class Settings:
    """Runtime settings; every value can be overridden with an MS_* environment variable."""

    warehouse: Path = field(default_factory=lambda: _p("MS_WAREHOUSE", ".steward/warehouse.duckdb"))
    models_dir: Path = field(default_factory=lambda: _p("MS_MODELS_DIR", "models"))
    catalog_dir: Path = field(default_factory=lambda: _p("MS_CATALOG_DIR", "catalog"))
    model: str = field(default_factory=lambda: os.getenv("MS_MODEL", "claude-sonnet-5-5"))
    max_agent_turns: int = field(default_factory=lambda: int(os.getenv("MS_MAX_AGENT_TURNS", "16")))
