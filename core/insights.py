"""Automatic insights: KPI cards, plain-English findings, analysis suggestions.

Everything here is generated from the detected schema, so it works on any
dataset - sales, HR, finance, inventory, sensors, ...

Outputs
-------
* build_kpis()             -> headline KPI cards
* build_sentences()        -> plain English findings ("Electronics makes up 43% ...")
* suggest_charts()         -> one-click chart presets
* build_auto_tables()      -> ready-made group-by analysis tables
* group_table()            -> free-form group-by used by the table builder
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from . import profiler

MAX_ROWS = 500_000  # safety valve for very large files


def _fmt_number(value: Any) -> str:
    if value is None or (isinstance(value, float) and (np.isnan(value) or np.isinf(value))):
        return "-"
    if isinstance(value, (np.integer, int)):
        return f"{int(value):,}"
    if isinstance(value, (np.floating, float)):
        absv = abs(value)
        if absv >= 1000:
            return f"{value:,.2f}"
        if absv >= 1:
            return f"{value:,.2f}"
        return f"{value:,.4f}"
    return str(value)


def _clean_number(x: Any) -> float | None:
    try:
        if x is None:
            return None
        f = float(x)
        if np.isnan(f) or np.isinf(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _money_rank(name: str) -> int:
    """Prefer real business totals over unit prices/costs."""
    n = str(name).lower()
    for i, key in enumerate(("revenue", "sales", "sale", "net", "gross", "profit", "income",
                             "amount", "amt", "gmv", "total", "value", "spend", "fee",
                             "charge", "balance", "salary", "budget", "cost", "price", "rate")):
        if key in n:
            return i
    return 99


# --------------------------------------------------------------------------- #
# KPIs
# --------------------------------------------------------------------------- #
def build_kpis(df: pd.DataFrame, profile: dict[str, Any]) -> list[dict[str, Any]]:
    ov = profile["overview"]
    kpis: list[dict[str, Any]] = []

    kpis.append({
        "id": "rows", "label": "Rows", "value": f"{ov['rows']:,}",
        "sub": f"{ov['cols']} columns", "icon": "rows",
    })

    money_cols = sorted(ov["money_cols"], key=_money_rank)[:2]
    for col in money_cols:
        series = pd.to_numeric(df[col], errors="coerce")
        lower = col.lower()
        if any(k in lower for k in ("price", "rate", "cost")):
            mean = _clean_number(series.mean())
            if mean is None:
                continue
            kpis.append({
                "id": f"avg_{col}", "label": f"Average {col.replace('_', ' ')}",
                "value": _fmt_number(mean),
                "sub": f"range {_fmt_number(_clean_number(series.min()))} - {_fmt_number(_clean_number(series.max()))}",
                "icon": "money",
            })
            continue
        total = _clean_number(series.sum())
        if total is None:
            continue
        kpis.append({
            "id": f"total_{col}", "label": f"Total {col.replace('_', ' ')}",
            "value": _fmt_number(total),
            "sub": f"avg {_fmt_number(_clean_number(series.mean()))} per row",
            "icon": "money",
        })

    for col in ov["quantity_cols"][:1]:
        series = pd.to_numeric(df[col], errors="coerce")
        total = _clean_number(series.sum())
        if total is not None:
            kpis.append({
                "id": f"sum_{col}", "label": f"Total {col.replace('_', ' ')}",
                "value": _fmt_number(total),
                "sub": f"avg {_fmt_number(_clean_number(series.mean()))} per row",
                "icon": "qty",
            })

    for col in ov["rating_cols"][:1]:
        series = pd.to_numeric(df[col], errors="coerce")
        mean = _clean_number(series.mean())
        if mean is not None:
            kpis.append({
                "id": f"avg_{col}", "label": f"Average {col.replace('_', ' ')}",
                "value": _fmt_number(mean),
                "sub": f"range {_fmt_number(_clean_number(series.min()))} - {_fmt_number(_clean_number(series.max()))}",
                "icon": "star",
            })

    if ov["datetime_span"]:
        span = ov["datetime_span"]
        kpis.append({
            "id": "span", "label": "Time range",
            "value": f"{span['days']} days",
            "sub": f"{span['min']} to {span['max']}",
            "icon": "calendar",
        })

    if len(kpis) < 4:
        # fall back to a generic numeric column so the header is never empty
        for col in ov["numeric_cols"][:3]:
            if any(k["id"].endswith(col) for k in kpis):
                continue
            series = pd.to_numeric(df[col], errors="coerce")
            kpis.append({
                "id": f"stat_{col}", "label": f"{col.replace('_', ' ')} (avg)",
                "value": _fmt_number(_clean_number(series.mean())),
                "sub": f"min {_fmt_number(_clean_number(series.min()))} / max {_fmt_number(_clean_number(series.max()))}",
                "icon": "number",
            })
            if len(kpis) >= 4:
                break

    kpis.append({
        "id": "missing", "label": "Missing values",
        "value": f"{ov['missing_pct']}%",
        "sub": f"{ov['missing_cells']:,} cells",
        "icon": "alert",
    })
    kpis.append({
        "id": "duplicates", "label": "Duplicate rows",
        "value": f"{ov['duplicate_rows']:,}",
        "sub": f"{ov['duplicate_pct']}% of rows",
        "icon": "copy",
    })
    return kpis


def discover_executive_kpis(
    df: pd.DataFrame,
    profile: dict[str, Any],
    audit: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Automatically discover high-impact executive business KPIs from dataset metrics and dimensions."""
    ov = profile.get("overview", {})
    kpis: list[dict[str, Any]] = []

    # Helper formatter
    def _fmt_executive(val: float | int | None, is_curr: bool = False) -> str:
        if val is None or pd.isna(val):
            return "N/A"
        p = "$" if is_curr else ""
        abs_v = abs(float(val))
        if abs_v >= 1_000_000_000:
            return f"{p}{val / 1_000_000_000:.2f}B"
        if abs_v >= 1_000_000:
            return f"{p}{val / 1_000_000:.2f}M"
        if abs_v >= 10_000:
            return f"{p}{val / 1_000:.1f}K"
        return f"{p}{val:,.2f}" if abs_v < 100 else f"{p}{val:,.0f}"

    # 1. Primary Volume / Revenue / Transaction Sum
    primary_num_col = None
    money_cols = sorted(ov.get("money_cols", []), key=_money_rank)
    if money_cols:
        primary_num_col = money_cols[0]
    elif ov.get("numeric_cols"):
        candidate_cols = [c for c in ov["numeric_cols"] if not any(id_w in c.lower() for id_w in ("id", "index", "postal", "zip", "code"))]
        primary_num_col = candidate_cols[0] if candidate_cols else ov["numeric_cols"][0]

    if primary_num_col and primary_num_col in df.columns:
        s = pd.to_numeric(df[primary_num_col], errors="coerce").dropna()
        tot = s.sum()
        avg_val = s.mean()
        is_money = any(k in primary_num_col.lower() for k in ("sales", "revenue", "price", "profit", "amount", "total", "cost", "gmv"))

        kpis.append({
            "id": f"exec_vol_{primary_num_col}",
            "label": f"Total {primary_num_col.replace('_', ' ').title()}",
            "value": _fmt_executive(tot, is_money),
            "sub": f"Aggregated across {len(df):,} rows",
            "icon": "trending-up",
            "badge": "Primary Volume",
        })

        kpis.append({
            "id": f"exec_avg_{primary_num_col}",
            "label": f"Average {primary_num_col.replace('_', ' ').title()}",
            "value": _fmt_executive(avg_val, is_money),
            "sub": f"Range: {_fmt_executive(s.min(), is_money)} to {_fmt_executive(s.max(), is_money)}",
            "icon": "activity",
            "badge": "Unit Mean",
        })

    # 2. Leading Contributor / Segment Driver
    cat_cols = [c["name"] for c in profile.get("columns", []) if c.get("kind") in ("categorical", "text") and 1 < c.get("unique", 0) <= 250]
    if cat_cols and primary_num_col and primary_num_col in df.columns:
        best_cat = cat_cols[0]
        try:
            grouped = df.groupby(best_cat)[primary_num_col].sum().sort_values(ascending=False)
            if not grouped.empty:
                top_name = str(grouped.index[0])
                top_val = grouped.iloc[0]
                total_val = grouped.sum()
                share_pct = (top_val / total_val * 100) if total_val > 0 else 0
                is_money = any(k in primary_num_col.lower() for k in ("sales", "revenue", "price", "profit", "amount"))
                val_str = _fmt_executive(top_val, is_money)
                kpis.append({
                    "id": f"exec_top_{best_cat}",
                    "label": f"Top {best_cat.replace('_', ' ').title()} Leader",
                    "value": f"{top_name[:18]}",
                    "sub": f"{val_str} ({share_pct:.1f}% share)",
                    "icon": "award",
                    "badge": "Growth Driver",
                })
        except Exception:
            pass

    # 3. Active Entity Scope / Unique Dimensions
    if cat_cols:
        scope_cat = cat_cols[1] if len(cat_cols) > 1 else cat_cols[0]
        uniq_count = int(df[scope_cat].nunique())
        kpis.append({
            "id": f"exec_scope_{scope_cat}",
            "label": f"Active {scope_cat.replace('_', ' ').title()} Scope",
            "value": f"{uniq_count:,}",
            "sub": "Distinct segments represented",
            "icon": "layers",
            "badge": "Entity Reach",
        })
    else:
        kpis.append({
            "id": "exec_rows",
            "label": "Total Dataset Records",
            "value": f"{len(df):,}",
            "sub": f"{len(df.columns)} active variables",
            "icon": "database",
            "badge": "Scale",
        })

    # 4. Data Quality & Health Score
    if audit and "overview" in audit:
        q_ov = audit["overview"]
        score = q_ov.get("quality_score", 100)
        grade = q_ov.get("grade", "A")
        crit = q_ov.get("critical_issues", 0)
        label = f"Grade {grade} ({score}%)"
        sub = "Zero critical defects" if crit == 0 else f"{crit} critical issue{'s' if crit > 1 else ''}"
        kpis.append({
            "id": "exec_quality",
            "label": "Data Quality Health",
            "value": label,
            "sub": sub,
            "icon": "shield-check",
            "badge": "Reliability",
        })
    else:
        tot_cells = df.shape[0] * df.shape[1]
        null_cells = int(df.isna().sum().sum())
        clean_pct = round((1 - (null_cells / max(1, tot_cells))) * 100, 1)
        kpis.append({
            "id": "exec_health",
            "label": "Data Health Score",
            "value": f"{clean_pct}%",
            "sub": f"{null_cells:,} null cells detected",
            "icon": "shield-check",
            "badge": "Reliability",
        })

    return kpis[:5]


