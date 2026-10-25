"""Per-user working folders.

Each browser session gets its own folder on disk:

    workspaces/<session_id>/
        state.json        - which file / sheet is selected, cleaning config, ...
        source.xlsx       - the file the user uploaded (untouched original)
        cleaned.pkl       - result of the cleaning pipeline (pandas pickle)
        charts/           - rendered chart images + charts.json index
        tables/           - saved group-by analysis tables + tables.json index
        exports/          - generated PDF / HTML dashboards, Excel exports

Keeping everything on disk means the app survives restarts and any
worker process can serve any request (needed for gunicorn on a VPS).
"""
from __future__ import annotations

import json
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

import pandas as pd

STATE_FILE = "state.json"
CLEANED_FILE = "cleaned.pkl"
CHARTS_DIR = "charts"
TABLES_DIR = "tables"
EXPORTS_DIR = "exports"
CHARTS_INDEX = "charts.json"
TABLES_INDEX = "tables.json"


# --------------------------------------------------------------------------- #
# basic helpers
# --------------------------------------------------------------------------- #
def new_sid() -> str:
    """Short unique id for a session workspace."""
    return uuid.uuid4().hex[:16]


def workspace(base: Path, sid: str, create: bool = True) -> Path:
    ws = base / sid
    if create:
        ws.mkdir(parents=True, exist_ok=True)
    return ws


def subdir(ws: Path, name: str) -> Path:
    p = ws / name
    p.mkdir(parents=True, exist_ok=True)
    return p


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2, default=str)
    tmp.replace(path)


# --------------------------------------------------------------------------- #
# state
# --------------------------------------------------------------------------- #
DEFAULT_STATE: dict[str, Any] = {
    "source_file": None,     # original file name
    "source_path": None,     # path inside workspace
    "source_kind": None,     # "excel" | "csv"
    "sheets": [],            # available sheet names (excel)
    "sheet": None,           # chosen sheet
    "header_row": 0,
    "clean_plan": None,      # saved cleaning plan (list of steps)
    "clean_log": None,       # what the last run of the cleaner did
    "cleaned_at": None,
    "use_cleaned": True,     # analysis uses cleaned data by default
}


def load_state(ws: Path) -> dict[str, Any]:
    state = dict(DEFAULT_STATE)
    state.update(read_json(ws / STATE_FILE, {}) or {})
    return state


def save_state(ws: Path, state: dict[str, Any]) -> None:
    write_json(ws / STATE_FILE, state)


# --------------------------------------------------------------------------- #
# data files
# --------------------------------------------------------------------------- #
def dataset_path(ws: Path) -> Path:
    """Path used for analysis: cleaned data if it exists, else the raw file."""
    cleaned = ws / CLEANED_FILE
    return cleaned


def has_cleaned(ws: Path) -> bool:
    return (ws / CLEANED_FILE).exists()


def save_cleaned(ws: Path, df: pd.DataFrame) -> None:
    df.to_pickle(ws / CLEANED_FILE)


def load_cleaned(ws: Path) -> pd.DataFrame | None:
    path = ws / CLEANED_FILE
    if not path.exists():
        return None
    try:
        return pd.read_pickle(path)
    except Exception:
        return None


def remove_cleaned(ws: Path) -> None:
    p = ws / CLEANED_FILE
    if p.exists():
        p.unlink()


# --------------------------------------------------------------------------- #
# charts
# --------------------------------------------------------------------------- #
def charts_index(ws: Path) -> list[dict[str, Any]]:
    return read_json(ws / CHARTS_DIR / CHARTS_INDEX, []) or []


def save_charts_index(ws: Path, items: list[dict[str, Any]]) -> None:
    write_json(ws / CHARTS_DIR / CHARTS_INDEX, items)


def chart_dir(ws: Path) -> Path:
    return subdir(ws, CHARTS_DIR)


# --------------------------------------------------------------------------- #
# tables
# --------------------------------------------------------------------------- #
def tables_index(ws: Path) -> list[dict[str, Any]]:
    return read_json(ws / TABLES_DIR / TABLES_INDEX, []) or []


def save_tables_index(ws: Path, items: list[dict[str, Any]]) -> None:
    write_json(ws / TABLES_DIR / TABLES_INDEX, items)


def table_dir(ws: Path) -> Path:
    return subdir(ws, TABLES_DIR)


# --------------------------------------------------------------------------- #
# housekeeping
# --------------------------------------------------------------------------- #
def cleanup_old_workspaces(base: Path, ttl_hours: int) -> int:
    """Delete working folders that were not touched for `ttl_hours`."""
    if not base.exists():
        return 0
    cutoff = time.time() - ttl_hours * 3600
    removed = 0
    for child in base.iterdir():
        if not child.is_dir():
            continue
        try:
            last_touch = max(
                (p.stat().st_mtime for p in child.rglob("*") if p.is_file()),
                default=child.stat().st_mtime,
            )
            if last_touch < cutoff:
                shutil.rmtree(child, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    return removed


def delete_workspace(base: Path, sid: str) -> None:
    shutil.rmtree(base / sid, ignore_errors=True)
