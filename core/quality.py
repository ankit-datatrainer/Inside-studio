"""Data Quality Checker for Insight Studio.

Performs deep automated data quality audits across 4 critical pillars:
1. Missing Values: nulls, blanks, placeholder tokens ("N/A", "-", "?", etc.), empty rows/cols.
2. Duplicates: full-row duplicate records and primary key/identifier collisions.
3. Incorrect Data Types: numbers stored as text (currencies, percentages), dates stored as strings,
   boolean-like text, mixed data types.
4. Unusual Values: statistical outliers (IQR / z-score), negative values in non-negative metrics,
   extreme/invalid dates, suspicious zeroes, and rare categorical anomalies.

Also provides multi-format export engines:
* generate_pdf_report()  -> Executive PDF quality report with scorecard & issue tables
* generate_csv_report()  -> CSV issue matrix
* generate_json_report() -> Machine-readable quality audit JSON
* generate_html_report() -> Standalone printable HTML report
"""
from __future__ import annotations

import io
import json
import math
import re
from datetime import datetime
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.patches import FancyBboxPatch
import numpy as np
import pandas as pd

from . import profiler

# Tokens often used in CSV/Excel to represent missing data
MISSING_TOKENS = {
    "", "na", "n/a", "nan", "null", "none", "nil", "-", "--", "?", "unknown",
    "missing", "#n/a", "#null!", "<na>", "undefined", "n.a.", "not available",
}

_ID_NAME_RE = re.compile(r"(^|[_\s])(id|ids|key|code|ref|no|num|number|uuid|guid)($|[_\s])", re.I)
_NON_NEGATIVE_METRIC_RE = re.compile(
    r"(revenue|sales|sale|amount|amt|price|value|cost|margin|income|spend|fee|charge|total|salary|budget|qty|quantity|units|count|volume|rating|score|age|duration)",
    re.I,
)
_DATE_NAME_RE = re.compile(r"(date|time|day|month|year|created|updated|timestamp)", re.I)

# Styling Constants matching iCapital Aesthetic
PALETTE = {
    "navy": "#0B1528",
    "blue": "#0066FF",
    "cyan": "#23C0FF",
    "light": "#F8FAFD",
    "line": "#E2E8F0",
    "text": "#0A1324",
    "muted": "#64748B",
    "success": "#10B981",
    "warning": "#F59E0B",
    "danger": "#EF4444",
}


def _safe_float(val: Any) -> float:
    try:
        f = float(val)
        return 0.0 if math.isnan(f) or math.isinf(f) else f
    except Exception:
        return 0.0


