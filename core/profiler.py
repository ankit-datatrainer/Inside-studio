"""Automatic data understanding (the notebook's "what do I have?" step).

Detection is schema-agnostic: it works for sales files, HR files, logs,
sensors, finance data... anything tabular.

For every column we detect a semantic `kind`:

    numeric | datetime | boolean | categorical | text | identifier | empty

and compute the statistics / value counts behind every screen of the app.
"""
from __future__ import annotations

import math
import re
from typing import Any

import numpy as np
import pandas as pd

KIND_NUMERIC = "numeric"
KIND_DATETIME = "datetime"
KIND_BOOLEAN = "boolean"
KIND_CATEGORICAL = "categorical"
KIND_TEXT = "text"
KIND_IDENTIFIER = "identifier"
KIND_EMPTY = "empty"

KIND_LABELS = {
    KIND_NUMERIC: "Number",
    KIND_DATETIME: "Date / Time",
    KIND_BOOLEAN: "Yes / No",
    KIND_CATEGORICAL: "Category",
    KIND_TEXT: "Text",
    KIND_IDENTIFIER: "Identifier / Key",
    KIND_EMPTY: "Empty",
}

MISSING_TOKENS = {
    "", "na", "n/a", "nan", "null", "none", "nil", "-", "--", "?", "unknown",
    "missing", "#n/a", "#null!", "<na>",
}

_ID_NAME_RE = re.compile(r"(^|[_\s])(id|ids|key|code|ref|no|num|number|uuid|guid)($|[_\s])", re.I)
_MONEY_NAME_RE = re.compile(
    r"(revenue|sales|sale|amount|amt|price|value|cost|profit|margin|income|gmv|spend|fee|charge|total|balance|salary|budget)",
    re.I,
)
_QTY_NAME_RE = re.compile(
    r"(^|[_\s])(qty|quantity|quantities|units|unit_count|units_sold|count|counts|volume|items?|"
    r"num_orders|order_count|orders_count|total_orders)($|[_\s])",
    re.I,
)
_PCT_NAME_RE = re.compile(r"(pct|percent|percentage|%|rate|ratio)", re.I)
_RATING_NAME_RE = re.compile(r"(rating|score|stars?|review|satisfaction|nps)", re.I)
_DATE_NAME_RE = re.compile(r"(date|time|day|month|year|created|updated|timestamp)", re.I)

_SAMPLE_SIZE = 800


# --------------------------------------------------------------------------- #
# detection helpers
# --------------------------------------------------------------------------- #
def _sample(series: pd.Series, size: int = _SAMPLE_SIZE) -> pd.Series:
    nonnull = series.dropna()
    if len(nonnull) > size:
        return nonnull.sample(size, random_state=7)
    return nonnull


def _looks_like_number(series: pd.Series) -> float:
    """Parse-rate when currency symbols / commas / % are stripped."""
    values = _sample(series).astype(str).str.strip()
    if values.empty:
        return 0.0
    cleaned = (
        values
        .str.replace(r"[^\d.,\-+eE]", "", regex=True)
        .str.replace(",", "", regex=False)
    )
    parsed = pd.to_numeric(cleaned, errors="coerce")
    return float(parsed.notna().mean())


def _looks_like_datetime(series: pd.Series) -> float:
    values = _sample(series)
    if values.empty:
        return 0.0
    parsed = pd.to_datetime(values, errors="coerce", format="mixed")
    return float(parsed.notna().mean())


