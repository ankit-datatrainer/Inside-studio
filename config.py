"""Configuration for Insight Studio.

Everything can be overridden with environment variables so the same code
runs unchanged on your laptop, a VPS, Hostinger, AWS or inside Docker.
"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _env_path(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value).expanduser().resolve() if value else default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


class Config:
    BASE_DIR = BASE_DIR

    # --- Flask ---
    SECRET_KEY = os.environ.get("SECRET_KEY", "change-me-in-production")
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "0") == "1"
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 24 * 7  # 7 days

    # --- Uploads / workspaces ---
    WORKSPACE_DIR = _env_path("WORKSPACE_DIR", BASE_DIR / "workspaces")
    UPLOAD_DIR = _env_path("UPLOAD_DIR", BASE_DIR / "uploads")
    MAX_CONTENT_LENGTH = _env_int("MAX_UPLOAD_MB", 100) * 1024 * 1024
    ALLOWED_EXTENSIONS = {
        ".xlsx", ".xls", ".xlsm",
        ".csv", ".tsv", ".txt",
        ".pdf",
    }

    # How long (hours) unused working folders are kept before janitor deletes them
    WORKSPACE_TTL_HOURS = _env_int("WORKSPACE_TTL_HOURS", 48)

    # Chart defaults
    DEFAULT_DPI = _env_int("CHART_DPI", 130)
    PREVIEW_DPI = _env_int("CHART_PREVIEW_DPI", 110)

    # Deployment
    HOST = os.environ.get("HOST", "127.0.0.1")
    PORT = _env_int("PORT", 5000)
    DEBUG = os.environ.get("FLASK_DEBUG", "0") == "1"

    # AI / Model Backend
    OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "nvidia/nemotron-3.5-super-vl:free")
    OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
    if not OPENROUTER_API_KEY:
        _k_file = BASE_DIR / ".openrouter_key"
        if _k_file.exists():
            try:
                OPENROUTER_API_KEY = _k_file.read_text(encoding="utf-8").strip()
            except Exception:
                pass

    # Misc
    APP_NAME = os.environ.get("APP_NAME", "Insight Studio")
    SAMPLE_FILE = BASE_DIR / "sample_data" / "pandas_practice_sales_data.xlsx"
