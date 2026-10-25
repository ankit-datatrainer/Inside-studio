"""OpenRouter LLM integration for Insight Studio.

Sends data context + user question to the OpenRouter API and streams
back an answer.  The free NVIDIA Nemotron model is the default.

Usage:
    answer = chat_with_data(api_key, df, question)
"""
from __future__ import annotations

import json
import time
from typing import Any

import httpx
import pandas as pd

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "nvidia/nemotron-3.5-super-vl:free"
TIMEOUT = 60  # seconds


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _send_completion_request(
    payload: dict[str, Any],
    api_key: str,
    max_retries: int = 1,
) -> tuple[int, dict[str, Any], str]:
    headers = {
        "Authorization": f"Bearer {api_key.strip()}",
        "Content-Type": "application/json",
        "HTTP-Referer": "http://localhost:5000",
        "X-Title": "Insight Studio",
    }
    last_err = ""
    for attempt in range(max_retries + 1):
        try:
            resp = httpx.post(OPENROUTER_API_URL, headers=headers, json=payload, timeout=TIMEOUT)
            if resp.status_code == 200:
                return 200, resp.json(), ""
            body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            err_msg = body.get("error", {}).get("message", f"API error {resp.status_code}")
            last_err = err_msg
            if attempt < max_retries and (resp.status_code in (429, 500, 502, 503) or "Provider returned error" in err_msg):
                time.sleep(1.2)
                continue
            return resp.status_code, body, err_msg
        except (httpx.TimeoutException, httpx.NetworkError):
            if attempt < max_retries:
                time.sleep(1.0)
                continue
            return 504, {}, "Request timed out. The model may be under heavy load — please try again."
        except Exception as exc:
            return 500, {}, f"Connection error: {exc}"
    return 500, {}, last_err or "Unknown connection error"


def _build_data_context(df: pd.DataFrame, max_rows: int = 30) -> str:
    """Build a compact text summary of the DataFrame for the LLM prompt."""
    lines: list[str] = []

    # shape
    lines.append(f"Dataset: {df.shape[0]:,} rows × {df.shape[1]} columns")

    # column info
    lines.append("\n## Columns")
    for col in df.columns:
        dtype = str(df[col].dtype)
        nulls = int(df[col].isna().sum())
        uniq = int(df[col].nunique())
        lines.append(f"- **{col}** (dtype={dtype}, nulls={nulls}, unique={uniq})")

    # describe
    desc = df.describe(include="all").round(2)
    lines.append("\n## Statistical summary (describe)")
    lines.append(desc.to_string())

    # sample rows
    sample = df.head(min(max_rows, len(df)))
    lines.append(f"\n## First {len(sample)} rows (sample)")
    lines.append(sample.to_string(index=False, max_colwidth=60))

    # value counts for low-cardinality columns
    for col in df.select_dtypes(include=["object", "category", "bool"]).columns:
        if df[col].nunique() <= 15:
            vc = df[col].value_counts().head(10)
            lines.append(f"\n## Value counts for '{col}'")
            for val, cnt in vc.items():
                lines.append(f"  {val}: {cnt}")

    return "\n".join(lines)


def validate_api_key(api_key: str) -> dict[str, Any]:
    """Quick validation — try a tiny completion to check the key works."""
    if not api_key or len(api_key.strip()) < 10:
        return {"ok": False, "error": "API key is too short or empty."}

    try:
        resp = httpx.post(
            OPENROUTER_API_URL,
            headers={
                "Authorization": f"Bearer {api_key.strip()}",
                "Content-Type": "application/json",
                "HTTP-Referer": "http://localhost:5000",
                "X-Title": "Insight Studio",
            },
            json={
                "model": DEFAULT_MODEL,
                "messages": [{"role": "user", "content": "Say OK"}],
                "max_tokens": 5,
            },
            timeout=15,
        )
        if resp.status_code == 401:
            return {"ok": False, "error": "Invalid API key — authentication failed."}
        if resp.status_code == 429:
            return {"ok": False, "error": "Rate limited. Try again in a moment."}
        if resp.status_code >= 400:
            body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            return {"ok": False, "error": body.get("error", {}).get("message", f"API error {resp.status_code}")}
        return {"ok": True}
    except httpx.TimeoutException:
        return {"ok": False, "error": "AI service timed out. Please try again."}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"Connection error: {exc}"}