def detect_kind(series: pd.Series, name: str = "") -> str:
    if series.dropna().empty:
        return KIND_EMPTY

    name = str(name)
    nunique = series.nunique(dropna=True)
    non_null = series.notna().sum()
    unique_ratio = nunique / max(non_null, 1)

    if pd.api.types.is_bool_dtype(series):
        return KIND_BOOLEAN

    if pd.api.types.is_datetime64_any_dtype(series):
        return KIND_DATETIME

    if pd.api.types.is_numeric_dtype(series):
        # integer columns with almost all-unique values that are named like ids
        if _ID_NAME_RE.search(name) and unique_ratio > 0.9:
            return KIND_IDENTIFIER
        if nunique <= 2 and non_null > 0 and set(series.dropna().unique()) <= {0, 1, 0.0, 1.0}:
            return KIND_BOOLEAN
        return KIND_NUMERIC

    # object / string columns
    stripped = series.astype(str).str.strip().str.lower()
    if stripped.isin(MISSING_TOKENS).sum() == len(series):
        return KIND_EMPTY

    # yes/no columns stored as text
    lowcard = set(stripped.dropna().unique()) - MISSING_TOKENS
    if len(lowcard) <= 2 and lowcard <= {"yes", "no", "y", "n", "true", "false", "0", "1"}:
        return KIND_BOOLEAN

    number_rate = _looks_like_number(series)
    date_rate = _looks_like_datetime(series)

    # Slash/dot dates ("05/02/2025") also look numeric once separators are
    # stripped, so prefer date detection when the values clearly look like
    # dates (or the header is date-like). Pure 4-digit year columns stay numeric.
    samples = _sample(series).astype(str).str.strip()
    date_shape = samples.str.contains(
        r"[/\-]|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\b",
        case=False, regex=True,
    ).mean() >= 0.8
    year_only = samples.str.fullmatch(r"\d{4}").mean() >= 0.9
    if date_rate >= 0.8 and (date_shape or is_date_name(name)) and not year_only:
        return KIND_DATETIME

    if number_rate >= 0.9:
        # numeric stored as text  (e.g. "1,299.00", "₹599")
        if _ID_NAME_RE.search(name) and unique_ratio > 0.9:
            return KIND_IDENTIFIER
        return KIND_NUMERIC

    if date_rate >= 0.8:
        return KIND_DATETIME

    if unique_ratio >= 0.95 and len(series) > 5:
        return KIND_IDENTIFIER

    mean_len = _sample(series).astype(str).str.len().mean() if non_null else 0
    if nunique <= 50 or unique_ratio <= 0.25:
        return KIND_CATEGORICAL
    if nunique <= 200 and mean_len <= 30:
        return KIND_CATEGORICAL
    return KIND_TEXT


def is_money_column(name: str) -> bool:
    return bool(_MONEY_NAME_RE.search(str(name))) and not _PCT_NAME_RE.search(str(name))


def is_quantity_column(name: str) -> bool:
    return bool(_QTY_NAME_RE.search(str(name)))


def is_percent_column(name: str) -> bool:
    return bool(_PCT_NAME_RE.search(str(name)))


def is_rating_column(name: str) -> bool:
    return bool(_RATING_NAME_RE.search(str(name)))


def is_date_name(name: str) -> bool:
    return bool(_DATE_NAME_RE.search(str(name)))


# --------------------------------------------------------------------------- #
# column stats
# --------------------------------------------------------------------------- #
def _json_number(x: Any) -> float | int | None:
    if x is None:
        return None
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.floating, float)):
        if math.isnan(x) or math.isinf(x):
            return None
        return round(float(x), 4)
    return x


def column_profile(df: pd.DataFrame, col: str) -> dict[str, Any]:
    series = df[col]
    kind = detect_kind(series, col)
    non_null = int(series.notna().sum())
    total = int(len(series))
    null_count = total - non_null

    profile: dict[str, Any] = {
        "name": str(col),
        "dtype": str(series.dtype),
        "kind": kind,
        "kind_label": KIND_LABELS.get(kind, kind),
        "non_null": non_null,
        "null_count": null_count,
        "null_pct": round(null_count / total * 100, 2) if total else 0.0,
        "unique": int(series.nunique(dropna=True)),
        "unique_ratio": round(series.nunique(dropna=True) / max(non_null, 1), 4),
        "samples": [str(v) for v in series.dropna().unique()[:5]],
        "flags": {
            "money": is_money_column(col) and kind == KIND_NUMERIC,
            "quantity": is_quantity_column(col) and kind in (KIND_NUMERIC, KIND_IDENTIFIER),
            "percent": is_percent_column(col),
            "rating": is_rating_column(col),
            "date_name": is_date_name(col),
        },
    }

    if kind == KIND_NUMERIC:
        numeric = pd.to_numeric(series, errors="coerce")
        profile["stats"] = {
            "min": _json_number(numeric.min()),
            "max": _json_number(numeric.max()),
            "mean": _json_number(numeric.mean()),
            "median": _json_number(numeric.median()),
            "std": _json_number(numeric.std()),
            "sum": _json_number(numeric.sum()),
            "q1": _json_number(numeric.quantile(0.25)),
            "q3": _json_number(numeric.quantile(0.75)),
        }
        profile["top_values"] = _top_values(series)
    elif kind == KIND_DATETIME:
        dates = pd.to_datetime(series, errors="coerce", format="mixed")
        profile["stats"] = {
            "min": str(dates.min()),
            "max": str(dates.max()),
        }
        profile["top_values"] = []
    elif kind == KIND_IDENTIFIER:
        profile["stats"] = {}
        profile["top_values"] = []
    else:
        profile["stats"] = {}
        profile["top_values"] = _top_values(series)

    return profile


