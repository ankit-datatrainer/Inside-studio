"""Automated, data-type agnostic cleaning.

`suggest_plan()` inspects any dataframe and proposes ready-to-run cleaning
steps with sensible defaults; `apply_plan()` executes the ones the user
enabled and returns a full audit log of what changed.

Step types
----------
fix_column_names   standardise header names
trim_text          strip whitespace / collapse double spaces
normalize_values   unify category spellings (Mumbai / mumbai / MUMBAI)
convert_numbers    text like "1,299.00" or "INR 599" -> real numbers
convert_dates      text dates -> datetime
drop_columns       remove low-value columns
drop_empty_rows    remove rows that are completely empty
drop_duplicates    remove exact duplicate rows
drop_duplicate_keys remove duplicates by a key column
fill_missing       per-column strategy (median / mean / mode / ...)
cap_outliers       winsorize numeric outliers (IQR)
derived_columns    create calculated columns (like Gross_Amount)
"""
from __future__ import annotations

import copy
import re
from typing import Any

import numpy as np
import pandas as pd

from . import profiler

STEP_LIBRARY: dict[str, str] = {
    "fix_column_names": "Standardise column names",
    "trim_text": "Trim spaces in text columns",
    "normalize_values": "Unify category spellings",
    "convert_numbers": "Convert text to numbers",
    "convert_dates": "Parse text into dates",
    "drop_columns": "Drop selected columns",
    "drop_empty_rows": "Remove empty rows",
    "drop_duplicates": "Remove duplicate rows",
    "drop_duplicate_keys": "Remove duplicate keys",
    "fill_missing": "Fill missing values",
    "cap_outliers": "Limit extreme outliers",
    "derived_columns": "Create calculated columns",
}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _is_text_like(series: pd.Series) -> bool:
    return pd.api.types.is_object_dtype(series) or pd.api.types.is_string_dtype(series)


def _snake(name: str) -> str:
    out = re.sub(r"[^\w]+", "_", str(name).strip()).strip("_")
    out = re.sub(r"_+", "_", out)
    return out or "column"


def _column_rename_mapping(df: pd.DataFrame) -> dict[str, str]:
    """Map every header to a unique snake_case name (collisions get _2, _3...)."""
    mapping: dict[str, str] = {}
    used: set[str] = set()
    for col in df.columns:
        base = _snake(col)
        name = base
        i = 2
        while name in used:
            name = f"{base}_{i}"
            i += 1
        used.add(name)
        mapping[col] = name
    return mapping


def _rewrite_expression(expression: str, mapping: dict[str, str]) -> str:
    """Rename columns referenced inside a formula after headers were standardised."""
    out = str(expression)
    for old in sorted(mapping, key=len, reverse=True):
        new = mapping[old]
        if new == old:
            continue
        out = re.sub(r"(?<![\w])" + re.escape(str(old)) + r"(?![\w])", new, out)
    return out


def _remap_step(step: dict[str, Any], mapping: dict[str, str]) -> dict[str, Any]:
    """Return a copy of a cleaning step with old column names replaced."""
    new = copy.deepcopy(step)
    new["columns"] = [mapping.get(c, c) for c in (new.get("columns") or [])]
    params = new.setdefault("params", {})
    if isinstance(params.get("columns"), list):
        params["columns"] = [mapping.get(c, c) for c in params["columns"]]
    if isinstance(params.get("column"), str):
        params["column"] = mapping.get(params["column"], params["column"])
    if isinstance(params.get("strategies"), dict):
        params["strategies"] = {mapping.get(k, k): v for k, v in params["strategies"].items()}
    if isinstance(params.get("rows"), list):
        for row in params["rows"]:
            if isinstance(row, dict) and row.get("expression"):
                row["expression"] = _rewrite_expression(row["expression"], mapping)
    return new


def _safe_env_names(df: pd.DataFrame) -> dict[str, str]:
    mapping: dict[str, str] = {}
    used: set[str] = set()
    for col in df.columns:
        safe = re.sub(r"[\W]+", "_", str(col)).strip("_")
        if not safe or safe[0].isdigit():
            safe = f"col_{safe}"
        base = safe
        i = 2
        while safe in used:
            safe = f"{base}_{i}"
            i += 1
        used.add(safe)
        mapping[str(col)] = safe
    return mapping


