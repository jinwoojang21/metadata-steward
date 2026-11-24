from __future__ import annotations

from pathlib import Path

import pytest

from metadata_steward.config import Settings
from metadata_steward.seed import seed

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def warehouse_path(tmp_path_factory) -> Path:
    p = tmp_path_factory.mktemp("wh") / "warehouse.duckdb"
    seed(p, models_dir=ROOT / "models")
    return p


@pytest.fixture
def settings(warehouse_path, tmp_path) -> Settings:
    return Settings(warehouse=warehouse_path, models_dir=ROOT / "models", catalog_dir=tmp_path / "catalog")
