"""Insight Studio - upload a spreadsheet, understand it, clean it, explore it,
chart it and export a dashboard. Runs on localhost, a VPS, Hostinger or AWS.

Start locally:
    python app.py
"""
from __future__ import annotations

import io
import re
import shutil
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from flask import (Flask, abort, flash, jsonify, redirect, render_template,
                   request, send_file, send_from_directory, session, url_for)
from markupsafe import Markup, escape

from config import Config
from core import charts as charts_mod
from core import cleaner, insights, loader, profiler, reporter
from core import workspace as ws_mod
from core import llm as llm_mod
from core import quality as quality_mod

app = Flask(__name__)
app.config.from_object(Config)


@app.template_filter("markdown_inline")
def markdown_inline(text: Any) -> Markup:
    """Render **bold** and `code` fragments used in auto-generated insights."""
    safe = str(escape(text))
    safe = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", safe)
    safe = re.sub(r"`(.+?)`", r"<code>\1</code>", safe)
    return Markup(safe)

# ensure folders exist
Config.WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
Config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# in-memory caches: sid -> (key, data)
_PROFILE_CACHE: dict[str, tuple[str, dict[str, Any]]] = {}
_AUDIT_CACHE: dict[str, tuple[str, dict[str, Any]]] = {}
_EXPORT_BYTES_CACHE: dict[str, dict[str, tuple[str, bytes, str, str]]] = {}


def clear_session_caches(sid: str) -> None:
    _PROFILE_CACHE.pop(sid, None)
    _AUDIT_CACHE.pop(sid, None)
    _EXPORT_BYTES_CACHE.pop(sid, None)


# --------------------------------------------------------------------------- #
# session / workspace helpers
# --------------------------------------------------------------------------- #
def get_sid() -> str:
    if "sid" not in session:
        session["sid"] = ws_mod.new_sid()
        session.permanent = True
    return session["sid"]


@app.before_request
def _janitor_and_key_sync() -> None:
    # cheap probabilistic cleanup of stale workspaces
    if time.time() % 50 < 0.5:
        ws_mod.cleanup_old_workspaces(Config.WORKSPACE_DIR, Config.WORKSPACE_TTL_HOURS)



def ws() -> Path:
    return ws_mod.workspace(Config.WORKSPACE_DIR, get_sid())


def state() -> dict[str, Any]:
    return ws_mod.load_state(ws())


def save_state(new_state: dict[str, Any]) -> None:
    ws_mod.save_state(ws(), new_state)


def require_dataset():
    st = state()
    if not st.get("source_path"):
        return None, st
    path = ws() / st["source_path"]
    if not path.exists():
        return None, st
    return path, st


def current_df(force_raw: bool = False) -> tuple[pd.DataFrame, dict[str, Any], dict[str, Any]]:
    """Return (dataframe, state, info) using cleaned data when available."""
    st = state()
    path, st = require_dataset()
    if path is None:
        raise FileNotFoundError("No dataset uploaded yet")

    use_cleaned = bool(st.get("use_cleaned", True)) and ws_mod.has_cleaned(ws()) and not force_raw
    if use_cleaned:
        df = ws_mod.load_cleaned(ws())
        if df is None:
            df, info = loader.read_table(path, st.get("sheet"), st.get("header_row", 0))
        else:
            info = {"kind": st.get("source_kind"), "sheet": st.get("sheet"), "cleaned": True}
    else:
        df, info = loader.read_table(path, st.get("sheet"), st.get("header_row", 0))
        info["cleaned"] = False
    return df, st, info


def cached_profile(df: pd.DataFrame, cache_key: str) -> dict[str, Any]:
    sid = get_sid()
    cached = _PROFILE_CACHE.get(sid)
    if cached and cached[0] == cache_key:
        return cached[1]
    profile = profiler.profile_frame(df)
    _PROFILE_CACHE[sid] = (cache_key, profile)
    return profile


def cached_audit(df: pd.DataFrame, cache_key: str, source_name: str = "Dataset") -> dict[str, Any]:
    sid = get_sid()
    cached = _AUDIT_CACHE.get(sid)
    if cached and cached[0] == cache_key:
        return cached[1]
    audit = quality_mod.audit_dataset_quality(df, source_name)
    _AUDIT_CACHE[sid] = (cache_key, audit)
    return audit


def dataset_cache_key(st: dict[str, Any]) -> str:
    parts = [str(st.get("source_path")), str(st.get("sheet")), str(st.get("use_cleaned"))]
    cleaned = ws() / ws_mod.CLEANED_FILE
    if cleaned.exists():
        parts.append(str(cleaned.stat().st_mtime))
    src = ws() / (st.get("source_path") or "")
    if src.exists():
        parts.append(str(src.stat().st_mtime))
    return "|".join(parts)


def nav_context() -> dict[str, Any]:
    """Sidebar context shared by all pages."""
    st = state()
    has_data = bool(st.get("source_path"))
    cleaned = ws_mod.has_cleaned(ws())
    charts_count = len(ws_mod.charts_index(ws()))
    ep = request.endpoint or ""

    step1_done = has_data
    step2_done = has_data
    step3_done = has_data
    step4_done = has_data and (cleaned or ep in ("insights_page", "dashboard_page", "chat_page") or charts_count > 0)
    step5_done = has_data and (charts_count > 0 or ep in ("dashboard_page", "chat_page"))
    step6_done = has_data and (ep == "dashboard_page" or charts_count > 0)
    step7_done = has_data and ep == "chat_page"

    return {
        "nav_state": st,
        "nav_has_data": has_data,
        "nav_cleaned": cleaned,
        "nav_charts": charts_count,
        "nav_step1_done": step1_done,
        "nav_step2_done": step2_done,
        "nav_step3_done": step3_done,
        "nav_step4_done": step4_done,
        "nav_step5_done": step5_done,
        "nav_step6_done": step6_done,
        "nav_step7_done": step7_done,
        "app_name": Config.APP_NAME,
    }