def audit_dataset_quality(df: pd.DataFrame, source_name: str = "") -> dict[str, Any]:
    """Execute complete algorithmic quality check on a DataFrame."""
    if df is None or df.empty:
        return {
            "overview": {
                "total_rows": 0, "total_cols": 0, "total_cells": 0,
                "quality_score": 0.0, "grade": "F", "grade_label": "Empty Dataset",
                "completeness_score": 0.0, "uniqueness_score": 0.0,
                "type_consistency_score": 0.0, "validity_score": 0.0,
                "total_issues": 1, "critical_issues": 1, "warning_issues": 0, "info_issues": 0,
            },
            "missing_analysis": {"total_missing_cells": 0, "missing_cells_pct": 0.0, "column_missing": []},
            "duplicate_analysis": {"exact_duplicate_rows": 0, "exact_duplicate_pct": 0.0, "id_column_issues": []},
            "type_analysis": {"type_mismatches_count": 0, "columns_with_mismatch": []},
            "unusual_analysis": {"outliers_count": 0, "columns_with_outliers": [], "domain_anomalies": []},
            "column_matrix": [],
            "issues_list": [{
                "id": "empty_dataset",
                "category": "missing",
                "severity": "critical",
                "title": "Empty Dataset",
                "column": "All Columns",
                "detail": "The dataset contains no rows or columns.",
                "sample_values": [],
                "remediation": "Please upload a file with tabular rows and headers.",
            }],
            "recommendations": ["Upload a non-empty tabular dataset."],
        }

    total_rows = int(df.shape[0])
    total_cols = int(df.shape[1])
    total_cells = total_rows * total_cols

    issues: list[dict[str, Any]] = []

    # ----------------------------------------------------------------------- #
    # 1. Missing Values Analysis
    # ----------------------------------------------------------------------- #
    column_missing_info: list[dict[str, Any]] = []
    total_missing_cells = 0
    empty_rows_count = int(df.isna().all(axis=1).sum())
    empty_cols_count = int(df.isna().all(axis=0).sum())

    for col in df.columns:
        s = df[col]
        # Standard pandas NaN / None
        null_count = int(s.isna().sum())

        # Check for placeholder string nulls ("N/A", "-", etc.)
        token_null_count = 0
        if pd.api.types.is_object_dtype(s) or pd.api.types.is_string_dtype(s):
            non_na_str = s.dropna().astype(str).str.strip().str.lower()
            token_null_count = int(non_na_str.isin(MISSING_TOKENS).sum())

        total_col_missing = null_count + token_null_count
        total_missing_cells += total_col_missing
        pct = round((total_col_missing / total_rows) * 100, 2) if total_rows > 0 else 0.0

        severity = "info"
        if pct > 40:
            severity = "critical"
        elif pct > 5 or token_null_count > 0:
            severity = "warning"

        column_missing_info.append({
            "column": str(col),
            "null_count": null_count,
            "token_nulls": token_null_count,
            "total_missing": total_col_missing,
            "pct": pct,
            "severity": severity,
        })

        if total_col_missing > 0:
            msg = f"{total_col_missing} missing value(s) ({pct}% of column)"
            if token_null_count > 0:
                msg += f" including {token_null_count} placeholder string token(s) (e.g. 'N/A' or '-')"
            issues.append({
                "id": f"missing_{col}",
                "category": "missing",
                "severity": severity,
                "title": f"Missing Values in '{col}'",
                "column": str(col),
                "detail": msg,
                "sample_values": ["<blank / null>"],
                "remediation": f"Impute missing values using mean/median or drop null rows on the Clean page.",
            })

    if empty_rows_count > 0:
        issues.append({
            "id": "empty_rows",
            "category": "missing",
            "severity": "critical",
            "title": f"{empty_rows_count} Completely Blank Row(s)",
            "column": "Dataset",
            "detail": f"Found {empty_rows_count} row(s) where every single cell is empty.",
            "sample_values": [],
            "remediation": "Remove empty rows automatically in the Clean step.",
        })

    # Sort columns by missing percentage descending
    column_missing_info.sort(key=lambda x: x["pct"], reverse=True)
    rows_with_missing = int(df.isna().any(axis=1).sum())
    missing_cells_pct = round((total_missing_cells / max(total_cells, 1)) * 100, 2)

    missing_analysis = {
        "total_missing_cells": total_missing_cells,
        "missing_cells_pct": missing_cells_pct,
        "rows_with_missing": rows_with_missing,
        "rows_with_missing_pct": round((rows_with_missing / max(total_rows, 1)) * 100, 2),
        "cols_with_missing": len([c for c in column_missing_info if c["total_missing"] > 0]),
        "empty_rows": empty_rows_count,
        "empty_cols": empty_cols_count,
        "column_missing": column_missing_info,
    }

    # ----------------------------------------------------------------------- #
    # 2. Duplicates Analysis
    # ----------------------------------------------------------------------- #
    exact_duplicates_mask = df.duplicated()
    exact_duplicate_rows = int(exact_duplicates_mask.sum())
    exact_duplicate_pct = round((exact_duplicate_rows / max(total_rows, 1)) * 100, 2)

    duplicate_indices = list(df[exact_duplicates_mask].index[:25])
    id_column_issues: list[dict[str, Any]] = []

    if exact_duplicate_rows > 0:
        issues.append({
            "id": "exact_duplicates",
            "category": "duplicate",
            "severity": "warning" if exact_duplicate_pct < 10 else "critical",
            "title": f"{exact_duplicate_rows} Exact Duplicate Row(s)",
            "column": "Entire Record",
            "detail": f"Detected {exact_duplicate_rows} row(s) ({exact_duplicate_pct}% of dataset) that are 100% identical duplicates.",
            "sample_values": [f"Row index {i}" for i in duplicate_indices[:5]],
            "remediation": "Deduplicate data in one click on the Clean page to preserve record integrity.",
        })

    # Check for Key / Identifier Column collisions
    for col in df.columns:
        s = df[col].dropna()
        col_str = str(col).lower()
        is_metric = bool(_NON_NEGATIVE_METRIC_RE.search(col_str)) or pd.api.types.is_numeric_dtype(s)
        is_id_candidate = bool(_ID_NAME_RE.search(col_str)) or (not is_metric and s.nunique() / max(len(s), 1) > 0.85 and s.nunique() < len(s))
        if is_id_candidate and not exact_duplicates_mask.all():
            dup_keys = int(s.duplicated().sum())
            if 0 < dup_keys < len(s):
                top_repeats = s.value_counts()
                collided = top_repeats[top_repeats > 1].head(5).to_dict()
                id_column_issues.append({
                    "column": str(col),
                    "duplicate_keys": dup_keys,
                    "total_keys": len(s),
                    "pct": round((dup_keys / len(s)) * 100, 2),
                    "example_collisions": [f"'{k}' ({v} times)" for k, v in collided.items()],
                })
                issues.append({
                    "id": f"id_dup_{col}",
                    "category": "duplicate",
                    "severity": "warning",
                    "title": f"Duplicate Keys in Identifier Column '{col}'",
                    "column": str(col),
                    "detail": f"Found {dup_keys} duplicate identifier collision(s) in '{col}'. Identifiers should typically be unique.",
                    "sample_values": [f"'{k}' ({v}x)" for k, v in list(collided.items())[:3]],
                    "remediation": "Review duplicate keys to ensure foreign key integrity or remove redundant entries.",
                })

    duplicate_analysis = {
        "exact_duplicate_rows": exact_duplicate_rows,
        "exact_duplicate_pct": exact_duplicate_pct,
        "duplicate_indices": [int(i) for i in duplicate_indices],
        "id_column_issues": id_column_issues,
    }

    # ----------------------------------------------------------------------- #
    # 3. Incorrect Data Types Analysis
    # ----------------------------------------------------------------------- #
    columns_with_mismatch: list[dict[str, Any]] = []

    for col in df.columns:
        s = df[col]
        non_null = s.dropna()
        if len(non_null) == 0:
            continue

        raw_dtype_str = str(s.dtype)

        # A: Text column that should be numeric (e.g. contains "$1,200", "45%", commas)
        if pd.api.types.is_object_dtype(s) or pd.api.types.is_string_dtype(s):
            str_vals = non_null.astype(str).str.strip()
            # Clean common currency / unit chars
            cleaned_num = (
                str_vals
                .str.replace(r"[^\d.,\-+eE]", "", regex=True)
                .str.replace(",", "", regex=False)
            )
            parsed_num = pd.to_numeric(cleaned_num, errors="coerce")
            num_success_rate = float(parsed_num.notna().mean())

            # Check if looks like date
            parsed_date = pd.to_datetime(str_vals, errors="coerce", format="mixed")
            date_success_rate = float(parsed_date.notna().mean())

            # Check if looks like boolean
            bool_tokens = {"true", "false", "yes", "no", "y", "n", "0", "1"}
            bool_match_rate = float(str_vals.str.lower().isin(bool_tokens).mean())

            # Detect mixed types (some numbers, some pure text words)
            has_mixed = False
            if 0.15 <= num_success_rate <= 0.85:
                has_mixed = True

            sample_vals = [str(x) for x in non_null.head(4)]
            is_id_name = bool(_ID_NAME_RE.search(str(col).lower()))

            if num_success_rate >= 0.70 and not bool_match_rate > 0.95 and not is_id_name:
                columns_with_mismatch.append({
                    "column": str(col),
                    "current_dtype": raw_dtype_str,
                    "detected_true_type": "Number (Numeric)",
                    "reason": f"Stored as text/object, but {round(num_success_rate * 100, 1)}% of values parse as numeric figures (e.g. currency, commas, decimals).",
                    "parseable_rate": round(num_success_rate * 100, 1),
                    "sample_raw_values": sample_vals,
                    "mixed_types_detected": has_mixed,
                })
                issues.append({
                    "id": f"type_num_{col}",
                    "category": "type",
                    "severity": "warning",
                    "title": f"Numbers Stored as Text in '{col}'",
                    "column": str(col),
                    "detail": f"Column '{col}' is typed as object/text, but contains numeric values like '{sample_vals[0] if sample_vals else ''}'.",
                    "sample_values": sample_vals[:3],
                    "remediation": "Convert to numeric on the Clean page to unlock statistical charts and aggregation.",
                })
            elif date_success_rate >= 0.75 and not num_success_rate >= 0.70:
                columns_with_mismatch.append({
                    "column": str(col),
                    "current_dtype": raw_dtype_str,
                    "detected_true_type": "Date / Time (Datetime)",
                    "reason": f"Stored as text/object, but {round(date_success_rate * 100, 1)}% of values parse as dates.",
                    "parseable_rate": round(date_success_rate * 100, 1),
                    "sample_raw_values": sample_vals,
                    "mixed_types_detected": has_mixed,
                })
                issues.append({
                    "id": f"type_date_{col}",
                    "category": "type",
                    "severity": "warning",
                    "title": f"Dates Stored as Text in '{col}'",
                    "column": str(col),
                    "detail": f"Column '{col}' contains date strings. Converting to datetime enables time-series analytics.",
                    "sample_values": sample_vals[:3],
                    "remediation": "Parse text into datetime in the Clean step.",
                })
            elif has_mixed:
                issues.append({
                    "id": f"mixed_type_{col}",
                    "category": "type",
                    "severity": "warning",
                    "title": f"Mixed Data Types in '{col}'",
                    "column": str(col),
                    "detail": f"Column '{col}' contains a blend of numbers and text strings ({round(num_success_rate*100, 1)}% numeric).",
                    "sample_values": sample_vals[:3],
                    "remediation": "Standardize formatting or split into dedicated columns.",
                })

    type_analysis = {
        "type_mismatches_count": len(columns_with_mismatch),
        "columns_with_mismatch": columns_with_mismatch,
    }

    # ----------------------------------------------------------------------- #
    # 4. Unusual Values & Outlier Analysis
    # ----------------------------------------------------------------------- #
    columns_with_outliers: list[dict[str, Any]] = []
    domain_anomalies: list[dict[str, Any]] = []
    total_outliers_count = 0

    numeric_cols = df.select_dtypes(include=[np.number]).columns

    for col in numeric_cols:
        s = df[col].dropna()
        if len(s) < 8:
            continue

        q1 = float(s.quantile(0.25))
        q3 = float(s.quantile(0.75))
        iqr = q3 - q1

        outliers_s = pd.Series([], dtype=float)
        lower_bound = q1 - 1.5 * iqr
        upper_bound = q3 + 1.5 * iqr

        if iqr > 0:
            outliers_s = s[(s < lower_bound) | (s > upper_bound)]

        count = int(len(outliers_s))
        if count > 0:
            total_outliers_count += count
            pct = round((count / len(s)) * 100, 2)
            ex_outliers = [round(float(x), 2) for x in outliers_s.head(5)]

            columns_with_outliers.append({
                "column": str(col),
                "method": "IQR (Interquartile Range)",
                "lower_bound": round(lower_bound, 2),
                "upper_bound": round(upper_bound, 2),
                "outliers_count": count,
                "outliers_pct": pct,
                "min_value": round(float(s.min()), 2),
                "max_value": round(float(s.max()), 2),
                "example_outliers": ex_outliers,
            })
            issues.append({
                "id": f"outlier_{col}",
                "category": "unusual",
                "severity": "warning" if pct < 5 else "critical",
                "title": f"Statistical Outliers in '{col}'",
                "column": str(col),
                "detail": f"Detected {count} outlier value(s) ({pct}%) outside expected range [{round(lower_bound, 2)} to {round(upper_bound, 2)}].",
                "sample_values": [str(x) for x in ex_outliers[:4]],
                "remediation": "Consider winsorizing/capping extreme outliers on the Clean page if they distort charts.",
            })

        # Check Domain Rule 1: Negative values where non-negative expected
        col_str = str(col).lower()
        if _NON_NEGATIVE_METRIC_RE.search(col_str) and not col_str.startswith(("change", "diff", "delta", "growth", "margin_pct")):
            neg_mask = s < 0
            neg_count = int(neg_mask.sum())
            if neg_count > 0:
                neg_examples = [round(float(x), 2) for x in s[neg_mask].head(4)]
                domain_anomalies.append({
                    "column": str(col),
                    "type": "negative_value",
                    "description": f"Found {neg_count} negative value(s) (min: {round(float(s.min()), 2)}) in a metric expected to be non-negative.",
                    "count": neg_count,
                    "sample_values": neg_examples,
                })
                issues.append({
                    "id": f"neg_{col}",
                    "category": "unusual",
                    "severity": "critical",
                    "title": f"Negative Values in '{col}'",
                    "column": str(col),
                    "detail": f"Column '{col}' represents a physical or financial metric but contains {neg_count} negative value(s).",
                    "sample_values": [str(x) for x in neg_examples],
                    "remediation": "Filter out negative entries or verify transaction return records.",
                })

        # Check Domain Rule 2: Out of range ratings (e.g. rating > 5 or > 10)
        if re.search(r"(rating|score|stars?)", col_str):
            if s.max() > 5 and s.max() <= 10:
                pass  # 10-point scale
            elif s.max() > 10:
                domain_anomalies.append({
                    "column": str(col),
                    "type": "out_of_range",
                    "description": f"Maximum rating value is {round(float(s.max()), 2)}, which exceeds standard rating scales.",
                    "count": int((s > 10).sum()),
                    "sample_values": [round(float(x), 2) for x in s[s > 10].head(3)],
                })

    # Check Date columns for unusual dates (e.g. year < 1970 or year > 2035)
    for col in df.select_dtypes(include=["datetime", "datetime64"]).columns:
        s_date = df[col].dropna()
        if len(s_date) > 0:
            ancient_dates = s_date[s_date.dt.year < 1970]
            future_dates = s_date[s_date.dt.year > 2035]
            if len(ancient_dates) > 0:
                domain_anomalies.append({
                    "column": str(col),
                    "type": "extreme_date",
                    "description": f"Found {len(ancient_dates)} date(s) before 1970 (earliest: {ancient_dates.min().strftime('%Y-%m-%d')}).",
                    "count": len(ancient_dates),
                    "sample_values": [d.strftime("%Y-%m-%d") for d in ancient_dates.head(3)],
                })
            if len(future_dates) > 0:
                domain_anomalies.append({
                    "column": str(col),
                    "type": "extreme_date",
                    "description": f"Found {len(future_dates)} date(s) far into the future (latest: {future_dates.max().strftime('%Y-%m-%d')}).",
                    "count": len(future_dates),
                    "sample_values": [d.strftime("%Y-%m-%d") for d in future_dates.head(3)],
                })

    unusual_analysis = {
        "outliers_count": total_outliers_count,
        "columns_with_outliers": columns_with_outliers,
        "domain_anomalies": domain_anomalies,
    }

    # ----------------------------------------------------------------------- #
    # 5. Column Quality Matrix
    # ----------------------------------------------------------------------- #
    column_matrix: list[dict[str, Any]] = []

    for col in df.columns:
        s = df[col]
        raw_dtype = str(s.dtype)
        detected_kind = profiler.detect_kind(s, str(col))
        null_count = int(s.isna().sum())
        null_pct = round((null_count / max(total_rows, 1)) * 100, 1)
        unique_cnt = int(s.nunique(dropna=True))

        col_issues = [i for i in issues if i.get("column") == str(col)]
        has_critical = any(i["severity"] == "critical" for i in col_issues)
        has_warning = any(i["severity"] == "warning" for i in col_issues)

        status = "clean"
        health_score = 100
        if has_critical:
            status = "critical"
            health_score = max(30, 100 - null_pct * 1.5 - len(col_issues) * 20)
        elif has_warning:
            status = "warning"
            health_score = max(60, 100 - null_pct - len(col_issues) * 10)

        recommended_action = "Data looks clean and ready for analysis."
        if col_issues:
            recommended_action = col_issues[0]["remediation"]

        column_matrix.append({
            "name": str(col),
            "raw_dtype": raw_dtype,
            "detected_type": profiler.KIND_LABELS.get(detected_kind, detected_kind.title()),
            "kind_key": detected_kind,
            "health_score": int(round(health_score)),
            "status": status,
            "missing_count": null_count,
            "missing_pct": null_pct,
            "unique_count": unique_cnt,
            "issues_count": len(col_issues),
            "issues_summary": [i["title"] for i in col_issues],
            "recommended_action": recommended_action,
        })

    # ----------------------------------------------------------------------- #
    # 6. Overall Quality Score Computation
    # ----------------------------------------------------------------------- #
    completeness_score = max(0.0, min(100.0, 100.0 - missing_cells_pct))
    uniqueness_score = max(0.0, min(100.0, 100.0 - exact_duplicate_pct))
    type_consistency_score = max(0.0, min(100.0, 100.0 - (len(columns_with_mismatch) / max(total_cols, 1) * 100)))

    # Validity considers outliers and domain anomalies
    anomaly_cells = total_outliers_count + sum(a.get("count", 0) for a in domain_anomalies)
    validity_penalty = (anomaly_cells / max(total_cells, 1)) * 100
    validity_score = max(0.0, min(100.0, 100.0 - min(40.0, validity_penalty * 3.0)))

    # Weighted Overall Score
    weighted_score = (
        0.35 * completeness_score +
        0.25 * uniqueness_score +
        0.20 * type_consistency_score +
        0.20 * validity_score
    )
    final_score = round(max(0.0, min(100.0, weighted_score)), 1)

    if final_score >= 90:
        grade, grade_label = "A", "Excellent Quality"
    elif final_score >= 75:
        grade, grade_label = "B", "Good Quality"
    elif final_score >= 60:
        grade, grade_label = "C", "Needs Attention"
    else:
        grade, grade_label = "D", "Poor Quality"

    critical_count = len([i for i in issues if i["severity"] == "critical"])
    warning_count = len([i for i in issues if i["severity"] == "warning"])
    info_count = len([i for i in issues if i["severity"] == "info"])

    # High-level actionable recommendations
    recommendations: list[str] = []
    if exact_duplicate_rows > 0:
        recommendations.append(f"Remove {exact_duplicate_rows} exact duplicate rows to prevent double-counting metrics.")
    if total_missing_cells > 0:
        recommendations.append(f"Impute or handle {total_missing_cells} missing cell(s) across {missing_analysis['cols_with_missing']} columns.")
    if columns_with_mismatch:
        recommendations.append(f"Convert {len(columns_with_mismatch)} text column(s) with numbers/dates into native types for charting.")
    if total_outliers_count > 0:
        recommendations.append(f"Inspect or winsorize {total_outliers_count} statistical outlier(s) that might distort averages.")
    if not recommendations:
        recommendations.append("Your dataset meets top-tier data quality standards. Proceed directly to charting and dashboard creation.")

    return {
        "source_name": source_name or "Dataset",
        "audited_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "overview": {
            "total_rows": total_rows,
            "total_cols": total_cols,
            "total_cells": total_cells,
            "quality_score": final_score,
            "grade": grade,
            "grade_label": grade_label,
            "completeness_score": round(completeness_score, 1),
            "uniqueness_score": round(uniqueness_score, 1),
            "type_consistency_score": round(type_consistency_score, 1),
            "validity_score": round(validity_score, 1),
            "total_issues": len(issues),
            "critical_issues": critical_count,
            "warning_issues": warning_count,
            "info_issues": info_count,
        },
        "missing_analysis": missing_analysis,
        "duplicate_analysis": duplicate_analysis,
        "type_analysis": type_analysis,
        "unusual_analysis": unusual_analysis,
        "column_matrix": column_matrix,
        "issues_list": issues,
        "recommendations": recommendations,
    }