def chat_with_data(
    api_key: str,
    df: pd.DataFrame,
    question: str,
    *,
    history: list[dict[str, str]] | None = None,
    model: str = DEFAULT_MODEL,
) -> dict[str, Any]:
    """Send a data-aware question to the LLM and return the answer.

    Returns: {"ok": True, "answer": "..."} or {"ok": False, "error": "..."}
    """
    if not api_key:
        return {"ok": False, "error": "AI service error: Backend API key is not configured."}

    context = _build_data_context(df)

    system_prompt = (
        "You are **Insight Studio AI**, an expert data analyst assistant. "
        "The user has uploaded a dataset and you have full access to its schema, "
        "statistics, and sample rows below.\n\n"
        "When answering:\n"
        "- Be specific — reference actual column names, values, and numbers from the data.\n"
        "- Use clear, concise language.\n"
        "- If asked for charts or recommendations, suggest exact column combinations.\n"
        "- Format numbers nicely (commas, rounding).\n"
        "- Use markdown for formatting (bold, lists, code blocks).\n"
        "- Never mention external platform names like OpenRouter or underlying model identifiers. Identify strictly as Insight Studio AI.\n\n"
        f"## DATASET CONTEXT\n{context}"
    )

    messages = [{"role": "system", "content": system_prompt}]

    # append conversation history
    if history:
        for msg in history[-8:]:  # keep last 8 turns to stay within context
            messages.append({"role": msg.get("role", "user"), "content": msg.get("content", "")})

    messages.append({"role": "user", "content": question})

    status_code, data, err_msg = _send_completion_request(
        {
            "model": model,
            "messages": messages,
            "max_tokens": 4096,
            "temperature": 0.3,
        },
        api_key,
    )

    if status_code != 200:
        return {"ok": False, "error": err_msg or "AI service error"}

    choice = data.get("choices", [{}])[0]
    msg = choice.get("message", {})
    answer = (msg.get("content") or "").strip()
    if not answer:
        answer = (msg.get("reasoning") or "").strip()

    if not answer:
        return {"ok": False, "error": "The model returned an empty response."}

    return {
        "ok": True,
        "answer": answer,
        "model": "Studio AI Assistant",
        "usage": data.get("usage", {}),
    }


def chat_without_data(
    api_key: str,
    question: str,
    *,
    history: list[dict[str, str]] | None = None,
    model: str = DEFAULT_MODEL,
) -> dict[str, Any]:
    """Answer questions about data analysis, supported file formats, and Insight Studio capabilities."""
    if not api_key:
        return {"ok": False, "error": "AI service error: Backend API key is not configured."}

    system_prompt = (
        "You are **Insight Studio AI**, an elite data analysis partner. "
        "The user is currently in the upload area or hasn't loaded a dataset yet.\n\n"
        "Insight Studio Capabilities:\n"
        "- **Supported formats**: Single or multiple Excel (.xlsx, .xls, .xlsm), CSV, TSV, TXT, and PDF table files.\n"
        "- **Folder upload**: Users can select an entire directory and all compatible files are merged seamlessly.\n"
        "- **Automated Data Cleaning**: Fixes missing values, removes duplicates, handles anomalies, standardizes date formats, and converts text-numbers.\n"
        "- **Automated Visualizations**: Instantly generates Bar, Line, Pie, Donut, Scatter, Heatmap, Histogram, Box, and Area charts with zero manual work.\n"
        "- **Executive Reporting**: One-click export to PDF report, standalone interactive HTML dashboard, PNG poster, or cleaned Excel workbook.\n\n"
        "Provide direct, inspiring, concise answers in markdown formatting. When relevant, invite the user to drop their file into the upload area or try the sample sales data. "
        "Never mention external platform names like OpenRouter or underlying model identifiers. Identify strictly as Insight Studio AI."
    )

    messages = [{"role": "system", "content": system_prompt}]
    if history:
        for msg in history[-8:]:
            messages.append({"role": msg.get("role", "user"), "content": msg.get("content", "")})
    messages.append({"role": "user", "content": question})

    status_code, data, err_msg = _send_completion_request(
        {
            "model": model,
            "messages": messages,
            "max_tokens": 4096,
            "temperature": 0.3,
        },
        api_key,
    )

    if status_code != 200:
        return {"ok": False, "error": err_msg or "AI service error"}

    choice = data.get("choices", [{}])[0]
    msg = choice.get("message", {})
    answer = (msg.get("content") or "").strip()
    if not answer:
        answer = (msg.get("reasoning") or "").strip()

    if not answer:
        return {"ok": False, "error": "The model returned an empty response."}

    return {
        "ok": True,
        "answer": answer,
        "model": "Studio AI Assistant",
        "usage": data.get("usage", {}),
    }