def _top_values(series: pd.Series, limit: int = 8) -> list[dict[str, Any]]:
    try:
        counts = series.astype(str).str.strip().replace(list(MISSING_TOKENS), np.nan).value_counts()
    except Exception:
        return []
    total = int(counts.sum()) or 1
    out = []
    for value, count in counts.head(limit).items():
        out.append({
            "value": str(value),
            "count": int(count),
            "pct": round(int(count) / total * 100, 2),
        })
    return out


# --------------------------------------------------------------------------- #
# frame-level profile
# --------------------------------------------------------------------------- #
def profile_frame(df: pd.DataFrame) -> dict[str, Any]:
    columns = [column_profile(df, c) for c in df.columns]

    kinds: dict[str, list[str]] = {}
    for col in columns:
        kinds.setdefault(col["kind"], []).append(col["name"])

    duplicate_rows = int(df.duplicated().sum())
    duplicate_pct = round(duplicate_rows / max(len(df), 1) * 100, 2)

    numeric_cols = kinds.get(KIND_NUMERIC, [])
    date_cols = kinds.get(KIND_DATETIME, [])
    category_cols = kinds.get(KIND_CATEGORICAL, [])
    text_cols = kinds.get(KIND_TEXT, [])
    id_cols = kinds.get(KIND_IDENTIFIER, [])

    total_cells = max(len(df) * max(df.shape[1], 1), 1)
    missing_cells = int(df.isna().sum().sum())

    overview = {
        "rows": int(len(df)),
        "cols": int(df.shape[1]),
        "memory_kb": round(df.memory_usage(deep=True).sum() / 1024, 1),
        "missing_cells": missing_cells,
        "missing_pct": round(missing_cells / total_cells * 100, 2),
        "duplicate_rows": duplicate_rows,
        "duplicate_pct": duplicate_pct,
        "kinds": {k: v for k, v in kinds.items()},
        "numeric_cols": numeric_cols,
        "date_cols": date_cols,
        "category_cols": category_cols,
        "text_cols": text_cols,
        "id_cols": id_cols,
        "money_cols": [c["name"] for c in columns if c["flags"]["money"]],
        "quantity_cols": [c["name"] for c in columns if c["flags"]["quantity"]],
        "rating_cols": [c["name"] for c in columns if c["flags"]["rating"]],
        "datetime_span": None,
    }

    if date_cols:
        for col in date_cols:
            try:
                dates = pd.to_datetime(df[col], errors="coerce", format="mixed")
                if dates.notna().any():
                    overview["datetime_span"] = {
                        "column": col,
                        "min": str(dates.min().date()),
                        "max": str(dates.max().date()),
                        "days": int((dates.max() - dates.min()).days),
                    }
                    break
            except Exception:
                continue

    # describe table (numeric + datetime columns, same idea as df.describe())
    describe_table = _describe_table(df, columns)

    # correlation matrix for numeric columns
    corr = _correlation(df, numeric_cols)

    preview_head = _records(df.head(10))
    preview_tail = _records(df.tail(5))

    quality_notes = _quality_notes(columns, overview)

    return {
        "overview": overview,
        "columns": columns,
        "describe": describe_table,
        "correlation": corr,
        "preview_head": preview_head,
        "preview_tail": preview_tail,
        "quality_notes": quality_notes,
        "kinds": kinds,
    }


def _describe_table(df: pd.DataFrame, columns: list[dict[str, Any]]) -> dict[str, Any]:
    stat_cols = [c["name"] for c in columns if c["kind"] in (KIND_NUMERIC, KIND_DATETIME)]
    if not stat_cols:
        return {"columns": [], "rows": []}
    sub = df[stat_cols].copy()
    for c in stat_cols:
        kind = next(x["kind"] for x in columns if x["name"] == c)
        if kind == KIND_NUMERIC:
            sub[c] = pd.to_numeric(sub[c], errors="coerce")
        else:
            sub[c] = pd.to_datetime(sub[c], errors="coerce", format="mixed")
    try:
        desc = sub.describe(include=[np.number, "datetime64[ns]"])
    except Exception:
        try:
            desc = sub.describe()
        except Exception:
            return {"columns": [], "rows": []}
    rows = []
    for idx, row in desc.iterrows():
        rows.append({
            "stat": str(idx),
            "cells": [_format_stat(v) for v in row.tolist()],
        })
    return {"columns": stat_cols, "rows": rows}