# --------------------------------------------------------------------------- #
# pages: upload
# --------------------------------------------------------------------------- #
@app.route("/")
def index():
    if request.args.get("new") or request.args.get("clear"):
        sid = get_sid()
        ws_mod.delete_workspace(Config.WORKSPACE_DIR, sid)
        _PROFILE_CACHE.pop(sid, None)
    st = state()
    has_existing = bool(st.get("source_path")) and not (request.args.get("new") or request.args.get("clear"))

    df_for_chat = None
    chat_suggestions = [
        "What file formats can I upload?",
        "How does automated data cleaning work?",
        "What kinds of charts can Insight Studio generate?",
        "Can I upload multiple files or a whole folder?",
    ]
    if has_existing:
        try:
            df_for_chat, _st, _info = current_df()
            num_cols = df_for_chat.select_dtypes(include=["number"]).columns.tolist()
            cat_cols = df_for_chat.select_dtypes(include=["object", "category", "string"]).columns.tolist()
            chat_suggestions = [
                f"Summarize key trends in {st.get('source_file')}",
            ]
            if cat_cols and num_cols:
                chat_suggestions.append(f"Which {cat_cols[0]} has highest total {num_cols[0]}?")
            if len(num_cols) >= 2:
                chat_suggestions.append(f"What is the correlation between {num_cols[0]} and {num_cols[1]}?")
            chat_suggestions.append("Which 3 charts would give leaders the fastest insights?")
        except Exception:
            pass

    return render_template(
        "index.html",
        allowed=sorted(Config.ALLOWED_EXTENSIONS),
        max_mb=Config.MAX_CONTENT_LENGTH // (1024 * 1024),
        has_sample=Config.SAMPLE_FILE.exists(),
        has_existing=has_existing,
        existing_file=st.get("source_file"),
        existing_rows=len(df_for_chat) if df_for_chat is not None else 0,
        existing_cols=len(df_for_chat.columns) if df_for_chat is not None else 0,
        chat_suggestions=chat_suggestions[:4],
        has_key=bool(get_openrouter_key()),
        model_name="Studio AI Assistant",
        **nav_context(),
    )



@app.route("/upload", methods=["POST"])
def upload():
    files = request.files.getlist("file")
    if not files or all(f.filename == "" for f in files):
        flash("Choose a file first.", "error")
        return redirect(url_for("index"))

    sid = get_sid()
    w = ws_mod.workspace(Config.WORKSPACE_DIR, sid)
    # fresh workspace for a new upload: wipe all old charts, tables, pickled data, and cache
    for item in w.iterdir():
        shutil.rmtree(item, ignore_errors=True) if item.is_dir() else item.unlink()
    clear_session_caches(sid)

    saved_paths: list[Path] = []
    original_names: list[str] = []

    for file in files:
        if not file.filename or file.filename == "":
            continue
        ext = Path(file.filename).suffix.lower()
        if ext not in Config.ALLOWED_EXTENSIONS:
            continue
        safe_name = f"source_{len(saved_paths)}{ext}"
        target = w / safe_name
        file.save(target)
        saved_paths.append(target)
        original_names.append(file.filename)

    if not saved_paths:
        flash("No supported files found. Allowed: " + ", ".join(sorted(Config.ALLOWED_EXTENSIONS)), "error")
        return redirect(url_for("index"))

    # Multi-file: merge them
    if len(saved_paths) > 1:
        try:
            merged_df, merge_info = loader.read_multiple_files(saved_paths)
        except Exception as exc:  # noqa: BLE001
            flash(f"Could not read/merge files: {exc}", "error")
            return redirect(url_for("index"))

        # Save merged as a pickle for fast re-reads
        ws_mod.save_cleaned(w, merged_df)
        display_name = f"{len(original_names)} files merged"
        st = {
            "source_file": display_name,
            "source_path": saved_paths[0].name,  # fallback path
            "source_kind": "multi",
            "sheets": [],
            "sheet": None,
            "use_cleaned": True,
            "multi_files": original_names,
        }
        ws_mod.save_state(w, {**ws_mod.DEFAULT_STATE, **st})
        _PROFILE_CACHE.pop(sid, None)
        flash(f"Merged {merge_info.get('files_merged', len(saved_paths))} files ({merge_info.get('total_files', len(saved_paths))} total). Analysis ready.", "success")
        next_dest = request.form.get("next") or request.args.get("next")
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"ok": True, "redirect": url_for("chat_page") if next_dest == "chat" else url_for("quality_page")})
        if next_dest == "chat":
            return redirect(url_for("chat_page"))
        return redirect(url_for("quality_page"))

    # Single file flow
    file_path = saved_paths[0]
    ext = file_path.suffix.lower()
    st = {"source_file": original_names[0], "source_path": file_path.name}
    try:
        sheets = loader.list_sheets(file_path)
    except Exception as exc:  # noqa: BLE001
        flash(f"Could not read the file: {exc}", "error")
        return redirect(url_for("index"))

    if ext in loader.PDF_EXTENSIONS:
        st["source_kind"] = "pdf"
    elif ext in loader.EXCEL_EXTENSIONS:
        st["source_kind"] = "excel"
    else:
        st["source_kind"] = "csv"
    st["sheets"] = [s["name"] for s in sheets]
    st["sheet"] = st["sheets"][0] if st["sheets"] else None

    ws_mod.save_state(w, {**ws_mod.DEFAULT_STATE, **st})
    clear_session_caches(sid)

    next_dest = request.form.get("next") or request.args.get("next")
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"ok": True, "redirect": url_for("chat_page") if next_dest == "chat" else url_for("quality_page")})
    if next_dest == "chat":
        return redirect(url_for("chat_page"))

    if st["source_kind"] == "excel" and len(sheets) > 1:
        return redirect(url_for("sheet_page"))
    return redirect(url_for("quality_page"))


@app.route("/sample", methods=["POST"])
def load_sample():
    if not Config.SAMPLE_FILE.exists():
        flash("Sample file not bundled with this build.", "error")
        return redirect(url_for("index"))
    sid = get_sid()
    w = ws_mod.workspace(Config.WORKSPACE_DIR, sid)
    # Wipe any previous workspace data (charts, tables, cleaned data)
    for item in w.iterdir():
        shutil.rmtree(item, ignore_errors=True) if item.is_dir() else item.unlink()
    clear_session_caches(sid)
    target = w / "source.xlsx"
    shutil.copyfile(Config.SAMPLE_FILE, target)
    sheets = loader.list_sheets(target)
    sheet = "Sales_Data" if "Sales_Data" in [s["name"] for s in sheets] else sheets[0]["name"]
    ws_mod.save_state(w, {**ws_mod.DEFAULT_STATE,
                          "source_file": Config.SAMPLE_FILE.name,
                          "source_path": "source.xlsx",
                          "source_kind": "excel",
                          "sheets": [s["name"] for s in sheets],
                          "sheet": sheet})
    return redirect(url_for("quality_page"))


@app.route("/sheet", methods=["GET", "POST"])
def sheet_page():
    path, st = require_dataset()
    if path is None:
        return redirect(url_for("index"))

    if request.method == "POST":
        chosen = request.form.get("sheet")
        if chosen:
            st["sheet"] = chosen
            ws_mod.save_state(ws(), st)
            _PROFILE_CACHE.pop(get_sid(), None)
        return redirect(url_for("quality_page"))

    previews = []
    try:
        previews = loader.list_sheets(path)
    except Exception as exc:  # noqa: BLE001
        flash(f"Could not list sheets: {exc}", "error")
    return render_template("sheet.html", previews=previews, **nav_context())