# --------------------------------------------------------------------------- #
# Multi-format Report Generation Engines
# --------------------------------------------------------------------------- #
def generate_pdf_report(audit: dict[str, Any], df: pd.DataFrame, source_name: str = "") -> bytes:
    """Generate a high-fidelity Executive Data Quality Report PDF."""
    buf = io.BytesIO()
    ov = audit.get("overview", {})

    with PdfPages(buf) as pdf:
        # ================= PAGE 1: Executive Scorecard =================
        fig = plt.figure(figsize=(11.69, 8.27), dpi=100)
        fig.patch.set_facecolor("#FFFFFF")

        # Top Bar
        fig.text(0.06, 0.94, "DATA QUALITY AUDIT REPORT", fontsize=15, fontweight="bold", color=PALETTE["navy"])
        sub = f"Source: {source_name or audit.get('source_name', 'Dataset')}  |  Generated: {audit.get('audited_at', '')}  |  Insight Studio Engine"
        fig.text(0.06, 0.905, sub, fontsize=9, color=PALETTE["muted"])
        line = plt.Line2D([0.06, 0.94], [0.885, 0.885], transform=fig.transFigure, color=PALETTE["line"], linewidth=1.2)
        fig.add_artist(line)

        # Quality Score Card (Top Left)
        score_ax = fig.add_axes([0.06, 0.65, 0.28, 0.20])
        score_ax.axis("off")
        score_ax.add_patch(FancyBboxPatch((0, 0), 1, 1, boxstyle="round,pad=0.03,rounding_size=0.05",
                                          linewidth=1.2, edgecolor=PALETTE["line"], facecolor="#F8FAFD",
                                          transform=score_ax.transAxes))
        score_ax.text(0.10, 0.78, "DATA QUALITY SCORE", fontsize=9, fontweight="bold", color=PALETTE["muted"], transform=score_ax.transAxes)
        score_color = PALETTE["success"] if ov.get("quality_score", 0) >= 80 else (PALETTE["warning"] if ov.get("quality_score", 0) >= 65 else PALETTE["danger"])
        score_ax.text(0.10, 0.42, f"{ov.get('quality_score', 0)}%", fontsize=28, fontweight="bold", color=score_color, transform=score_ax.transAxes)
        score_ax.text(0.10, 0.18, f"Grade {ov.get('grade', 'B')}  •  {ov.get('grade_label', '')}", fontsize=9.5, fontweight="bold", color=PALETTE["navy"], transform=score_ax.transAxes)

        # 4 Pillar KPI Cards (Top Right Grid)
        pillars = [
            ("Completeness", f"{ov.get('completeness_score', 100)}%", f"{audit['missing_analysis']['total_missing_cells']} missing cells"),
            ("Uniqueness", f"{ov.get('uniqueness_score', 100)}%", f"{audit['duplicate_analysis']['exact_duplicate_rows']} duplicate rows"),
            ("Type Consistency", f"{ov.get('type_consistency_score', 100)}%", f"{audit['type_analysis']['type_mismatches_count']} mismatched cols"),
            ("Validity & Outliers", f"{ov.get('validity_score', 100)}%", f"{audit['unusual_analysis']['outliers_count']} detected outliers"),
        ]
        p_w, p_h = 0.27, 0.09
        for i, (p_title, p_val, p_sub) in enumerate(pillars):
            col_idx = i % 2
            row_idx = i // 2
            px = 0.37 + col_idx * (p_w + 0.03)
            py = 0.76 - row_idx * (p_h + 0.02)
            p_ax = fig.add_axes([px, py, p_w, p_h])
            p_ax.axis("off")
            p_ax.add_patch(FancyBboxPatch((0, 0), 1, 1, boxstyle="round,pad=0.02,rounding_size=0.04",
                                          linewidth=1, edgecolor=PALETTE["line"], facecolor="#FFFFFF", transform=p_ax.transAxes))
            p_ax.text(0.08, 0.72, p_title, fontsize=8.5, color=PALETTE["muted"], transform=p_ax.transAxes)
            p_ax.text(0.08, 0.38, p_val, fontsize=14, fontweight="bold", color=PALETTE["navy"], transform=p_ax.transAxes)
            p_ax.text(0.08, 0.14, p_sub, fontsize=7.5, color=PALETTE["muted"], transform=p_ax.transAxes)

        # Summary of Detected Issues Section
        fig.text(0.06, 0.59, "EXECUTIVE AUDIT SUMMARY", fontsize=11, fontweight="bold", color=PALETTE["navy"])
        fig.text(0.70, 0.59, f"Critical: {ov.get('critical_issues', 0)}  |  Warnings: {ov.get('warning_issues', 0)}  |  Total: {ov.get('total_issues', 0)}",
                 fontsize=8.5, fontweight="bold", color=PALETTE["muted"])

        # Issues Table (top 6 issues)
        top_issues = audit.get("issues_list", [])[:6]
        table_ax = fig.add_axes([0.06, 0.26, 0.88, 0.30])
        table_ax.axis("off")
        if top_issues:
            cols = ["Severity", "Category", "Affected Column", "Issue Summary", "Recommended Action"]
            rows_data = []
            for item in top_issues:
                rows_data.append([
                    item.get("severity", "").upper(),
                    item.get("category", "").title(),
                    item.get("column", "")[:18],
                    item.get("title", "")[:32],
                    item.get("remediation", "")[:42],
                ])
            tb = table_ax.table(cellText=rows_data, colLabels=cols, loc="center", cellLoc="left")
            tb.auto_set_font_size(False)
            tb.set_fontsize(7.8)
            tb.scale(1.0, 1.4)
            for (r, c), cell in tb.get_celld().items():
                if r == 0:
                    cell.set_facecolor(PALETTE["navy"])
                    cell.set_text_props(color="#FFFFFF", fontweight="bold")
                else:
                    cell.set_facecolor("#FFFFFF" if r % 2 == 0 else "#F8FAFD")
                    cell.set_edgecolor(PALETTE["line"])
        else:
            table_ax.text(0.5, 0.5, "No data quality issues detected. Dataset is clean and ready.", ha="center", va="center", color=PALETTE["success"], fontsize=11)

        # Recommendations Box (Bottom)
        fig.text(0.06, 0.20, "KEY ACTIONABLE RECOMMENDATIONS", fontsize=10, fontweight="bold", color=PALETTE["navy"])
        rec_y = 0.165
        for r in audit.get("recommendations", [])[:3]:
            fig.text(0.08, rec_y, f"• {r}", fontsize=8.5, color=PALETTE["navy"])
            rec_y -= 0.035

        fig.text(0.94, 0.04, "Page 1 of 2  •  Insight Studio Data Quality", fontsize=8, color=PALETTE["muted"], ha="right")
        pdf.savefig(fig)
        plt.close(fig)

        # ================= PAGE 2: Column Audit Matrix =================
        fig2 = plt.figure(figsize=(11.69, 8.27), dpi=100)
        fig2.patch.set_facecolor("#FFFFFF")

        fig2.text(0.06, 0.94, "COLUMN QUALITY AUDIT MATRIX", fontsize=15, fontweight="bold", color=PALETTE["navy"])
        fig2.text(0.06, 0.905, "Detailed field-level health, null rates, and detected anomalies per column.", fontsize=9, color=PALETTE["muted"])
        line2 = plt.Line2D([0.06, 0.94], [0.885, 0.885], transform=fig2.transFigure, color=PALETTE["line"], linewidth=1.2)
        fig2.add_artist(line2)

        matrix_ax = fig2.add_axes([0.06, 0.12, 0.88, 0.74])
        matrix_ax.axis("off")

        cols_matrix = audit.get("column_matrix", [])[:16]
        if cols_matrix:
            headers = ["Column Name", "Detected Type", "Storage Dtype", "Health", "Missing", "Unique", "Status", "Action"]
            rows_m = []
            for c in cols_matrix:
                rows_m.append([
                    c["name"][:20],
                    c["detected_type"][:14],
                    c["raw_dtype"][:12],
                    f"{c['health_score']}%",
                    f"{c['missing_count']} ({c['missing_pct']}%)",
                    f"{c['unique_count']:,}",
                    c["status"].upper(),
                    c["recommended_action"][:38],
                ])
            tb2 = matrix_ax.table(cellText=rows_m, colLabels=headers, loc="center", cellLoc="left")
            tb2.auto_set_font_size(False)
            tb2.set_fontsize(7.5)
            tb2.scale(1.0, 1.35)
            for (r, c), cell in tb2.get_celld().items():
                if r == 0:
                    cell.set_facecolor(PALETTE["navy"])
                    cell.set_text_props(color="#FFFFFF", fontweight="bold")
                else:
                    cell.set_facecolor("#FFFFFF" if r % 2 == 0 else "#F8FAFD")
                    cell.set_edgecolor(PALETTE["line"])

        fig2.text(0.94, 0.04, "Page 2 of 2  •  Insight Studio Data Quality", fontsize=8, color=PALETTE["muted"], ha="right")
        pdf.savefig(fig2)
        plt.close(fig2)

    return buf.getvalue()


