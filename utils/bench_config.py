"""Lab bench registry (config/benches.yaml) — stand label → profile mapping."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


@lru_cache(maxsize=1)
def load_benches() -> dict[str, dict[str, Any]]:
    path = _repo_root() / "config" / "benches.yaml"
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    benches = data.get("benches") or {}
    return {str(key): dict(value or {}) for key, value in benches.items()}


def bench_for_stand(stand: str) -> dict[str, Any]:
    return dict(load_benches().get(str(stand or "").strip(), {}))


def profile_for_stand(stand: str, *, default: str = "default") -> str:
    bench = bench_for_stand(stand)
    return str(bench.get("profile") or default).strip() or default


def recovery_profile_for_stand(stand: str, *, default: str = "link_formation") -> str:
    bench = bench_for_stand(stand)
    return str(bench.get("recovery_profile") or default).strip() or default