# --------------------------------------------------------------------------- #
# pages: understand + clean
# --------------------------------------------------------------------------- #
@app.route("/profile")
def profile_page():
    path, st = require_dataset()
    if path is None:
        return redirect(url_for("index"))
    try:
        df, st, info = current_df()
    except Exception as exc:  # noqa: BLE001
        flash(f"Could not load the dataset: {exc}", "error")
        return redirect(url_for("index"))

    ckey = dataset_cache_key(st)
    profile = cached_profile(df, ckey)
    kpis = insights.build_kpis(df, profile)

    audit = cached_audit(df, ckey, st.get("source_file", "Dataset"))
    quality_summary = {
        "quality_score": audit["overview"]["quality_score"],
        "grade": audit["overview"]["grade"],
        "grade_label": audit["overview"]["grade_label"],
        "total_issues": audit["overview"]["total_issues"],
        "missing_cells": audit["missing_analysis"]["total_missing_cells"],
        "duplicate_rows": audit["duplicate_analysis"]["exact_duplicate_rows"],
        "type_mismatches": audit["type_analysis"]["type_mismatches_count"],
        "outliers_count": audit["unusual_analysis"]["outliers_count"],
    }

    return render_template("profile.html", profile=profile, kpis=kpis, info=info,
                           quality_summary=quality_summary,
                           using_cleaned=ws_mod.has_cleaned(ws()) and st.get("use_cleaned", True),
                           **nav_context())


@app.route("/quality")
def quality_page():
    path, st = require_dataset()
    if path is None:
        return redirect(url_for("index"))
    try:
        df, st, info = current_df()
    except Exception as exc:  # noqa: BLE001
        flash(f"Could not load the dataset: {exc}", "error")
        return redirect(url_for("index"))

    ckey = dataset_cache_key(st)
    audit = cached_audit(df, ckey, st.get("source_file", "Dataset"))

    # Prepare preview rows (up to 60 sample rows)
    sample_df = df.head(60)
    preview_rows = []
    rows_with_nulls = 0
    rows_with_outliers = 0

    # Build outlier lookup map: col -> (lower_bound, upper_bound)
    outlier_map: dict[str, tuple[float, float]] = {}
    for oc in audit.get("unusual_analysis", {}).get("columns_with_outliers", []):
        c_name = oc["column"]
        outlier_map[c_name] = (oc["lower_bound"], oc["upper_bound"])

    for idx, row in sample_df.iterrows():
        row_dict: dict[str, Any] = {}
        has_null = False
        has_outlier = False
        for col in df.columns:
            val = row[col]
            if pd.isna(val):
                has_null = True
                row_dict[str(col)] = ""
            else:
                row_dict[str(col)] = str(val)
                if str(col) in outlier_map and isinstance(val, (int, float, np.number)):
                    lb, ub = outlier_map[str(col)]
                    if val < lb or val > ub:
                        has_outlier = True

        if has_null:
            rows_with_nulls += 1
        if has_outlier:
            rows_with_outliers += 1

        preview_rows.append({
            "index": idx,
            "cells": row_dict,
            "_has_null": has_null,
            "_has_outlier": has_outlier,
        })

    return render_template(
        "quality.html",
        audit=audit,
        preview_rows=preview_rows,
        rows_with_nulls_count=rows_with_nulls,
        rows_with_outliers_count=rows_with_outliers,
        info=info,
        using_cleaned=ws_mod.has_cleaned(ws()) and st.get("use_cleaned", True),
        **nav_context(),
    )


@app.route("/quality/download/<fmt>")
def quality_download(fmt: str):
    path, st = require_dataset()
    if path is None:
        return redirect(url_for("index"))
    try:
        df, st, _info = current_df()
    except Exception as exc:  # noqa: BLE001
        flash(f"Could not load the dataset: {exc}", "error")
        return redirect(url_for("index"))

    sid = get_sid()
    ckey = dataset_cache_key(st)
    audit = cached_audit(df, ckey, st.get("source_file", "Dataset"))
    clean_name = re.sub(r"[^\w\-]+", "_", Path(st.get("source_file", "dataset")).stem)

    fmt_lower = fmt.lower().strip()
    user_caches = _EXPORT_BYTES_CACHE.setdefault(sid, {})

    if fmt_lower == "pdf":
        cached_export = user_caches.get("pdf")
        if cached_export and cached_export[0] == ckey:
            pdf_bytes = cached_export[1]
        else:
            pdf_bytes = quality_mod.generate_pdf_report(audit, df, st.get("source_file", "Dataset"))
            user_caches["pdf"] = (ckey, pdf_bytes, "application/pdf", f"{clean_name}_data_quality_report.pdf")
        return send_file(
            io.BytesIO(pdf_bytes),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=f"{clean_name}_data_quality_report.pdf",
        )
    elif fmt_lower == "csv":
        cached_export = user_caches.get("csv")
        if cached_export and cached_export[0] == ckey:
            csv_bytes = cached_export[1]
        else:
            csv_str = quality_mod.generate_csv_report(audit)
            csv_bytes = csv_str.encode("utf-8-sig")
            user_caches["csv"] = (ckey, csv_bytes, "text/csv", f"{clean_name}_data_quality_report.csv")
        return send_file(
            io.BytesIO(csv_bytes),
            mimetype="text/csv",
            as_attachment=True,
            download_name=f"{clean_name}_data_quality_report.csv",
        )
    elif fmt_lower == "json":
        json_str = quality_mod.generate_json_report(audit)
        return send_file(
            io.BytesIO(json_str.encode("utf-8")),
            mimetype="application/json",
            as_attachment=True,
            download_name=f"{clean_name}_data_quality_report.json",
        )
    abort(404)


@app.route("/quality/view")
def quality_report_view():
    path, st = require_dataset()
    if path is None:
        return redirect(url_for("index"))
    try:
        df, st, _info = current_df()
    except Exception as exc:  # noqa: BLE001
        flash(f"Could not load the dataset: {exc}", "error")
        return redirect(url_for("index"))

    ckey = dataset_cache_key(st)
    audit = cached_audit(df, ckey, st.get("source_file", "Dataset"))
    return quality_mod.generate_html_report(audit, df, st.get("source_file", "Dataset"))