# --------------------------------------------------------------------------- #
# plain-English findings
# --------------------------------------------------------------------------- #
def build_sentences(df: pd.DataFrame, profile: dict[str, Any]) -> list[dict[str, str]]:
    ov = profile["overview"]
    columns = profile["columns"]
    out: list[dict[str, str]] = []

    def add(kind: str, text: str) -> None:
        out.append({"kind": kind, "text": text})

    add("shape", f"**{ov['rows']:,} rows** and **{ov['cols']} columns**"
                 + (f" - {len(ov['numeric_cols'])} numeric, {len(ov['date_cols'])} date, "
                    f"{len(ov['category_cols']) + len(ov['text_cols'])} text"
                    if ov["numeric_cols"] or ov["date_cols"] else ""))

    if ov["datetime_span"]:
        span = ov["datetime_span"]
        add("time", f"Data covers **{span['min']} -> {span['max']}** ({span['days']} days) "
                    f"based on `{span['column']}`.")

    # money totals + biggest contributor
    money_cols = sorted(ov["money_cols"], key=_money_rank)
    lead_done = False
    for money in money_cols[:3]:
        series = pd.to_numeric(df[money], errors="coerce")
        lower = money.lower()
        if any(k in lower for k in ("price", "rate", "cost")):
            add("money", f"Average **{money}** = **{_fmt_number(_clean_number(series.mean()))}** "
                         f"(range {_fmt_number(_clean_number(series.min()))} - "
                         f"{_fmt_number(_clean_number(series.max()))}).")
            continue
        total = _clean_number(series.sum())
        add("money", f"Total **{money}** = **{_fmt_number(total)}**.")
        if "net" in lower or "revenue" in lower:
            add("money", f"Average **{money} per row** = **{_fmt_number(_clean_number(series.mean()))}** "
                         f"- comparable to average order value.")
        if not lead_done:
            group_col = _first_group_column(df, profile, exclude=[money])
            if group_col:
                try:
                    grouped = (df.assign(_m=pd.to_numeric(df[money], errors="coerce"))
                                 .groupby(group_col)["_m"].sum().sort_values(ascending=False))
                    if len(grouped) >= 2 and grouped.sum() > 0:
                        share = grouped.iloc[0] / grouped.sum() * 100
                        add("pattern",
                            f"**{grouped.index[0]}** leads on `{money}` with "
                            f"**{_fmt_number(_clean_number(grouped.iloc[0]))}** ({share:.1f}% of the total), "
                            f"and **{grouped.index[-1]}** is lowest at {_fmt_number(_clean_number(grouped.iloc[-1]))}.")
                        lead_done = True
                except Exception:
                    pass

    # category distributions
    for col in (ov["category_cols"] + ov["id_cols"])[:3]:
        series = df[col].dropna().astype(str)
        if series.empty or series.nunique() > 50:
            continue
        counts = series.str.strip().value_counts()
        if len(counts) >= 2:
            share = counts.iloc[0] / counts.sum() * 100
            add("category",
                f"`{col}` has {series.nunique()} distinct values; most common is "
                f"**{counts.index[0]}** ({counts.iloc[0]:,} rows, {share:.1f}%).")

    # strongest correlations
    corr = profile.get("correlation")
    if corr:
        pairs = []
        rows = corr["rows"]
        cols = corr["columns"]
        for i, row in enumerate(rows):
            for j, value in enumerate(row["cells"]):
                if j <= i or value is None:
                    continue
                pairs.append((abs(value), value, cols[i], cols[j]))
        pairs.sort(reverse=True)
        for _, value, a, b in pairs[:2]:
            direction = "positive" if value > 0 else "negative"
            add("correlation",
                f"`{a}` and `{b}` move together ({direction} correlation r = {value:.2f}).")

    # missing data
    null_cols = sorted(columns, key=lambda c: -c["null_count"])
    if null_cols and null_cols[0]["null_count"] > 0:
        top = null_cols[0]
        add("quality", f"`{top['name']}` is missing {top['null_count']:,} values "
                       f"({top['null_pct']}% of rows).")
    if ov["duplicate_rows"]:
        add("quality", f"{ov['duplicate_rows']:,} duplicate rows found - remove them in the Clean step.")

    # outlier-ish note: max vs mean for numeric columns
    for col in ov["numeric_cols"][:6]:
        series = pd.to_numeric(df[col], errors="coerce").dropna()
        if len(series) < 10 or series.mean() == 0:
            continue
        ratio = series.max() / series.mean() if series.mean() else 0
        if ratio >= 4:
            add("outlier", f"`{col}` is skewed: maximum {_fmt_number(_clean_number(series.max()))} "
                           f"is {ratio:.1f}x the average {_fmt_number(_clean_number(series.mean()))}.")
            break

    return out