def _format_stat(value: Any) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "-"
    if isinstance(value, (pd.Timestamp, np.datetime64)):
        return str(pd.Timestamp(value).date())
    if isinstance(value, (np.integer, int)):
        return f"{int(value):,}"
    if isinstance(value, (np.floating, float)):
        return f"{float(value):,.2f}"
    return str(value)


def _correlation(df: pd.DataFrame, numeric_cols: list[str]) -> dict[str, Any] | None:
    cols = numeric_cols[:12]
    if len(cols) < 2:
        return None
    try:
        numeric = df[cols].apply(pd.to_numeric, errors="coerce")
        corr = numeric.corr(numeric_only=True).round(3)
    except Exception:
        return None
    matrix = []
    for i, row_name in enumerate(corr.index):
        row = []
        for j, col_name in enumerate(corr.columns):
            value = corr.iloc[i, j]
            row.append(None if pd.isna(value) else float(value))
        matrix.append({"name": str(row_name), "cells": row})
    return {"columns": [str(c) for c in corr.columns], "rows": matrix}


def _records(df: pd.DataFrame) -> dict[str, Any]:
    safe = df.copy()
    for col in safe.columns:
        if pd.api.types.is_datetime64_any_dtype(safe[col]):
            safe[col] = safe[col].astype(str)
    safe = safe.where(pd.notna(safe), None)
    return {
        "columns": [str(c) for c in safe.columns],
        "rows": [
            ["" if v is None else (f"{v:,}" if isinstance(v, (int, float)) and not isinstance(v, bool) else str(v))
             for v in row]
            for row in safe.itertuples(index=False)
        ],
    }


def _quality_notes(columns: list[dict[str, Any]], overview: dict[str, Any]) -> list[dict[str, str]]:
    notes: list[dict[str, str]] = []

    if overview["duplicate_rows"]:
        notes.append({
            "level": "warn",
            "title": f"{overview['duplicate_rows']} duplicate rows",
            "text": "The Clean step can drop exact duplicates in one click.",
        })
    else:
        notes.append({
            "level": "ok",
            "title": "No duplicate rows",
            "text": "Every row in the file is unique.",
        })

    high_null = [c for c in columns if c["null_pct"] >= 40]
    some_null = [c for c in columns if 0 < c["null_pct"] < 40]
    if high_null:
        names = ", ".join(c["name"] for c in high_null[:4])
        notes.append({
            "level": "warn",
            "title": f"{len(high_null)} column(s) mostly empty (>=40%)",
            "text": f"Consider dropping: {names}.",
        })
    if some_null:
        names = ", ".join(f"{c['name']} ({c['null_pct']}%)" for c in some_null[:4])
        notes.append({
            "level": "info",
            "title": f"{len(some_null)} column(s) contain missing values",
            "text": f"{names}. Suggested fill strategies are ready in the Clean step.",
        })
    if not high_null and not some_null:
        notes.append({
            "level": "ok",
            "title": "No missing values",
            "text": "All columns are complete.",
        })

    text_numbers = [c for c in columns if c["kind"] == KIND_NUMERIC and not pd.api.types.is_numeric_dtype(pd.Series([], dtype=object)) and c["dtype"] in ("object", "string")]
    if text_numbers:
        names = ", ".join(c["name"] for c in text_numbers[:4])
        notes.append({
            "level": "info",
            "title": "Numbers stored as text",
            "text": f"{names} can be converted to real numbers (Clean step).",
        })

    text_dates = [c for c in columns if c["kind"] == KIND_DATETIME and "datetime" not in c["dtype"]]
    if text_dates:
        names = ", ".join(c["name"] for c in text_dates[:4])
        notes.append({
            "level": "info",
            "title": "Dates stored as text",
            "text": f"{names} can be parsed into real dates (Clean step).",
        })

    id_cols = overview["id_cols"]
    if id_cols:
        notes.append({
            "level": "ok",
            "title": "Identifier columns detected",
            "text": f"{', '.join(id_cols[:4])} look like unique keys and are excluded from charts by default.",
        })

    if overview["datetime_span"]:
        span = overview["datetime_span"]
        notes.append({
            "level": "ok",
            "title": f"Time range in {span['column']}",
            "text": f"{span['min']} to {span['max']} ({span['days']} days) - time trends are available.",
        })

    return notes