def generate_csv_report(audit: dict[str, Any]) -> str:
    """Generate a CSV detailing every quality issue and column health item."""
    lines: list[str] = [
        "Quality Dimension,Severity,Column,Issue Title,Details,Sample Values,Recommended Action"
    ]
    for issue in audit.get("issues_list", []):
        cat = issue.get("category", "")
        sev = issue.get("severity", "")
        col = f'"{issue.get("column", "")}"'
        title = f'"{issue.get("title", "")}"'
        det = f'"{issue.get("detail", "").replace(chr(34), chr(39))}"'
        samples = f'"{", ".join(str(s) for s in issue.get("sample_values", []))}"'
        rem = f'"{issue.get("remediation", "").replace(chr(34), chr(39))}"'
        lines.append(f"{cat},{sev},{col},{title},{det},{samples},{rem}")

    lines.append("")
    lines.append("--- COLUMN QUALITY MATRIX ---")
    lines.append("Column Name,Detected Type,Storage Dtype,Health Score,Status,Missing Count,Missing Pct,Unique Count,Recommended Action")
    for c in audit.get("column_matrix", []):
        name = f'"{c.get("name", "")}"'
        dtype = c.get("detected_type", "")
        raw = c.get("raw_dtype", "")
        health = c.get("health_score", 100)
        status = c.get("status", "")
        m_cnt = c.get("missing_count", 0)
        m_pct = c.get("missing_pct", 0)
        u_cnt = c.get("unique_count", 0)
        act = f'"{c.get("recommended_action", "").replace(chr(34), chr(39))}"'
        lines.append(f"{name},{dtype},{raw},{health},{status},{m_cnt},{m_pct},{u_cnt},{act}")

    return "\n".join(lines)