def _first_group_column(df: pd.DataFrame, profile: dict[str, Any], exclude: list[str] | None = None) -> str | None:
    exclude = set(exclude or [])
    for col in profile["overview"]["category_cols"] + profile["overview"]["id_cols"]:
        if col in exclude:
            continue
        if df[col].nunique() <= 50:
            return col
    return None


# --------------------------------------------------------------------------- #
# chart suggestions
# --------------------------------------------------------------------------- #
def suggest_charts(df: pd.DataFrame, profile: dict[str, Any]) -> list[dict[str, Any]]:
    ov = profile["overview"]
    suggestions: list[dict[str, Any]] = []

    date_col = ov["date_cols"][0] if ov["date_cols"] else None
    money = sorted(ov["money_cols"], key=_money_rank)[0] if ov["money_cols"] else None
    qty = ov["quantity_cols"][0] if ov["quantity_cols"] else None
    categories = [c for c in ov["category_cols"] if df[c].nunique() <= 30]

    def add(**spec: Any) -> None:
        suggestions.append(spec)

    if date_col and money:
        add(title=f"{money} over time (monthly)", reason="See the trend and seasonality",
            chart_type="line", x=date_col, y=money, agg="sum", freq="M")
    if date_col and len(ov["date_cols"]) > 0:
        add(title=f"Rows over time (monthly)", reason="Volume of records per month",
            chart_type="line", x=date_col, y=None, agg="count", freq="M")
    if date_col and categories:
        add(title=f"{money or 'Rows'} by month and {categories[0]}", reason="How categories evolve over time",
            chart_type="stacked_bar", x=date_col, y=money, group=categories[0], agg="sum", freq="M")
    if categories and money:
        add(title=f"{money} by {categories[0]}", reason="Which group contributes most",
            chart_type="bar", x=categories[0], y=money, agg="sum", top_n=10, show_values=True)
    if categories and qty:
        add(title=f"{qty} by {categories[0]}", reason="Volume comparison between groups",
            chart_type="hbar", x=categories[0], y=qty, agg="sum", top_n=10)
    if money:
        add(title=f"Distribution of {money}", reason="Spread, skew and outliers",
            chart_type="hist", x=money, bins=20)
    if categories and money and categories[0]:
        add(title=f"{money} spread by {categories[0]}", reason="Median and spread per group",
            chart_type="box", x=categories[0], y=money)
    if len(categories) >= 1:
        add(title=f"Share of {categories[0]}", reason="Composition of the file",
            chart_type="donut", x=categories[0], y=None, agg="count", top_n=8)
    if len(categories) >= 2:
        add(title=f"{categories[1]} vs {categories[0]} (counts)", reason="Two-way comparison",
            chart_type="heatmap_counts", x=categories[0], y=categories[1])
    if len(ov["numeric_cols"]) >= 2:
        a, b = ov["numeric_cols"][0], ov["numeric_cols"][1]
        add(title=f"{a} vs {b}", reason="Relationship between two numbers",
            chart_type="scatter", x=a, y=b)
    if len(ov["numeric_cols"]) >= 2:
        add(title="Correlation heatmap", reason="Quick view of what moves together",
            chart_type="heatmap", x=None, y=None)

    if not suggestions and ov["numeric_cols"]:
        col = ov["numeric_cols"][0]
        add(title=f"Distribution of {col}", reason="Start here", chart_type="hist", x=col, bins=20)

    for i, item in enumerate(suggestions):
        item["id"] = f"suggestion_{i}"
    return suggestions[:12]