@app.route("/clean")
def clean_page():
    path, st = require_dataset()
    if path is None:
        return redirect(url_for("index"))
    raw_df, _st, _info = current_df(force_raw=True)
    plan = st.get("clean_plan")
    if not plan:
        plan = cleaner.suggest_plan(raw_df, profiler.profile_frame(raw_df))
        st["clean_plan"] = plan
        ws_mod.save_state(ws(), st)

    # remember what the user last ticked so the form keeps their choices
    for step in plan:
        step["render_checked"] = bool(step.get("enabled", step.get("default_enabled", False)))

    cleaned_df = ws_mod.load_cleaned(ws())
    preview = None
    if cleaned_df is not None:
        preview = {
            "columns": [str(c) for c in cleaned_df.columns],
            "rows": profiler._records(cleaned_df.head(15))["rows"],
            "shape": [int(cleaned_df.shape[0]), int(cleaned_df.shape[1])],
        }

    # Summarize mistakes / issues in the data for the user at a glance
    prof = cached_profile(raw_df, dataset_cache_key(st) + "_raw")
    total_missing = sum(c.get("null_count", 0) for c in prof.get("columns", []))
    missing_cols = [c["name"] for c in prof.get("columns", []) if c.get("null_count", 0) > 0]
    dup_rows = prof.get("overview", {}).get("duplicate_rows", 0)
    issue_summary = {
        "total_missing": total_missing,
        "missing_cols": missing_cols,
        "duplicate_rows": dup_rows,
        "suggested_steps_count": len([s for s in plan if s.get("default_enabled")]),
    }

    return render_template("clean.html", plan=plan, log=st.get("clean_log"),
                           cleaned_at=st.get("cleaned_at"), preview=preview,
                           raw_shape=[int(raw_df.shape[0]), int(raw_df.shape[1])],
                           columns_hint=", ".join(str(c) for c in raw_df.columns),
                           issue_summary=issue_summary,
                           **nav_context())