def evaluate_expression(df: pd.DataFrame, expression: str) -> pd.Series:
    """Evaluate a user expression like: Quantity * Unit_Price * (1 - Discount_Pct/100)."""
    mapping = _safe_env_names(df)
    env: dict[str, Any] = {"__builtins__": {}, "np": np,
                           "abs": abs, "round": round, "min": min, "max": max,
                           "where": np.where}
    for col in df.columns:
        env[mapping[str(col)]] = df[col]

    expr = re.sub(r"`([^`]+)`", lambda m: mapping.get(m.group(1), m.group(1)), expression)
    try:
        result = eval(expr, env)  # noqa: S307 - restricted namespace, local tool
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Cannot evaluate '{expression}': {exc}") from exc

    if isinstance(result, (int, float)):
        result = pd.Series([result] * len(df), index=df.index)
    series = pd.Series(result, index=df.index) if not isinstance(result, pd.Series) else result
    return series


# --------------------------------------------------------------------------- #
# suggestions
# --------------------------------------------------------------------------- #
def _missing_strategy_defaults(df: pd.DataFrame, columns: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    strategies: dict[str, dict[str, Any]] = {}
    for col in columns:
        name, kind, null_pct = col["name"], col["kind"], col["null_pct"]
        if col["null_count"] == 0:
            continue
        if null_pct >= 60:
            strategies[name] = {"strategy": "drop_column", "value": None}
        elif kind == profiler.KIND_NUMERIC:
            strategies[name] = {"strategy": "median", "value": None}
        elif kind in (profiler.KIND_DATETIME, profiler.KIND_IDENTIFIER, profiler.KIND_TEXT):
            strategies[name] = {"strategy": "leave", "value": None}
        else:  # categorical / boolean
            strategies[name] = {"strategy": "mode", "value": None}
    return strategies


def suggest_plan(df: pd.DataFrame, profile: dict[str, Any]) -> list[dict[str, Any]]:
    columns = profile["columns"]
    steps: list[dict[str, Any]] = []

    # 1. column names ---------------------------------------------------------
    messy = [c["name"] for c in columns if _snake(c["name"]) != str(c["name"])]
    if messy:
        steps.append({
            "id": "fix_column_names",
            "type": "fix_column_names",
            "title": STEP_LIBRARY["fix_column_names"],
            "description": "Convert headers to uniform snake_case so formulas and charts are easy to read.",
            "columns": messy,
            "default_enabled": True,
            "impact": f"{len(messy)} column name(s) change, e.g. '{messy[0]}' -> '{_snake(messy[0])}'",
            "params": {},
        })

    # 2. trim whitespace ------------------------------------------------------
    trim_cols = []
    for col in columns:
        if not _is_text_like(df[col["name"]]):
            continue
        values = df[col["name"]].dropna().astype(str)
        if values.empty:
            continue
        if (values != values.str.strip()).any() or values.str.contains(r"\s{2,}", regex=True).any():
            trim_cols.append(col["name"])
    if trim_cols:
        steps.append({
            "id": "trim_text",
            "type": "trim_text",
            "title": STEP_LIBRARY["trim_text"],
            "description": "Remove leading/trailing spaces and collapse repeated spaces so text matches.",
            "columns": trim_cols,
            "default_enabled": True,
            "impact": f"{len(trim_cols)} text column(s) affected",
            "params": {},
        })

    # 3. unify category spellings --------------------------------------------
    norm_cols = []
    for col in columns:
        if col["kind"] != profiler.KIND_CATEGORICAL:
            continue
        values = df[col["name"]].dropna().astype(str)
        if values.empty:
            continue
        lowered = set(values.str.lower().str.strip())
        if len(lowered) < values.nunique():
            norm_cols.append(col["name"])
    if norm_cols:
        steps.append({
            "id": "normalize_values",
            "type": "normalize_values",
            "title": STEP_LIBRARY["normalize_values"],
            "description": "Treat 'Mumbai', 'mumbai ' and 'MUMBAI' as one category (keeps the most common spelling).",
            "columns": norm_cols,
            "default_enabled": True,
            "impact": f"{len(norm_cols)} category column(s) affected",
            "params": {},
        })

    # 4. numeric text ---------------------------------------------------------
    num_cols = [c["name"] for c in columns
                if c["kind"] == profiler.KIND_NUMERIC and not pd.api.types.is_numeric_dtype(df[c["name"]])]
    if num_cols:
        steps.append({
            "id": "convert_numbers",
            "type": "convert_numbers",
            "title": STEP_LIBRARY["convert_numbers"],
            "description": "Strip currency symbols/commas and turn these columns into real numbers.",
            "columns": num_cols,
            "default_enabled": True,
            "impact": f"{len(num_cols)} column(s): {', '.join(num_cols[:4])}",
            "params": {},
        })

    # 5. date text ------------------------------------------------------------
    date_cols = [c["name"] for c in columns
                 if c["kind"] == profiler.KIND_DATETIME and not pd.api.types.is_datetime64_any_dtype(df[c["name"]])]
    if date_cols:
        steps.append({
            "id": "convert_dates",
            "type": "convert_dates",
            "title": STEP_LIBRARY["convert_dates"],
            "description": "Parse these columns into real dates so time trends work.",
            "columns": date_cols,
            "default_enabled": True,
            "impact": f"{len(date_cols)} column(s): {', '.join(date_cols[:4])}",
            "params": {},
        })

    # 6. drop useless columns -------------------------------------------------
    drop_cols = [c["name"] for c in columns if c["kind"] == profiler.KIND_EMPTY]
    if drop_cols:
        steps.append({
            "id": "drop_empty_columns",
            "type": "drop_columns",
            "title": "Drop fully empty columns",
            "description": "These columns contain no data at all.",
            "columns": drop_cols,
            "default_enabled": True,
            "impact": f"{len(drop_cols)} empty column(s)",
            "params": {"columns": drop_cols},
        })

    # 7. empty rows / duplicates ---------------------------------------------
    if df.isna().all(axis=1).any():
        steps.append({
            "id": "drop_empty_rows",
            "type": "drop_empty_rows",
            "title": STEP_LIBRARY["drop_empty_rows"],
            "description": "Rows where every column is empty carry no information.",
            "columns": [],
            "default_enabled": True,
            "impact": f"{int(df.isna().all(axis=1).sum())} row(s)",
            "params": {},
        })

    dup_count = int(df.duplicated().sum())
    if dup_count:
        steps.append({
            "id": "drop_duplicates",
            "type": "drop_duplicates",
            "title": STEP_LIBRARY["drop_duplicates"],
            "description": "Remove rows that are exact copies of another row.",
            "columns": [],
            "default_enabled": True,
            "impact": f"{dup_count} duplicate row(s)",
            "params": {},
        })

    key_candidates = []
    for col in columns:
        if col["kind"] == profiler.KIND_IDENTIFIER:
            dups = int(df[col["name"]].duplicated().sum())
            if dups:
                key_candidates.append((col["name"], dups))
    if key_candidates:
        name, dups = key_candidates[0]
        steps.append({
            "id": "drop_duplicate_keys",
            "type": "drop_duplicate_keys",
            "title": STEP_LIBRARY["drop_duplicate_keys"],
            "description": f"'{name}' looks like a unique key but has repeated values.",
            "columns": [name],
            "default_enabled": False,
            "impact": f"{dups} repeated value(s) in {name}",
            "params": {"column": name, "keep": "first"},
        })

    # 8. missing values -------------------------------------------------------
    missing_cols = [c for c in columns if c["null_count"] > 0]
    if missing_cols:
        strategies = _missing_strategy_defaults(df, columns)
        n = sum(1 for s in strategies.values() if s["strategy"] not in ("leave", "drop_column"))
        steps.append({
            "id": "fill_missing",
            "type": "fill_missing",
            "title": STEP_LIBRARY["fill_missing"],
            "description": "Per-column strategy chosen from the column type: median for numbers, "
                           "most common value for categories. Change any of them below.",
            "columns": [c["name"] for c in missing_cols],
            "default_enabled": True,
            "impact": f"{sum(c['null_count'] for c in missing_cols)} missing value(s) in "
                      f"{len(missing_cols)} column(s); {n} auto-fill strategy(ies) ready",
            "params": {"strategies": strategies},
        })

    # 9. outliers -------------------------------------------------------------
    outlier_cols = []
    details = []
    numeric_cols = [c["name"] for c in columns if c["kind"] == profiler.KIND_NUMERIC]
    for name in numeric_cols:
        series = pd.to_numeric(df[name], errors="coerce").dropna()
        if len(series) < 8:
            continue
        q1, q3 = series.quantile(0.25), series.quantile(0.75)
        iqr = q3 - q1
        if iqr <= 0:
            continue
        low, high = q1 - 1.5 * iqr, q3 + 1.5 * iqr
        count = int(((series < low) | (series > high)).sum())
        if count:
            outlier_cols.append(name)
            details.append(f"{name}: {count}")
    if outlier_cols:
        steps.append({
            "id": "cap_outliers",
            "type": "cap_outliers",
            "title": STEP_LIBRARY["cap_outliers"],
            "description": "Clamp values outside 1.5 x IQR to the nearest boundary. "
                           "Keeps the row but protects averages and totals. Off by default.",
            "columns": outlier_cols,
            "default_enabled": False,
            "impact": "Outliers found - " + "; ".join(details[:5]),
            "params": {"columns": outlier_cols, "factor": 1.5},
        })

    # 10. derived columns -----------------------------------------------------
    preset = _derived_presets(df, profile)
    steps.append({
        "id": "derived_columns",
        "type": "derived_columns",
        "title": STEP_LIBRARY["derived_columns"],
        "description": "Excel-style formulas on your columns. Presets are offered when the data looks "
                       "like orders (quantity x price, discounts...). Add your own rows too.",
        "columns": [],
        "default_enabled": bool(preset),
        "impact": f"{len(preset)} suggested formula(s)" if preset else "No preset detected - add your own",
        "params": {"rows": preset},
    })

    return steps


def _derived_presets(df: pd.DataFrame, profile: dict[str, Any]) -> list[dict[str, str]]:
    """Detect the notebook's revenue pattern (and similar) automatically."""
    cols = {c["name"]: c for c in profile["columns"]}
    numeric = [c["name"] for c in profile["columns"] if c["kind"] == profiler.KIND_NUMERIC]

    qty = next((c for c in numeric if profiler.is_quantity_column(c)), None)
    price = next((c for c in numeric if profiler.is_money_column(c) and "unit" in c.lower()), None)
    price = price or next((c for c in numeric if profiler.is_money_column(c)), None)
    pct = next((c for c in numeric if profiler.is_percent_column(c)), None)

    rows: list[dict[str, str]] = []
    if qty and price:
        gross_name = "Gross_Amount" if "Gross_Amount" not in df.columns else "Gross_Amount_Calc"
        rows.append({
            "name": gross_name,
            "expression": f"{qty} * {price}",
            "description": f"{qty} x {price}",
        })
        if pct:
            rows.append({
                "name": "Discount_Amount",
                "expression": f"({gross_name} * {pct} / 100)",
                "description": f"{gross_name} x {pct}%",
            })
            rows.append({
                "name": "Net_Revenue",
                "expression": f"({gross_name} - Discount_Amount)",
                "description": f"{gross_name} minus discount",
            })
    elif price:
        rows.append({
            "name": "Monthly_Value",
            "expression": f"{price}",
            "description": "(example - edit or remove)",
        })
    return rows


# --------------------------------------------------------------------------- #
# application
# --------------------------------------------------------------------------- #
def _before_after(before: pd.DataFrame, after: pd.DataFrame) -> dict[str, Any]:
    return {
        "rows": [int(len(before)), int(len(after))],
        "cols": [int(before.shape[1]), int(after.shape[1])],
        "missing": [int(before.isna().sum().sum()), int(after.isna().sum().sum())],
        "duplicates": [int(before.duplicated().sum()), int(after.duplicated().sum())],
    }


def apply_plan(df: pd.DataFrame, steps: list[dict[str, Any]]) -> tuple[pd.DataFrame, dict[str, Any]]:
    work = df.copy()
    log: list[dict[str, Any]] = []

    # When headers are standardised, later steps (and formula presets) still
    # carry the original names. Remap them so cleaning does not silently skip.
    rename_map: dict[str, str] = {}
    if any(s.get("enabled") and s.get("type") == "fix_column_names" for s in steps):
        rename_map = _column_rename_mapping(work)

    for step in steps:
        if not step.get("enabled", False):
            continue
        stype = step.get("type")
        use_step = _remap_step(step, rename_map) if rename_map and stype != "fix_column_names" else step
        try:
            work, detail = _apply_step(work, use_step)
            log.append({
                "id": step.get("id"),
                "title": step.get("title", stype),
                "detail": detail,
                "ok": True,
            })
        except Exception as exc:  # noqa: BLE001
            log.append({
                "id": step.get("id"),
                "title": step.get("title", stype),
                "detail": f"skipped: {exc}",
                "ok": False,
            })

    summary = _before_after(df, work)
    return work, {"steps": log, "before_after": summary}


def _apply_step(df: pd.DataFrame, step: dict[str, Any]) -> tuple[pd.DataFrame, str]:
    stype = step.get("type")
    params = step.get("params", {}) or {}
    columns = [c for c in (step.get("columns") or []) if c in df.columns]

    if stype == "fix_column_names":
        mapping = _column_rename_mapping(df)
        df = df.rename(columns=mapping)
        return df, f"renamed {len(mapping)} column(s)"

    if stype == "trim_text":
        changed = 0
        for col in columns:
            if not _is_text_like(df[col]):
                continue
            before = df[col].astype(str)
            cleaned = before.str.strip().str.replace(r"\s{2,}", " ", regex=True)
            changed += int((before != cleaned).sum())
            df[col] = cleaned.where(df[col].notna(), df[col])
        return df, f"trimmed {changed} value(s) in {len(columns)} column(s)"

    if stype == "normalize_values":
        fixed = 0
        for col in columns:
            if not _is_text_like(df[col]):
                continue
            series = df[col]
            key = series.astype(str).str.strip().str.lower()
            counts = key.value_counts()
            # canonical spelling = most common original form for each key
            canonical: dict[str, str] = {}
            for k in counts.index:
                originals = series[key == k].dropna().astype(str).str.strip()
                if not originals.empty:
                    canonical[k] = originals.value_counts().idxmax()
            new_values = key.map(lambda k: canonical.get(k, None)).where(series.notna(), None)
            fixed += int((new_values.fillna("__na__") != series.fillna("__na__")).sum())
            df[col] = new_values.where(series.notna(), series)
        return df, f"unified {fixed} value(s) in {len(columns)} column(s)"

    if stype == "convert_numbers":
        converted = 0
        for col in columns:
            cleaned = (
                df[col].astype(str)
                .str.strip()
                .str.replace(r"[^\d.,\-+eE]", "", regex=True)
                .str.replace(",", "", regex=False)
            )
            numeric = pd.to_numeric(cleaned, errors="coerce")
            converted += int(numeric.notna().sum())
            df[col] = numeric
        return df, f"converted {converted} value(s) to numbers"

    if stype == "convert_dates":
        for col in columns:
            df[col] = pd.to_datetime(df[col], errors="coerce", format="mixed")
        return df, f"parsed {len(columns)} column(s) as dates"

    if stype == "drop_columns":
        drop = [c for c in (params.get("columns") or columns) if c in df.columns]
        df = df.drop(columns=drop)
        return df, f"dropped {len(drop)} column(s): {', '.join(drop[:6])}"

    if stype == "drop_empty_rows":
        before = len(df)
        df = df.dropna(how="all")
        return df, f"removed {before - len(df)} empty row(s)"

    if stype == "drop_duplicates":
        before = len(df)
        df = df.drop_duplicates()
        return df.reset_index(drop=True), f"removed {before - len(df)} duplicate row(s)"

    if stype == "drop_duplicate_keys":
        col = params.get("column")
        if col not in df.columns:
            return df, "key column missing - skipped"
        before = len(df)
        df = df.drop_duplicates(subset=[col], keep=params.get("keep", "first"))
        return df.reset_index(drop=True), f"removed {before - len(df)} duplicate key row(s) in {col}"

    if stype == "fill_missing":
        strategies = params.get("strategies", {}) or {}
        filled, dropped_cols, dropped_rows = 0, [], 0
        for col, cfg in strategies.items():
            if col not in df.columns:
                continue
            strategy = (cfg or {}).get("strategy", "leave")
            value = (cfg or {}).get("value")
            series = df[col]
            if strategy == "leave":
                continue
            if strategy == "drop_column":
                df = df.drop(columns=[col])
                dropped_cols.append(col)
                continue
            if strategy == "drop_rows":
                before = len(df)
                df = df[df[col].notna()]
                dropped_rows += before - len(df)
                continue

            null_mask = series.isna()
            if not null_mask.any():
                continue

            if strategy == "median":
                fill_value = pd.to_numeric(series, errors="coerce").median()
            elif strategy == "mean":
                fill_value = pd.to_numeric(series, errors="coerce").mean()
            elif strategy == "mode":
                modes = series.mode(dropna=True)
                fill_value = modes.iloc[0] if not modes.empty else "Unknown"
            elif strategy == "zero":
                fill_value = 0
            elif strategy == "constant":
                fill_value = value if value not in (None, "") else "Unknown"
            elif strategy == "ffill":
                df[col] = series.ffill().bfill()
                filled += int(null_mask.sum())
                continue
            else:
                continue

            if fill_value is None or (isinstance(fill_value, float) and np.isnan(fill_value)):
                continue
            if strategy in ("median", "mean"):
                fill_value = round(float(fill_value), 2)
            df[col] = series.fillna(fill_value)
            filled += int(null_mask.sum())

        parts = [f"filled {filled} missing value(s)"]
        if dropped_cols:
            parts.append(f"dropped column(s): {', '.join(dropped_cols)}")
        if dropped_rows:
            parts.append(f"dropped {dropped_rows} incomplete row(s)")
        return df, "; ".join(parts)

    if stype == "cap_outliers":
        capped_cols, capped = 0, 0
        factor = float(params.get("factor", 1.5))
        for col in columns:
            series = pd.to_numeric(df[col], errors="coerce")
            q1, q3 = series.quantile(0.25), series.quantile(0.75)
            iqr = q3 - q1
            if pd.isna(iqr) or iqr <= 0:
                continue
            low, high = q1 - factor * iqr, q3 + factor * iqr
            out_of_range = ((series < low) | (series > high)).sum()
            if out_of_range:
                df[col] = series.clip(low, high)
                capped += int(out_of_range)
                capped_cols += 1
        return df, f"limited {capped} outlier value(s) across {capped_cols} column(s)"

    if stype == "derived_columns":
        rows = params.get("rows", []) or []
        created = []
        for row in rows:
            name = (row.get("name") or "").strip()
            expr = (row.get("expression") or "").strip()
            if not name or not expr:
                continue
            series = evaluate_expression(df, expr)
            df[name] = series
            created.append(name)
        return df, f"created {len(created)} column(s): {', '.join(created)}" if created else "nothing to create"

    return df, "no-op"


def preview_plan(df: pd.DataFrame, steps: list[dict[str, Any]], limit: int = 10) -> dict[str, Any]:
    """Apply to a copy and return sample before/after rows for the UI."""
    cleaned, log = apply_plan(df, steps)
    return {
        "log": log,
        "before_after": log["before_after"] if isinstance(log, dict) else None,
        "head": cleaned.head(limit),
    }