# --------------------------------------------------------------------------- #
# tables
# --------------------------------------------------------------------------- #
def group_table(
    df: pd.DataFrame,
    profile: dict[str, Any],
    rows: list[str],
    metrics: list[dict[str, Any]],
    sort_by: str | None = None,
    sort_desc: bool = True,
    limit: int = 500,
) -> dict[str, Any]:
    """Build a group-by table: rows = group columns, metrics = aggregations."""
    rows = [c for c in rows if c in df.columns]
    if not rows:
        raise ValueError("Pick at least one column to group by")

    work = df.copy()
    valid_metrics: list[dict[str, Any]] = []
    for metric in metrics:
        col = metric.get("column")
        agg = metric.get("agg", "sum")
        if agg == "count":
            valid_metrics.append({"column": None, "agg": "count", "label": "Rows"})
            continue
        if agg == "nunique":
            if col in work.columns:
                valid_metrics.append({"column": col, "agg": "nunique",
                                      "label": f"Unique {col}"})
            continue
        if col not in work.columns:
            continue
        if agg in ("sum", "mean", "median", "min", "max", "std"):
            numeric = pd.to_numeric(work[col], errors="coerce")
            if numeric.notna().sum() == 0:
                continue
            work[col] = numeric
            label = f"{agg.title()} {col}"
            valid_metrics.append({"column": col, "agg": agg, "label": label})

    if not valid_metrics:
        valid_metrics = [{"column": None, "agg": "count", "label": "Rows"}]

    grouped = work.groupby(rows, dropna=False, observed=True)
    pieces = []
    for metric in valid_metrics:
        if metric["agg"] == "count":
            pieces.append(grouped.size().rename("Rows"))
        elif metric["agg"] == "nunique":
            pieces.append(grouped[metric["column"]].nunique().rename(f"Unique {metric['column']}"))
        else:
            pieces.append(
                getattr(grouped[metric["column"]], metric["agg"])().rename(metric["label"])
            )

    table = pd.concat(pieces, axis=1).reset_index()

    # share-of-total column for the first sum metric (nice for money columns)
    sum_metrics = [m for m in valid_metrics if m["agg"] == "sum"]
    if sum_metrics:
        first = sum_metrics[0]["label"]
        if first in table.columns and table[first].sum() > 0:
            table[f"{first} %"] = (table[first] / table[first].sum() * 100).round(2)

    if sort_by and sort_by in table.columns:
        table = table.sort_values(sort_by, ascending=not sort_desc)
    elif valid_metrics:
        first_label = valid_metrics[0]["label"]
        if first_label in table.columns:
            table = table.sort_values(first_label, ascending=False)

    display = table.head(limit).copy()
    for col in display.columns:
        if pd.api.types.is_float_dtype(display[col]):
            display[col] = display[col].round(2)

    return {
        "columns": [str(c) for c in display.columns],
        "rows": [
            ["" if (isinstance(v, float) and np.isnan(v)) else (f"{v:,}" if isinstance(v, (int, np.integer)) else
             (f"{v:,.2f}" if isinstance(v, (float, np.floating)) else str(v)))
             for v in record]
            for record in display.itertuples(index=False)
        ],
        "table": display,
        "total_rows": int(len(table)),
    }