@app.route("/clean/apply", methods=["POST"])
def clean_apply():
    path, st = require_dataset()
    if path is None:
        return redirect(url_for("index"))

    plan: list[dict[str, Any]] = st.get("clean_plan") or []
    form = request.form
    action = form.get("action", "")
    redirect_to_insights = action in ("clean_and_insights", "clean_all") or form.get("next") == "insights"

    for step in plan:
        sid_key = step["id"]
        if action in ("clean_and_insights", "clean_all"):
            step["enabled"] = bool(step.get("default_enabled", True))
        else:
            step["enabled"] = form.get(f"enable__{sid_key}") == "on"

        # fill_missing: per column strategies
        if step["type"] == "fill_missing":
            strategies = step.setdefault("params", {}).setdefault("strategies", {})
            for col in list(strategies.keys()):
                strategy = form.get(f"strategy__{col}")
                if strategy:
                    strategies[col] = {"strategy": strategy, "value": form.get(f"value__{col}") or None}
        # derived columns
        if step["type"] == "derived_columns":
            names = form.getlist("derived_name")
            exprs = form.getlist("derived_expr")
            rows = []
            for name, expr in zip(names, exprs):
                name, expr = (name or "").strip(), (expr or "").strip()
                if name and expr:
                    rows.append({"name": name, "expression": expr, "description": "custom"})
            step.setdefault("params", {})["rows"] = rows
            step["enabled"] = bool(rows) and step["enabled"]
        # numeric params
        if step["type"] == "cap_outliers":
            try:
                step.setdefault("params", {})["factor"] = float(form.get("outlier_factor", 1.5))
            except ValueError:
                step["params"]["factor"] = 1.5

    raw_df, _st, _info = current_df(force_raw=True)
    cleaned_df, log = cleaner.apply_plan(raw_df, plan)
    log["applied_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

    ws_mod.save_cleaned(ws(), cleaned_df)
    st["clean_plan"] = plan
    st["clean_log"] = log
    st["cleaned_at"] = log["applied_at"]
    st["use_cleaned"] = True
    ws_mod.save_state(ws(), st)
    clear_session_caches(get_sid())

    if redirect_to_insights:
        flash("Data cleaned successfully! You are now viewing insights & charts for your cleaned data.", "success")
        return redirect(url_for("insights_page"))

    flash("Cleaning applied. Analysis now uses the cleaned data.", "success")
    return redirect(url_for("clean_page"))


@app.route("/clean/reset", methods=["POST"])
def clean_reset():
    st = state()
    ws_mod.remove_cleaned(ws())
    st["clean_log"] = None
    st["cleaned_at"] = None
    st["clean_plan"] = None
    st["use_cleaned"] = True
    ws_mod.save_state(ws(), st)
    clear_session_caches(get_sid())
    flash("Cleaning cleared - back to the original file.", "success")
    return redirect(url_for("clean_page"))


@app.route("/use-cleaned", methods=["POST"])
def toggle_use_cleaned():
    st = state()
    st["use_cleaned"] = request.form.get("use") == "1"
    ws_mod.save_state(ws(), st)
    clear_session_caches(get_sid())
    return redirect(request.form.get("next") or url_for("profile_page"))


# --------------------------------------------------------------------------- #
# pages: insights
# --------------------------------------------------------------------------- #
@app.route("/insights")
def insights_page():
    path, st = require_dataset()
    if path is None:
        return redirect(url_for("index"))

    df, st, info = current_df()
    try:
        profile = cached_profile(df, dataset_cache_key(st))
    except Exception as exc:  # noqa: BLE001
        flash(f"Could not profile the dataset: {exc}", "error")
        return redirect(url_for("index"))

    kpis = insights.build_kpis(df, profile)
    sentences = insights.build_sentences(df, profile)
    suggestions = insights.suggest_charts(df, profile)
    auto_tables = insights.build_auto_tables(df, profile)

    columns_info = [
        {
            "name": c["name"],
            "kind": c["kind"],
            "kind_label": c["kind_label"],
            "is_numeric": c["kind"] == profiler.KIND_NUMERIC,
            "is_date": c["kind"] == profiler.KIND_DATETIME,
        }
        for c in profile["columns"]
        if c["kind"] not in (profiler.KIND_EMPTY,)
    ]

    saved_charts = ws_mod.charts_index(ws())

    return render_template(
        "insights.html",
        profile=profile, kpis=kpis, sentences=sentences, suggestions=suggestions,
        auto_tables=auto_tables, columns_info=columns_info,
        chart_types=charts_mod.CHART_TYPES, aggregations=charts_mod.AGGREGATIONS,
        frequencies=charts_mod.FREQUENCIES, palettes=list(charts_mod.PALETTES.keys()),
        export_formats=charts_mod.EXPORT_FORMATS,
        saved_charts=saved_charts, info=info,
        using_cleaned=ws_mod.has_cleaned(ws()) and st.get("use_cleaned", True),
        **nav_context(),
    )


# --------------------------------------------------------------------------- #
# api: charts
# --------------------------------------------------------------------------- #
@app.post("/api/chart/render")
def api_chart_render():
    payload = request.get_json(silent=True) or request.form.to_dict()
    spec = _spec_from_payload(payload)
    try:
        df, st, _info = current_df()
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "No dataset uploaded"}), 400

    chart_id = charts_mod.new_chart_id(spec.get("title") or spec.get("chart_type", "chart"))
    out = ws_mod.chart_dir(ws()) / f"{chart_id}.png"
    try:
        meta = charts_mod.render_chart(df, spec, out, dpi=Config.DEFAULT_DPI)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 400

    index = ws_mod.charts_index(ws())
    item = {
        "id": chart_id,
        "title": meta["title"],
        "image": out.name,
        "spec": spec,
        "on_dashboard": bool(payload.get("on_dashboard", True)),
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    index = [i for i in index if i["id"] != chart_id] + [item]
    ws_mod.save_charts_index(ws(), index)

    return jsonify({
        "ok": True,
        "chart": {**item, "image_url": url_for("chart_image", chart_id=chart_id)},
        "downloads": [
            {"fmt": f["id"], "label": f["label"],
             "url": url_for("chart_download", chart_id=chart_id, fmt=f["id"])}
            for f in charts_mod.EXPORT_FORMATS
        ],
    })


def _spec_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    def get(key: str, default: Any = None) -> Any:
        value = payload.get(key, default)
        if isinstance(value, str):
            value = value.strip()
            if value == "":
                return default
        return value

    def as_int(key: str, default: int = 0) -> int:
        try:
            return int(float(get(key, default) or default))
        except (TypeError, ValueError):
            return default

    def as_float(key: str, default: float) -> float:
        try:
            return float(get(key, default) or default)
        except (TypeError, ValueError):
            return default

    def as_bool(key: str, default: bool = False) -> bool:
        value = payload.get(key, default)
        if isinstance(value, bool):
            return value
        return str(value).lower() in ("1", "true", "on", "yes", default)

    spec = {
        "chart_type": get("chart_type", "bar"),
        "x": get("x"),
        "y": get("y"),
        "group": get("group"),
        "agg": get("agg", "sum"),
        "freq": get("freq", "M"),
        "top_n": as_int("top_n", 0),
        "bins": as_int("bins", 20),
        "title": get("title", ""),
        "palette": get("palette", "studio"),
        "show_values": as_bool("show_values", True),
        "x_is_date": as_bool("x_is_date", False),
        "width": as_float("width", 10),
        "height": as_float("height", 5.5),
    }
    if spec["chart_type"] in ("count", "heatmap"):
        spec["y"] = None if spec["chart_type"] == "heatmap" else spec.get("y")
    return spec


def _missing_spec_columns(df: pd.DataFrame, spec: dict[str, Any]) -> list[str]:
    """Columns referenced by a saved chart that no longer exist in the data."""
    missing: list[str] = []
    for key in ("x", "y", "group"):
        value = spec.get(key)
        if isinstance(value, str) and value and value not in df.columns:
            missing.append(value)
    return missing


@app.get("/chart/<chart_id>/image")
def chart_image(chart_id: str):
    for item in ws_mod.charts_index(ws()):
        if item["id"] == chart_id:
            return send_from_directory(ws_mod.chart_dir(ws()), item["image"])
    abort(404)


@app.get("/chart/<chart_id>/download")
def chart_download(chart_id: str):
    fmt = (request.args.get("fmt") or "png").lower()
    valid = {f["id"] for f in charts_mod.EXPORT_FORMATS} | {"jpeg"}
    if fmt not in valid:
        abort(400)

    item = next((i for i in ws_mod.charts_index(ws()) if i["id"] == chart_id), None)
    if item is None:
        abort(404)

    df, _st, _info = current_df()
    missing = _missing_spec_columns(df, item.get("spec") or {})
    if missing:
        flash(
            f"Can't export '{item.get('title', 'this chart')}': the current data has no "
            f"column {', '.join(repr(c) for c in missing)}. Switch data version or rebuild the chart.",
            "error",
        )
        return redirect(url_for("insights_page"))
    try:
        data = charts_mod.render_to_bytes(df, item["spec"], fmt, dpi=220)
    except Exception as exc:  # noqa: BLE001
        flash(f"Export failed: {exc}", "error")
        abort(500)

    filename = f"{chart_id}.{ 'jpg' if fmt == 'jpeg' else fmt }"
    return send_file(io.BytesIO(data), mimetype=charts_mod.export_mime(fmt),
                     as_attachment=True, download_name=filename)


@app.post("/api/chart/dashboard")
def api_chart_dashboard():
    payload = request.get_json(silent=True) or {}
    chart_id = payload.get("id")
    on = bool(payload.get("on", True))
    index = ws_mod.charts_index(ws())
    found = False
    for item in index:
        if item["id"] == chart_id:
            item["on_dashboard"] = on
            found = True
    if not found:
        return jsonify({"ok": False, "error": "Chart not found"}), 404
    ws_mod.save_charts_index(ws(), index)
    return jsonify({"ok": True, "count": sum(1 for i in index if i.get("on_dashboard"))})


@app.post("/api/chart/delete")
def api_chart_delete():
    payload = request.get_json(silent=True) or {}
    chart_id = payload.get("id")
    index = ws_mod.charts_index(ws())
    item = next((i for i in index if i["id"] == chart_id), None)
    if item:
        try:
            (ws_mod.chart_dir(ws()) / item["image"]).unlink(missing_ok=True)
        except OSError:
            pass
        index = [i for i in index if i["id"] != chart_id]
        ws_mod.save_charts_index(ws(), index)
    return jsonify({"ok": True})


# --------------------------------------------------------------------------- #
# api: tables
# --------------------------------------------------------------------------- #
@app.post("/api/table/build")
def api_table_build():
    payload = request.get_json(silent=True) or {}
    rows = payload.get("rows") or []
    metrics = payload.get("metrics") or []
    sort_by = payload.get("sort_by") or None
    sort_desc = bool(payload.get("sort_desc", True))
    title = (payload.get("title") or "Custom analysis").strip()

    try:
        df, _st, _info = current_df()
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "No dataset uploaded"}), 400

    try:
        table = insights.group_table(df, None, rows, metrics, sort_by, sort_desc)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 400

    table_id = charts_mod.new_chart_id(title or "table")
    index = ws_mod.tables_index(ws())
    item = {
        "id": table_id,
        "title": title,
        "group_cols": rows,
        "metrics": metrics,
        "sort_by": sort_by,
        "sort_desc": sort_desc,
        "on_dashboard": bool(payload.get("on_dashboard", False)),
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "snapshot": {"columns": table["columns"], "rows": table["rows"]},
    }
    index = [i for i in index if i["id"] != table_id] + [item]
    ws_mod.save_tables_index(ws(), index)

    return jsonify({
        "ok": True,
        "table": {"id": table_id, "title": title, "columns": table["columns"],
                  "rows": table["rows"], "total_rows": table["total_rows"],
                  "on_dashboard": item["on_dashboard"]},
        "csv_url": url_for("table_download", table_id=table_id, fmt="csv"),
    })