def generate_json_report(audit: dict[str, Any]) -> str:
    """Return JSON string representation of the audit."""
    return json.dumps(audit, indent=2, default=str)


def generate_html_report(audit: dict[str, Any], df: pd.DataFrame, source_name: str = "") -> str:
    """Generate a standalone printable HTML Executive Quality Report."""
    ov = audit.get("overview", {})
    source = source_name or audit.get("source_name", "Dataset")

    score_color = PALETTE["success"] if ov.get("quality_score", 0) >= 80 else (PALETTE["warning"] if ov.get("quality_score", 0) >= 65 else PALETTE["danger"])

    issues_html = ""
    for issue in audit.get("issues_list", []):
        sev_color = PALETTE["danger"] if issue["severity"] == "critical" else (PALETTE["warning"] if issue["severity"] == "warning" else PALETTE["blue"])
        issues_html += f"""
        <tr style="border-bottom:1px solid #E2E8F0;">
            <td style="padding:10px 12px;"><span style="display:inline-block; padding:3px 8px; border-radius:12px; font-size:11px; font-weight:700; color:#FFFFFF; background:{sev_color};">{issue['severity'].upper()}</span></td>
            <td style="padding:10px 12px; font-weight:600; color:#0B1528;">{issue['category'].title()}</td>
            <td style="padding:10px 12px; font-family:monospace; font-size:12px;">{issue['column']}</td>
            <td style="padding:10px 12px; font-weight:600;">{issue['title']}</td>
            <td style="padding:10px 12px; color:#475569; font-size:12px;">{issue['detail']}</td>
            <td style="padding:10px 12px; color:#0066FF; font-size:12px;">{issue['remediation']}</td>
        </tr>
        """

    matrix_html = ""
    for c in audit.get("column_matrix", []):
        st_color = PALETTE["success"] if c["status"] == "clean" else (PALETTE["warning"] if c["status"] == "warning" else PALETTE["danger"])
        matrix_html += f"""
        <tr style="border-bottom:1px solid #E2E8F0;">
            <td style="padding:9px 12px; font-weight:700; font-family:monospace;">{c['name']}</td>
            <td style="padding:9px 12px;"><span style="padding:2px 7px; border-radius:8px; font-size:11px; background:#F1F5F9; color:#0F172A;">{c['detected_type']}</span></td>
            <td style="padding:9px 12px; color:#64748B; font-size:12px;">{c['raw_dtype']}</td>
            <td style="padding:9px 12px; font-weight:700; color:{st_color};">{c['health_score']}%</td>
            <td style="padding:9px 12px; font-size:12px;">{c['missing_count']} ({c['missing_pct']}%)</td>
            <td style="padding:9px 12px; font-size:12px;">{c['unique_count']:,}</td>
            <td style="padding:9px 12px;"><span style="padding:3px 8px; border-radius:10px; font-size:11px; font-weight:700; color:{st_color}; background:{st_color}18;">{c['status'].upper()}</span></td>
            <td style="padding:9px 12px; color:#475569; font-size:12px;">{c['recommended_action']}</td>
        </tr>
        """

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Data Quality Audit Report - {source}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #F8FAFD; color: #0A1324; margin: 0; padding: 32px; }}
    .container {{ max-width: 1200px; margin: 0 auto; background: #FFFFFF; border-radius: 16px; border: 1px solid #E2E8F0; padding: 40px; box-shadow: 0 4px 20px rgba(0,0,0,0.04); }}
    .head {{ display: flex; justify-content: space-between; align-items: flex-start; border-bottom: 2px solid #E2E8F0; padding-bottom: 24px; margin-bottom: 30px; }}
    .head h1 {{ margin: 0 0 6px 0; font-size: 26px; font-weight: 800; color: #0B1528; letter-spacing: -0.5px; }}
    .badge {{ display: inline-block; padding: 4px 10px; border-radius: 999px; font-size: 11px; font-weight: 700; }}
    .grid-4 {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 30px; }}
    .card {{ background: #F8FAFD; border: 1px solid #E2E8F0; border-radius: 12px; padding: 20px; }}
    .card .val {{ font-size: 24px; font-weight: 800; color: #0B1528; margin: 6px 0 2px 0; }}
    .card .lbl {{ font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; color: #64748B; }}
    .card .sub {{ font-size: 12px; color: #64748B; }}
    table {{ width: 100%; border-collapse: collapse; text-align: left; font-size: 13px; margin-top: 14px; }}
    th {{ background: #0B1528; color: #FFFFFF; padding: 12px; font-weight: 700; font-size: 12px; letter-spacing: 0.3px; }}
    @media print {{ body {{ background: #FFFFFF; padding: 0; }} .container {{ border: none; box-shadow: none; padding: 0; }} }}
  </style>
</head>
<body>
  <div class="container">
    <div class="head">
      <div>
        <h1>Data Quality Audit Report</h1>
        <div style="color:#64748B; font-size:14px;">Dataset: <strong>{source}</strong> &middot; Generated on {audit.get('audited_at', '')}</div>
      </div>
      <div style="text-align:right;">
        <div style="font-size:32px; font-weight:900; color:{score_color};">{ov.get('quality_score', 0)}%</div>
        <div style="font-size:13px; font-weight:700; color:#0B1528;">Grade {ov.get('grade', 'B')} &middot; {ov.get('grade_label', '')}</div>
      </div>
    </div>

    <div class="grid-4">
      <div class="card">
        <div class="lbl">Completeness</div>
        <div class="val">{ov.get('completeness_score', 100)}%</div>
        <div class="sub">{audit['missing_analysis']['total_missing_cells']} missing cell(s)</div>
      </div>
      <div class="card">
        <div class="lbl">Uniqueness</div>
        <div class="val">{ov.get('uniqueness_score', 100)}%</div>
        <div class="sub">{audit['duplicate_analysis']['exact_duplicate_rows']} duplicate row(s)</div>
      </div>
      <div class="card">
        <div class="lbl">Type Consistency</div>
        <div class="val">{ov.get('type_consistency_score', 100)}%</div>
        <div class="sub">{audit['type_analysis']['type_mismatches_count']} mismatched column(s)</div>
      </div>
      <div class="card">
        <div class="lbl">Validity &amp; Outliers</div>
        <div class="val">{ov.get('validity_score', 100)}%</div>
        <div class="sub">{audit['unusual_analysis']['outliers_count']} statistical outlier(s)</div>
      </div>
    </div>

    <h2 style="font-size:18px; font-weight:800; color:#0B1528; margin-top:36px; margin-bottom:12px;">Detected Quality Issues ({len(audit.get('issues_list', []))})</h2>
    <table>
      <thead>
        <tr>
          <th>Severity</th>
          <th>Category</th>
          <th>Column</th>
          <th>Issue Title</th>
          <th>Details</th>
          <th>Recommended Action</th>
        </tr>
      </thead>
      <tbody>
        {issues_html or '<tr><td colspan="6" style="padding:20px; text-align:center; color:#10B981;">No data quality issues detected!</td></tr>'}
      </tbody>
    </table>

    <h2 style="font-size:18px; font-weight:800; color:#0B1528; margin-top:40px; margin-bottom:12px;">Column Quality Matrix ({len(audit.get('column_matrix', []))})</h2>
    <table>
      <thead>
        <tr>
          <th>Column</th>
          <th>Detected Type</th>
          <th>Dtype</th>
          <th>Health</th>
          <th>Missing</th>
          <th>Unique</th>
          <th>Status</th>
          <th>Recommended Action</th>
        </tr>
      </thead>
      <tbody>
        {matrix_html}
      </tbody>
    </table>
  </div>
</body>
</html>"""