def build_auto_tables(df: pd.DataFrame, profile: dict[str, Any], max_tables: int = 6) -> list[dict[str, Any]]:
    """Ready-made analysis tables (like the notebook's category/city/monthly group-bys)."""
    ov = profile["overview"]
    money = sorted(ov["money_cols"], key=_money_rank)[:2]
    qty = ov["quantity_cols"][:1]
    rating = ov["rating_cols"][:1]
    tables: list[dict[str, Any]] = []

    def metrics_for() -> list[dict[str, Any]]:
        metrics: list[dict[str, Any]] = [{"column": None, "agg": "count"}]
        for col in money:
            metrics.append({"column": col, "agg": "sum"})
            metrics.append({"column": col, "agg": "mean"})
        for col in qty:
            metrics.append({"column": col, "agg": "sum"})
        for col in rating:
            metrics.append({"column": col, "agg": "mean"})
        if not money and not qty and not rating:
            for col in ov["numeric_cols"][:2]:
                metrics.append({"column": col, "agg": "mean"})
        return metrics

    categories = [c for c in ov["category_cols"] if 1 < df[c].nunique() <= 50]
    for col in categories[:max_tables]:
        try:
            table = group_table(df, profile, [col], metrics_for())
            tables.append({
                "id": f"auto_{col}",
                "title": f"Breakdown by {col}",
                "group_cols": [col],
                "metrics": metrics_for(),
                **{k: v for k, v in table.items() if k != "table"},
            })
        except Exception:
            continue

    # time table
    if ov["date_cols"]:
        date_col = ov["date_cols"][0]
        try:
            work = df.copy()
            work[date_col] = pd.to_datetime(work[date_col], errors="coerce", format="mixed")
            work = work.dropna(subset=[date_col])
            if not work.empty:
                work["Month"] = work[date_col].dt.to_period("M").astype(str)
                metrics = [m for m in metrics_for() if m["agg"] != "mean" or m["column"] in money]
                table = group_table(work, profile, ["Month"], metrics)
                tables.append({
                    "id": "auto_month",
                    "title": "Monthly summary",
                    "group_cols": ["Month"],
                    "metrics": metrics,
                    **{k: v for k, v in table.items() if k != "table"},
                })
        except Exception:
            pass

    return tables