@app.post("/api/table/dashboard")
def api_table_dashboard():
    payload = request.get_json(silent=True) or {}
    table_id = payload.get("id")
    on = bool(payload.get("on", True))
    index = ws_mod.tables_index(ws())
    found = False
    for item in index:
        if item["id"] == table_id:
            item["on_dashboard"] = on
            found = True
    if not found:
        return jsonify({"ok": False, "error": "Table not found"}), 404
    ws_mod.save_tables_index(ws(), index)
    return jsonify({"ok": True})


@app.post("/api/table/delete")
def api_table_delete():
    payload = request.get_json(silent=True) or {}
    table_id = payload.get("id")
    index = [i for i in ws_mod.tables_index(ws()) if i["id"] != table_id]
    ws_mod.save_tables_index(ws(), index)
    return jsonify({"ok": True})


@app.get("/table/<table_id>/download")
def table_download(table_id: str):
    fmt = (request.args.get("fmt") or "csv").lower()
    item = next((i for i in ws_mod.tables_index(ws()) if i["id"] == table_id), None)
    if item is None:
        abort(404)
    df, _st, _info = current_df()
    table = insights.group_table(df, None, item["group_cols"], item["metrics"],
                                 item.get("sort_by"), item.get("sort_desc", True))
    frame: pd.DataFrame = table["table"]
    if fmt == "csv":
        data = frame.to_csv(index=False).encode("utf-8-sig")
        return send_file(io.BytesIO(data), mimetype="text/csv", as_attachment=True,
                         download_name=f"{table_id}.csv")
    abort(400)


# --------------------------------------------------------------------------- #
# pages: dashboard
# --------------------------------------------------------------------------- #
@app.route("/dashboard")
def dashboard_page():
    path, st = require_dataset()
    if path is None:
        return redirect(url_for("index"))

    df, st, info = current_df()
    ckey = dataset_cache_key(st)
    profile = cached_profile(df, ckey)
    audit = cached_audit(df, ckey, st.get("source_file", "Dataset"))

    kpis = st.get("executive_kpis") or insights.discover_executive_kpis(df, profile, audit)
    narrative = st.get("executive_narrative")

    chart_items = [i for i in ws_mod.charts_index(ws()) if i.get("on_dashboard")]
    table_items = [i for i in ws_mod.tables_index(ws()) if i.get("on_dashboard")]
    for item in chart_items:
        item["image_url"] = url_for("chart_image", chart_id=item["id"])
    exports = sorted(
        (p.name for p in (ws() / ws_mod.EXPORTS_DIR).glob("*")) if (ws() / ws_mod.EXPORTS_DIR).exists() else [],
        reverse=True,
    )
    return render_template("dashboard.html", kpis=kpis, narrative=narrative, charts=chart_items, tables=table_items,
                           exports=exports[:10], dataset_name=st.get("source_file") or "dataset",
                           info=info, **nav_context())