def _generate_smart_fallback_charts(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Generate 5 intelligent, data-driven chart ideas when LLM response is unavailable."""
    num_cols = df.select_dtypes(include=["number"]).columns.tolist()
    cat_cols = df.select_dtypes(include=["object", "category", "string", "bool"]).columns.tolist()
    date_cols = [c for c in df.columns if any(k in c.lower() for k in ["date", "time", "day", "month", "year"])]

    suggestions: list[dict[str, Any]] = []

    # 1. Bar Chart: Primary Category vs Primary Metric
    if cat_cols and num_cols:
        cat = cat_cols[0]
        num = num_cols[0]
        suggestions.append({
            "chart_type": "bar",
            "x": cat,
            "y": num,
            "agg": "sum",
            "title": f"Total {num} by {cat}",
            "reason": f"Reveals which {cat} contributes the highest total {num}.",
        })

    # 2. Donut/Pie Chart: Proportion Breakdown
    if len(cat_cols) > 0 and num_cols:
        cat = cat_cols[0] if len(cat_cols) == 1 else cat_cols[1]
        num = num_cols[0]
        suggestions.append({
            "chart_type": "donut",
            "x": cat,
            "y": num,
            "agg": "sum",
            "title": f"Share of {num} across {cat}",
            "reason": f"Visualizes the percentage share and concentration of {num} among {cat} groups.",
        })

    # 3. Time Series or Trend Line
    if date_cols and num_cols:
        d_col = date_cols[0]
        num = num_cols[0]
        suggestions.append({
            "chart_type": "line",
            "x": d_col,
            "y": num,
            "agg": "sum",
            "title": f"{num} Trend over {d_col}",
            "reason": f"Tracks growth trajectory, seasonality, and periodic peaks in {num}.",
        })
    elif len(num_cols) >= 2:
        suggestions.append({
            "chart_type": "scatter",
            "x": num_cols[0],
            "y": num_cols[1],
            "agg": "sum",
            "title": f"{num_cols[1]} vs {num_cols[0]} Relationship",
            "reason": f"Identifies correlation, clusters, and unusual outliers between {num_cols[0]} and {num_cols[1]}.",
        })

    # 4. Distribution Histogram
    if num_cols:
        num = num_cols[1] if len(num_cols) > 1 else num_cols[0]
        suggestions.append({
            "chart_type": "histogram",
            "x": num,
            "y": None,
            "agg": "count",
            "title": f"Distribution Profile of {num}",
            "reason": f"Examines frequency spread, skewness, and common ranges for {num}.",
        })

    # 5. Correlation or Multi-metric comparison
    if len(num_cols) >= 2:
        suggestions.append({
            "chart_type": "heatmap",
            "x": num_cols[0],
            "y": num_cols[1],
            "agg": "mean",
            "title": f"Correlation Matrix across Key Metrics",
            "reason": "Exposes cross-variable dependencies and covariance across numerical dimensions.",
        })
    elif len(cat_cols) >= 2 and num_cols:
        suggestions.append({
            "chart_type": "stacked_bar",
            "x": cat_cols[0],
            "y": num_cols[0],
            "agg": "sum",
            "title": f"{num_cols[0]} Breakdown across {cat_cols[0]} and {cat_cols[1]}",
            "reason": f"Provides a granular multi-tier breakdown comparing segments side-by-side.",
        })

    # Ensure at least 3 suggestions
    if not suggestions and len(df.columns) > 0:
        c1 = df.columns[0]
        suggestions.append({
            "chart_type": "bar",
            "x": c1,
            "y": None,
            "agg": "count",
            "title": f"Record Counts by {c1}",
            "reason": "Baseline frequency count of records across data groups.",
        })

    return suggestions[:5]


def suggest_charts_ai(
    api_key: str,
    df: pd.DataFrame,
    *,
    model: str = DEFAULT_MODEL,
) -> dict[str, Any]:
    """Ask the LLM to suggest chart ideas based on the data, with automatic resilient fallback."""
    if not api_key:
        return {"ok": True, "suggestions": _generate_smart_fallback_charts(df), "source": "smart_local"}

    context = _build_data_context(df, max_rows=15)
    valid_types = {"bar", "line", "pie", "donut", "scatter", "histogram", "box", "area", "heatmap", "stacked_bar"}
    valid_aggs = {"sum", "mean", "count", "median", "max", "min"}

    prompt = (
        "Based on the dataset below, suggest exactly 5 insightful charts that would best "
        "visualize the key patterns and stories in this data.\n\n"
        "For each chart, provide:\n"
        "1. chart_type (one of: bar, line, pie, donut, scatter, heatmap, histogram, box, area, stacked_bar)\n"
        "2. x (column name for X axis)\n"
        "3. y (column name for Y axis, or null for histogram/count)\n"
        "4. agg (one of: sum, mean, count, median, max, min)\n"
        "5. title (short descriptive title)\n"
        "6. reason (one concise sentence explaining why this chart is insightful)\n\n"
        "Return ONLY a valid JSON array of objects. Do NOT include markdown code blocks, do NOT include explanations.\n\n"
        f"## DATASET\n{context}"
    )

    try:
        resp = httpx.post(
            OPENROUTER_API_URL,
            headers={
                "Authorization": f"Bearer {api_key.strip()}",
                "Content-Type": "application/json",
                "HTTP-Referer": "http://localhost:5000",
                "X-Title": "Insight Studio",
            },
            json={
                "model": model,
                "messages": [
                    {
                        "role": "system",
                        "content": "You are an expert data visualization consultant. You output ONLY valid JSON arrays without markdown fencing or preamble.",
                    },
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 4096,
                "temperature": 0.2,
            },
            timeout=TIMEOUT,
        )

        if resp.status_code >= 400:
            return {"ok": True, "suggestions": _generate_smart_fallback_charts(df), "source": "smart_fallback"}

        data = resp.json()
        choice = data.get("choices", [{}])[0]
        msg = choice.get("message", {})
        answer = (msg.get("content") or "").strip()
        if not answer:
            answer = (msg.get("reasoning") or "").strip()

        # Robust JSON extraction
        if "```" in answer:
            import re
            m = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", answer, re.DOTALL)
            if m:
                answer = m.group(1).strip()

        start_idx = answer.find("[")
        end_idx = answer.rfind("]")
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            answer = answer[start_idx : end_idx + 1]

        suggestions = json.loads(answer)
        if not isinstance(suggestions, list):
            suggestions = [suggestions]

        # Sanitize and validate suggestions against actual columns
        cleaned_suggestions = []
        df_cols = list(df.columns)
        for s in suggestions:
            if not isinstance(s, dict):
                continue
            ctype = str(s.get("chart_type", "bar")).lower().replace("-", "_")
            if ctype not in valid_types:
                ctype = "bar"
            
            x_col = str(s.get("x") or "")
            if x_col not in df_cols:
                x_col = df_cols[0] if df_cols else "Index"
            
            y_col = s.get("y")
            if y_col and str(y_col) not in df_cols:
                num_cols = df.select_dtypes(include=["number"]).columns.tolist()
                y_col = num_cols[0] if num_cols else None
            
            agg = str(s.get("agg", "sum")).lower()
            if agg not in valid_aggs:
                agg = "sum" if y_col else "count"
            
            title = str(s.get("title") or f"{ctype.title()} Chart of {x_col}")
            reason = str(s.get("reason") or "Discovered by AI model based on column distribution.")
            
            cleaned_suggestions.append({
                "chart_type": ctype,
                "x": x_col,
                "y": y_col,
                "agg": agg,
                "title": title,
                "reason": reason,
            })

        if not cleaned_suggestions:
            cleaned_suggestions = _generate_smart_fallback_charts(df)

        return {"ok": True, "suggestions": cleaned_suggestions, "source": "ai"}

    except Exception:  # noqa: BLE001
        # Always return smart fallback on any parsing or network error
        return {"ok": True, "suggestions": _generate_smart_fallback_charts(df), "source": "smart_fallback"}


def _generate_smart_dashboard_fallback(
    df: pd.DataFrame,
    profile: dict[str, Any],
    kpis: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Deterministic, publication-grade executive narrative and charts from dataframe statistics."""
    ov = profile.get("overview", {})
    charts = _generate_smart_fallback_charts(df)

    # Enhance to ensure at least 4 complementary charts
    num_cols = df.select_dtypes(include=["number"]).columns.tolist()
    cat_cols = [c for c in df.select_dtypes(include=["object", "category"]).columns if 1 < df[c].nunique() <= 100]
    date_cols = df.select_dtypes(include=["datetime", "datetimetz"]).columns.tolist()

    existing_types = {c["chart_type"] for c in charts}
    if "donut" not in existing_types and "pie" not in existing_types and cat_cols and num_cols:
        charts.append({
            "chart_type": "donut",
            "x": cat_cols[0],
            "y": num_cols[0],
            "agg": "sum",
            "title": f"Market Composition by {cat_cols[0]}",
            "reason": f"Visualizes proportional volume distribution across {cat_cols[0]}.",
        })

    # Find dominant segment
    top_cat = cat_cols[0] if cat_cols else "Primary Segment"
    top_metric = num_cols[0] if num_cols else "Total Value"
    leader_name = "Core Products"
    share_text = "significant"
    if cat_cols and num_cols:
        try:
            grouped = df.groupby(cat_cols[0])[num_cols[0]].sum().sort_values(ascending=False)
            if not grouped.empty:
                leader_name = str(grouped.index[0])
                pct = (grouped.iloc[0] / grouped.sum() * 100) if grouped.sum() > 0 else 0
                share_text = f"{pct:.1f}%"
        except Exception:
            pass

    null_count = int(df.isna().sum().sum())
    summary = f"Dataset encompasses {len(df):,} records and {len(df.columns)} dimensions. Aggregate activity demonstrates stable operating performance with strong concentration in {top_cat}."
    growth_driver = f"Segment '{leader_name}' represents the principal performance catalyst ({share_text} of {top_metric}), driving overall volume and growth."
    watchpoint = f"Found {null_count:,} missing values across columns requiring validation. Segment variances should be actively monitored." if null_count > 0 else "Metrics exhibit consistent data integrity with zero critical missing values across core reporting dimensions."

    return {
        "summary": summary,
        "growth_driver": growth_driver,
        "watchpoint": watchpoint,
        "recommended_charts": charts[:6],
    }


def generate_dashboard_narrative_ai(
    api_key: str,
    df: pd.DataFrame,
    profile: dict[str, Any],
    kpis: list[dict[str, Any]] | None = None,
    *,
    model: str = DEFAULT_MODEL,
) -> dict[str, Any]:
    """Generate executive AI narrative and 4-6 publication-grade chart blueprints with strict whitelabeling."""
    fallback = _generate_smart_dashboard_fallback(df, profile, kpis)
    if not api_key:
        return fallback

    context = _build_data_context(df, max_rows=15)
    valid_types = {"bar", "hbar", "line", "pie", "donut", "scatter", "histogram", "box", "area", "heatmap", "stacked_bar"}
    valid_aggs = {"sum", "mean", "count", "median", "max", "min"}

    kpi_summary = ""
    if kpis:
        kpi_summary = "\n".join(f"- {k.get('label')}: {k.get('value')} ({k.get('sub')})" for k in kpis)

    prompt = (
        "You are an executive data analyst consulting for C-suite decision makers. Analyze the dataset schema, sample rows, and executive KPIs below.\n\n"
        "Generate a publication-grade executive narrative and exactly 4 to 6 complementary charts representing the most meaningful business stories.\n\n"
        "CONFIDENTIALITY AND BRANDING MANDATE:\n"
        "- Strictly NEVER mention OpenRouter, NVIDIA, Nemotron, or model architectures in your text.\n"
        "- Write with the voice of an elite corporate intelligence consultant.\n\n"
        "Return ONLY a valid JSON object in this exact structure:\n"
        "{\n"
        '  "summary": "1-2 sentences summarizing dataset volume, scope, and key overarching pattern.",\n'
        '  "growth_driver": "1-2 sentences highlighting the single most significant category, product, or contributor leading metrics.",\n'
        '  "watchpoint": "1-2 sentences pinpointing an operational watchpoint, anomaly, outlier concentration, or data variance.",\n'
        '  "recommended_charts": [\n'
        '    {\n'
        '      "chart_type": "bar|hbar|line|pie|donut|scatter|histogram|box|area|stacked_bar",\n'
        '      "x": "Exact column name from dataset",\n'
        '      "y": "Exact numeric column name or null",\n'
        '      "agg": "sum|mean|count|median|max|min",\n'
        '      "title": "Clean concise chart title",\n'
        '      "reason": "Why this chart delivers immediate executive insight"\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        f"## EXECUTIVE KPIS\n{kpi_summary}\n\n"
        f"## DATASET CONTEXT\n{context}"
    )

    try:
        resp = httpx.post(
            OPENROUTER_API_URL,
            headers={
                "Authorization": f"Bearer {api_key.strip()}",
                "Content-Type": "application/json",
                "HTTP-Referer": "http://localhost:5000",
                "X-Title": "Insight Studio",
            },
            json={
                "model": model,
                "messages": [
                    {
                        "role": "system",
                        "content": "You are an elite corporate intelligence data analyst. You output ONLY valid JSON without markdown formatting or introductory text.",
                    },
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 4096,
                "temperature": 0.2,
            },
            timeout=TIMEOUT,
        )

        if resp.status_code >= 400:
            return fallback

        data = resp.json()
        choice = data.get("choices", [{}])[0]
        msg = choice.get("message", {})
        answer = (msg.get("content") or "").strip()
        if not answer:
            answer = (msg.get("reasoning") or "").strip()

        # JSON extraction
        if "```" in answer:
            import re
            m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", answer, re.DOTALL)
            if m:
                answer = m.group(1).strip()

        start_idx = answer.find("{")
        end_idx = answer.rfind("}")
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            answer = answer[start_idx : end_idx + 1]

        parsed = json.loads(answer)
        if not isinstance(parsed, dict):
            return fallback

        # Clean and validate charts
        df_cols = list(df.columns)
        num_cols = df.select_dtypes(include=["number"]).columns.tolist()
        cleaned_charts = []

        for ch in parsed.get("recommended_charts", []):
            if not isinstance(ch, dict):
                continue
            ctype = str(ch.get("chart_type", "bar")).lower().replace("-", "_")
            if ctype not in valid_types:
                ctype = "bar"

            x_col = str(ch.get("x") or "")
            if x_col not in df_cols:
                x_col = df_cols[0] if df_cols else "Index"

            y_col = ch.get("y")
            if y_col and str(y_col) not in df_cols:
                y_col = num_cols[0] if num_cols else None

            agg = str(ch.get("agg", "sum")).lower()
            if agg not in valid_aggs:
                agg = "sum" if y_col else "count"

            title = str(ch.get("title") or f"{ctype.title()} Analysis")
            reason = str(ch.get("reason") or "Strategic metric comparison.")

            cleaned_charts.append({
                "chart_type": ctype,
                "x": x_col,
                "y": y_col,
                "agg": agg,
                "title": title,
                "reason": reason,
            })

        if not cleaned_charts:
            cleaned_charts = fallback["recommended_charts"]

        return {
            "summary": str(parsed.get("summary") or fallback["summary"]),
            "growth_driver": str(parsed.get("growth_driver") or fallback["growth_driver"]),
            "watchpoint": str(parsed.get("watchpoint") or fallback["watchpoint"]),
            "recommended_charts": cleaned_charts[:6],
        }

    except Exception:  # noqa: BLE001
        return fallback