@app.post("/api/dashboard/auto-build")
def api_dashboard_auto_build():
    path, st = require_dataset()
    if path is None:
        return jsonify({"ok": False, "error": "No dataset uploaded"}), 400

    try:
        df, st, info = current_df()
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": f"Failed to load dataset: {exc}"}), 400

    ckey = dataset_cache_key(st)
    profile = cached_profile(df, ckey)
    audit = cached_audit(df, ckey, st.get("source_file", "Dataset"))

    # 1. Discover Executive KPIs
    kpis = insights.discover_executive_kpis(df, profile, audit)

    # 2. Query AI Narrative & Complementary Charts
    api_key = get_openrouter_key() or ""
    narrative_data = llm_mod.generate_dashboard_narrative_ai(api_key, df, profile, kpis)

    recommended_charts = narrative_data.get("recommended_charts", [])
    if not recommended_charts:
        recommended_charts = llm_mod._generate_smart_fallback_charts(df)

    # 3. Render 4 to 6 Publication-Grade Charts and mount onto dashboard
    created_charts = []
    charts_idx = ws_mod.charts_index(ws())
    chart_dir = ws_mod.chart_dir(ws())

    palettes = ["studio", "ocean", "sunset", "forest", "berry"]

    for idx, ch in enumerate(recommended_charts[:6]):
        c_type = ch.get("chart_type", "bar")
        x_col = ch.get("x")
        y_col = ch.get("y")
        agg = ch.get("agg", "sum")
        title = ch.get("title") or f"{c_type.title()} Analysis"
        palette = palettes[idx % len(palettes)]

        spec = {
            "chart_type": c_type,
            "x": x_col,
            "y": y_col,
            "agg": agg,
            "freq": "M",
            "top_n": 8 if c_type in ("bar", "hbar", "pie", "donut", "stacked_bar") else 0,
            "bins": 20,
            "title": title,
            "palette": palette,
            "show_values": True,
            "x_is_date": False,
            "width": 10.0,
            "height": 5.5,
        }
        chart_id = charts_mod.new_chart_id(title or c_type)
        out_file = chart_dir / f"{chart_id}.png"
        try:
            meta = charts_mod.render_chart(df, spec, out_file, dpi=Config.DEFAULT_DPI)
            item = {
                "id": chart_id,
                "title": meta.get("title", title),
                "image": out_file.name,
                "spec": spec,
                "on_dashboard": True,
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            charts_idx = [i for i in charts_idx if i["id"] != chart_id] + [item]
            created_charts.append(item)
        except Exception:
            continue

    if charts_idx:
        ws_mod.save_charts_index(ws(), charts_idx)

    # 4. Mount summary table if none exists
    tables_idx = ws_mod.tables_index(ws())
    has_dash_table = any(t.get("on_dashboard") for t in tables_idx)
    if not has_dash_table:
        cat_cols = [c["name"] for c in profile.get("columns", []) if c.get("kind") in ("categorical", "text") and 1 < c.get("unique", 0) <= 250]
        num_cols = df.select_dtypes(include=["number"]).columns.tolist()
        if cat_cols and num_cols:
            t_col = cat_cols[0]
            m_col = num_cols[0]
            try:
                table_res = insights.group_table(df, None, [t_col], [{"col": m_col, "agg": "sum"}], sort_by=f"{m_col}_sum", sort_desc=True)
                table_id = charts_mod.new_chart_id(f"Summary_{t_col}")
                table_item = {
                    "id": table_id,
                    "title": f"Summary Breakdown by {t_col.replace('_', ' ').title()}",
                    "group_cols": [t_col],
                    "metrics": [{"col": m_col, "agg": "sum"}],
                    "sort_by": f"{m_col}_sum",
                    "sort_desc": True,
                    "on_dashboard": True,
                    "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "snapshot": {"columns": table_res["columns"], "rows": table_res["rows"][:12]},
                }
                tables_idx = [t for t in tables_idx if t["id"] != table_id] + [table_item]
                ws_mod.save_tables_index(ws(), tables_idx)
            except Exception:
                pass

    # 5. Persist Executive KPIs and AI narrative in workspace state
    st["executive_kpis"] = kpis
    st["executive_narrative"] = {
        "summary": narrative_data.get("summary", ""),
        "growth_driver": narrative_data.get("growth_driver", ""),
        "watchpoint": narrative_data.get("watchpoint", ""),
    }
    ws_mod.save_state(ws(), st)

    return jsonify({
        "ok": True,
        "charts_created": len(created_charts),
        "kpis": kpis,
        "narrative": st["executive_narrative"],
    })


@app.post("/api/dashboard/export")
def api_dashboard_export():
    payload = request.get_json(silent=True) or {}
    fmt = (payload.get("fmt") or "pdf").lower()
    st = state()
    dataset_name = st.get("source_file") or "dataset"

    df, st, info = current_df()
    ckey = dataset_cache_key(st)
    profile = cached_profile(df, ckey)
    audit = cached_audit(df, ckey, dataset_name)
    kpis = st.get("executive_kpis") or insights.discover_executive_kpis(df, profile, audit)
    sentences = insights.build_sentences(df, profile)

    narrative = st.get("executive_narrative")
    if narrative:
        ai_findings = []
        if narrative.get("summary"):
            ai_findings.append({"text": f"**Executive Summary:** {narrative['summary']}"})
        if narrative.get("growth_driver"):
            ai_findings.append({"text": f"**Primary Growth Driver:** {narrative['growth_driver']}"})
        if narrative.get("watchpoint"):
            ai_findings.append({"text": f"**Operational Watchpoint:** {narrative['watchpoint']}"})
        sentences = ai_findings + sentences

    chart_items = [i for i in ws_mod.charts_index(ws()) if i.get("on_dashboard")]
    table_items = [i for i in ws_mod.tables_index(ws()) if i.get("on_dashboard")]

    if not chart_items:
        return jsonify({"ok": False, "error": "Add at least one chart to the dashboard first."}), 400

    chart_payload = [
        {"image": str(ws_mod.chart_dir(ws()) / item["image"]), "title": item["title"]}
        for item in chart_items
    ]
    table_payload = [
        {"title": item["title"], "columns": item["snapshot"]["columns"], "rows": item["snapshot"]["rows"]}
        for item in table_items
    ]

    info_payload = {
        "rows": int(len(df)),
        "cols": int(df.shape[1]),
        "source_file": dataset_name,
        "sheet": st.get("sheet") or ("cleaned data" if info.get("cleaned") else "-"),
    }

    exports = ws_mod.subdir(ws(), ws_mod.EXPORTS_DIR)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    base = Path(dataset_name).stem or "dashboard"

    try:
        if fmt == "pdf":
            out = exports / f"{base}-dashboard-{stamp}.pdf"
            reporter.build_dashboard_pdf(out, dataset_name, info_payload, kpis, sentences,
                                         chart_payload, table_payload)
        elif fmt == "html":
            out = exports / f"{base}-dashboard-{stamp}.html"
            reporter.build_dashboard_html(out, dataset_name, info_payload, kpis, sentences,
                                          chart_payload, table_payload)
        elif fmt == "png":
            out = exports / f"{base}-poster-{stamp}.png"
            reporter.build_dashboard_png(out, dataset_name, kpis, chart_payload)
        elif fmt == "excel":
            out = exports / f"{base}-analysis-{stamp}.xlsx"
            _build_excel_export(out, df, profile, kpis, sentences, table_items)
        else:
            return jsonify({"ok": False, "error": f"Unknown format '{fmt}'"}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": f"Export failed: {exc}"}), 500

    return jsonify({"ok": True, "file": out.name, "url": url_for("export_file", name=out.name)})


def _build_excel_export(out: Path, df: pd.DataFrame, profile: dict[str, Any],
                        kpis: list[dict[str, Any]], sentences: list[dict[str, str]],
                        table_items: list[dict[str, Any]]) -> None:
    with pd.ExcelWriter(out, engine="xlsxwriter") as writer:
        pd.DataFrame({
            "Metric": [k["label"] for k in kpis],
            "Value": [k["value"] for k in kpis],
            "Detail": [k["sub"] for k in kpis],
        }).to_excel(writer, sheet_name="KPIs", index=False)

        pd.DataFrame({
            "Finding": [str(s.get("text", "")).replace("**", "").replace("`", "") for s in sentences],
        }).to_excel(writer, sheet_name="Insights", index=False)

        stats = []
        for col in profile["columns"]:
            row = {
                "Column": col["name"], "Type": col["kind_label"], "Non-null": col["non_null"],
                "Missing": col["null_count"], "Missing %": col["null_pct"],
                "Unique": col["unique"],
            }
            stats_row = col.get("stats") or {}
            row.update({f"stat_{k}": v for k, v in stats_row.items()})
            stats.append(row)
        pd.DataFrame(stats).to_excel(writer, sheet_name="Columns", index=False)

        for item in table_items:
            name = str(item["title"])[:28].replace("/", "-").replace("\\", "-").replace(":", "") or "Table"
            pd.DataFrame(item["snapshot"]["rows"], columns=item["snapshot"]["columns"]).to_excel(
                writer, sheet_name=name, index=False)

        df.head(50_000).to_excel(writer, sheet_name="Cleaned Data", index=False)


@app.get("/export/<path:name>")
def export_file(name: str):
    exports = ws() / ws_mod.EXPORTS_DIR
    safe = Path(name).name
    if not (exports / safe).exists():
        abort(404)
    return send_from_directory(exports, safe, as_attachment=True)


@app.get("/download/cleaned")
def download_cleaned():
    fmt = (request.args.get("fmt") or "csv").lower()
    path, st = require_dataset()
    if path is None:
        abort(404)
    df, _st, _info = current_df()
    if fmt == "xlsx":
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="xlsxwriter") as writer:
            df.to_excel(writer, sheet_name="Cleaned", index=False)
        buffer.seek(0)
        return send_file(buffer, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                         as_attachment=True, download_name="cleaned_data.xlsx")
    data = df.to_csv(index=False).encode("utf-8-sig")
    return send_file(io.BytesIO(data), mimetype="text/csv", as_attachment=True,
                     download_name="cleaned_data.csv")


def get_openrouter_key() -> str | None:
    # 1. Local persistent config file
    key_file = Config.BASE_DIR / ".openrouter_key"
    if key_file.exists():
        try:
            k = key_file.read_text(encoding="utf-8").strip()
            if k:
                session["openrouter_key"] = k
                return k
        except Exception:
            pass
    # 2. Built-in App Config
    if getattr(Config, "OPENROUTER_API_KEY", None):
        k = str(Config.OPENROUTER_API_KEY).strip()
        if k:
            session["openrouter_key"] = k
            return k
    # 3. Environment variable
    import os
    env_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENROUTER_KEY")
    if env_key:
        k = env_key.strip()
        session["openrouter_key"] = k
        return k
    # 4. Session key fallback
    if session.get("openrouter_key"):
        return session["openrouter_key"]
    return None


@app.route("/reset", methods=["GET", "POST"])
def reset():
    sid = get_sid()
    ws_mod.delete_workspace(Config.WORKSPACE_DIR, sid)
    _PROFILE_CACHE.pop(sid, None)
    # preserve the API key!
    saved_key = session.get("openrouter_key")
    session.clear()
    if saved_key:
        session["openrouter_key"] = saved_key
    flash("Data cleared. You can now upload a new file or folder to start new insights.", "success")
    return redirect(url_for("index", new="1"))


@app.route("/chat")
def chat_page():
    path, st = require_dataset()
    has_dataset = path is not None

    dataset_summary = None
    suggestions = []
    info = {}

    if has_dataset:
        try:
            df, st, info = current_df()
            profile = cached_profile(df, dataset_cache_key(st))

            num_cols = [c["name"] for c in profile["columns"] if c["kind"] == profiler.KIND_NUMERIC]
            cat_cols = [c["name"] for c in profile["columns"] if c["kind"] in (profiler.KIND_CATEGORICAL, profiler.KIND_TEXT, profiler.KIND_BOOLEAN)]
            date_cols = [c["name"] for c in profile["columns"] if c["kind"] == profiler.KIND_DATETIME]

            suggestions = [
                "Summarize the key business findings, trends, and patterns in this data.",
            ]
            if cat_cols and num_cols:
                suggestions.append(f"Which {cat_cols[0]} generates the highest total {num_cols[0]}?")
            if len(cat_cols) > 1 and num_cols:
                suggestions.append(f"Compare {num_cols[0]} across different {cat_cols[1]} categories.")
            elif len(num_cols) >= 2:
                suggestions.append(f"What is the correlation or relationship between {num_cols[0]} and {num_cols[1]}?")
            if date_cols and num_cols:
                suggestions.append(f"How did {num_cols[0]} evolve over time by {date_cols[0]}?")
            suggestions.append("Are there any anomalies, outliers, or suspicious data points?")
            suggestions.append("Which 3 charts would give executive leaders the fastest actionable insights?")

            dataset_summary = {
                "name": st.get("source_file") or "dataset",
                "sheet": st.get("sheet"),
                "rows": len(df),
                "cols": len(df.columns),
                "is_cleaned": ws_mod.has_cleaned(ws()) and st.get("use_cleaned", True),
                "num_count": len(num_cols),
                "cat_count": len(cat_cols),
                "columns": [c["name"] for c in profile["columns"] if c["kind"] != profiler.KIND_EMPTY],
            }
        except Exception:
            has_dataset = False

    if not has_dataset:
        suggestions = [
            "What file formats can Insight Studio analyze?",
            "How does automated data cleaning work?",
            "What kind of charts and reports can I generate?",
            "Can I upload multiple Excel sheets or folders?",
        ]
        dataset_summary = {
            "name": "No dataset loaded yet",
            "sheet": None,
            "rows": 0,
            "cols": 0,
            "is_cleaned": False,
            "num_count": 0,
            "cat_count": 0,
            "columns": [],
        }

    return render_template(
        "chat.html",
        has_dataset=has_dataset,
        dataset=dataset_summary,
        suggestions=suggestions[:6],
        has_key=bool(get_openrouter_key()),
        model_name="Studio AI Assistant",
        info=info,
        allowed=sorted(Config.ALLOWED_EXTENSIONS),
        max_mb=Config.MAX_CONTENT_LENGTH // (1024 * 1024),
        has_sample=Config.SAMPLE_FILE.exists(),
        **nav_context(),
    )


# --------------------------------------------------------------------------- #
# AI chat + settings
# --------------------------------------------------------------------------- #
@app.post("/api/settings/key")
def api_save_key():
    payload = request.get_json(silent=True) or {}
    key = (payload.get("api_key") or "").strip()
    if not key:
        return jsonify({"ok": False, "error": "API key is empty."}), 400

    result = llm_mod.validate_api_key(key)
    if not result["ok"]:
        return jsonify(result), 400

    session["openrouter_key"] = key
    session.permanent = True
    Config.OPENROUTER_API_KEY = key
    try:
        (Config.BASE_DIR / ".openrouter_key").write_text(key, encoding="utf-8")
    except Exception:
        pass
    return jsonify({"ok": True, "message": "API key saved and validated successfully."})


@app.get("/api/settings/key")
def api_get_key_status():
    key = get_openrouter_key()
    return jsonify({"ok": True, "has_key": bool(key)})


@app.post("/api/chat")
def api_chat():
    payload = request.get_json(silent=True) or {}
    question = (payload.get("question") or "").strip()
    if not question:
        return jsonify({"ok": False, "error": "Please type a question."}), 400

    api_key = get_openrouter_key()
    if not api_key:
        return jsonify({"ok": False, "error": "AI service error: Backend API key is not configured."}), 400

    history = payload.get("history") or []
    try:
        df, _st, _info = current_df()
        result = llm_mod.chat_with_data(
            api_key, df, question,
            history=history,
            model=Config.OPENROUTER_MODEL,
        )
    except FileNotFoundError:
        result = llm_mod.chat_without_data(
            api_key, question,
            history=history,
            model=Config.OPENROUTER_MODEL,
        )
    return jsonify(result)


@app.post("/api/ai/suggest-charts")
def api_ai_suggest_charts():
    api_key = get_openrouter_key()
    if not api_key:
        return jsonify({"ok": False, "error": "AI service error: Backend API key is not configured."}), 400
    try:
        df, _st, _info = current_df()
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "No dataset."}), 400

    result = llm_mod.suggest_charts_ai(api_key, df, model=Config.OPENROUTER_MODEL)
    return jsonify(result)


# --------------------------------------------------------------------------- #
# health + errors
# --------------------------------------------------------------------------- #
@app.get("/healthz")
def healthz():
    return jsonify({"ok": True, "app": Config.APP_NAME, "time": time.time()})


@app.errorhandler(413)
def too_large(_e):
    flash(f"File too large. Limit is {Config.MAX_CONTENT_LENGTH // (1024 * 1024)} MB.", "error")
    return redirect(url_for("index")), 413


@app.errorhandler(404)
def not_found(_e):
    return render_template("error.html", code=404, message="Page not found", **nav_context()), 404


@app.errorhandler(500)
def server_error(_e):
    return render_template("error.html", code=500, message="Something went wrong", **nav_context()), 500


if __name__ == "__main__":
    ws_mod.cleanup_old_workspaces(Config.WORKSPACE_DIR, Config.WORKSPACE_TTL_HOURS)
    print(f"\n  {Config.APP_NAME} running at http://{Config.HOST}:{Config.PORT}\n")
    app.run(host=Config.HOST, port=Config.PORT, debug=Config.DEBUG)
