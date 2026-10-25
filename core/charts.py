"""Matplotlib chart factory + multi-format export.

The chart builder on the Insights screen sends a *spec* (JSON) here and gets
a PNG back. The exact same spec can be re-rendered into:

    PNG  JPG  SVG  PDF  WEBP  TIFF

Chart types: bar, hbar, line, area, stacked_bar, pie, donut, scatter,
hist, box, heatmap, heatmap_counts, count.
"""
from __future__ import annotations

import io
import re
import uuid
from typing import Any

import matplotlib

matplotlib.use("Agg")  # headless: works on servers / Docker

import matplotlib.pyplot as plt
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter
import numpy as np
import pandas as pd

PALETTES: dict[str, list[str]] = {
    "studio": ["#0066FF", "#23C0FF", "#0F172A", "#059669", "#D97706", "#6366F1", "#0284C7", "#F97316"],
    "ocean": ["#0C4A6E", "#0369A1", "#0284C7", "#0EA5E9", "#38BDF8", "#7DD3FC", "#BAE6FD"],
    "sunset": ["#7C2D12", "#C2410C", "#EA580C", "#F97316", "#FB923C", "#FDBA74", "#FED7AA"],
    "forest": ["#14532D", "#166534", "#15803D", "#16A34A", "#22C55E", "#4ADE80", "#86EFAC"],
    "berry": ["#4A044E", "#86198F", "#C026D3", "#E879F9", "#A21CAF", "#701A75", "#D946EF"],
    "mono": ["#111827", "#374151", "#6B7280", "#9CA3AF", "#D1D5DB", "#4B5563", "#1F2937"],
}

CHART_TYPES = [
    {"id": "bar", "label": "Bar", "needs": "xy"},
    {"id": "hbar", "label": "Bar (horizontal)", "needs": "xy"},
    {"id": "line", "label": "Line", "needs": "xy"},
    {"id": "area", "label": "Area", "needs": "xy"},
    {"id": "stacked_bar", "label": "Stacked bar", "needs": "xyg"},
    {"id": "pie", "label": "Pie", "needs": "xy"},
    {"id": "donut", "label": "Donut", "needs": "xy"},
    {"id": "scatter", "label": "Scatter", "needs": "xy"},
    {"id": "hist", "label": "Histogram", "needs": "x"},
    {"id": "box", "label": "Box plot", "needs": "xy"},
    {"id": "count", "label": "Count of rows", "needs": "x"},
    {"id": "heatmap", "label": "Correlation heatmap", "needs": "none"},
    {"id": "heatmap_counts", "label": "Two-way counts", "needs": "xy"},
]

AGGREGATIONS = ["sum", "mean", "median", "min", "max", "count", "nunique", "std"]
FREQUENCIES = [
    {"id": "D", "label": "Daily"},
    {"id": "W", "label": "Weekly"},
    {"id": "M", "label": "Monthly"},
    {"id": "Q", "label": "Quarterly"},
    {"id": "Y", "label": "Yearly"},
]
EXPORT_FORMATS = [
    {"id": "png", "label": "PNG", "mime": "image/png"},
    {"id": "jpg", "label": "JPG", "mime": "image/jpeg"},
    {"id": "svg", "label": "SVG (vector)", "mime": "image/svg+xml"},
    {"id": "pdf", "label": "PDF (vector)", "mime": "application/pdf"},
    {"id": "webp", "label": "WEBP", "mime": "image/webp"},
    {"id": "tiff", "label": "TIFF", "mime": "image/tiff"},
]


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def new_chart_id(title: str = "chart") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(title).lower()).strip("-")[:40] or "chart"
    return f"{slug}-{uuid.uuid4().hex[:6]}"


def _compact(value: float, _pos: Any = None) -> str:
    try:
        absv = abs(value)
        if absv >= 1_000_000_000:
            return f"{value / 1_000_000_000:.1f}B"
        if absv >= 1_000_000:
            return f"{value / 1_000_000:.1f}M"
        if absv >= 10_000:
            return f"{value / 1_000:.0f}k"
        if absv >= 1_000:
            return f"{value:,.0f}"
        if float(value).is_integer():
            return f"{value:.0f}"
        return f"{value:,.2f}"
    except Exception:
        return str(value)


def _agg_series(series: pd.Series, agg: str) -> float:
    if agg == "count":
        return float(series.size)
    if agg == "nunique":
        return float(series.nunique())
    numeric = pd.to_numeric(series, errors="coerce")
    if agg == "sum":
        return float(numeric.sum())
    if agg == "mean":
        return float(numeric.mean())
    if agg == "median":
        return float(numeric.median())
    if agg == "min":
        return float(numeric.min())
    if agg == "max":
        return float(numeric.max())
    if agg == "std":
        return float(numeric.std())
    return float(numeric.sum())


def _period_label(values: pd.Series, freq: str) -> pd.Series:
    dates = pd.to_datetime(values, errors="coerce", format="mixed")
    freq = (freq or "M").upper()
    if freq == "D":
        return dates.dt.strftime("%Y-%m-%d")
    if freq == "W":
        return dates.dt.strftime("%Y-W%V")
    if freq == "M":
        return dates.dt.strftime("%Y-%m")
    if freq == "Q":
        return dates.dt.to_period("Q").astype(str)
    if freq == "Y":
        return dates.dt.strftime("%Y")
    return dates.dt.strftime("%Y-%m")


def _style_axes(ax: plt.Axes, spec: dict[str, Any]) -> None:
    ax.set_facecolor("#FFFFFF")
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color("#D1D5DB")
    ax.tick_params(colors="#4B5563", labelsize=9)
    ax.yaxis.set_major_formatter(FuncFormatter(_compact))
    if spec.get("grid", True):
        ax.grid(axis="y", color="#E5E7EB", linewidth=0.8, alpha=0.9)
        ax.set_axisbelow(True)
    if spec.get("rotate_labels", True):
        labels = ax.get_xticklabels()
        if labels and max((len(t.get_text()) for t in labels), default=0) > 8:
            plt.setp(labels, rotation=30, ha="right")


def _title_for(spec: dict[str, Any]) -> str:
    if spec.get("title"):
        return str(spec["title"])
    x, y, agg = spec.get("x"), spec.get("y"), spec.get("agg", "sum")
    ctype = spec.get("chart_type", "bar")
    if ctype == "count":
        return f"Rows per {x}"
    if ctype == "hist":
        return f"Distribution of {x}"
    if ctype == "heatmap":
        return "Correlation heatmap"
    if ctype == "heatmap_counts":
        return f"{spec.get('y')} vs {spec.get('x')} (row counts)"
    if ctype == "scatter":
        return f"{y} vs {x}"
    if y:
        return f"{str(agg).title()} of {y} by {x}"
    return f"{x}"


# --------------------------------------------------------------------------- #
# figure builder
# --------------------------------------------------------------------------- #
def build_figure(df: pd.DataFrame, spec: dict[str, Any]) -> tuple[Figure, dict[str, Any]]:
    chart_type = spec.get("chart_type", "bar")
    palette = PALETTES.get(spec.get("palette", "studio"), PALETTES["studio"])
    width = float(spec.get("width", 10))
    height = float(spec.get("height", 5.5))
    fig, ax = plt.subplots(figsize=(width, height), dpi=100)
    fig.patch.set_facecolor("#FFFFFF")

    x_col = spec.get("x")
    y_col = spec.get("y")
    group_col = spec.get("group")
    agg = spec.get("agg", "sum")
    freq = spec.get("freq", "M")
    top_n = int(spec.get("top_n") or 0)
    bins = int(spec.get("bins") or 20)
    meta: dict[str, Any] = {"rows_used": int(len(df))}

    work = df.copy()

    # ---------------------------------------------------------------- count --
    if chart_type == "count":
        if x_col not in work.columns:
            raise ValueError("Pick a column for count")
        values = work[x_col].astype(str).str.strip().replace({"nan": "Missing", "None": "Missing"})
        counts = values.value_counts()
        if top_n:
            counts = counts.head(top_n)
        ax.bar([str(i) for i in counts.index], counts.values, color=palette[0], width=0.65)
        if spec.get("show_values", True) and len(counts) <= 20:
            for i, v in enumerate(counts.values):
                ax.text(i, v, _compact(v), ha="center", va="bottom", fontsize=8.5, color="#374151")
        _style_axes(ax, spec)
        ax.set_ylabel("Rows")

    # ----------------------------------------------------------------- hist --
    elif chart_type == "hist":
        col = y_col or x_col
        if col not in work.columns:
            raise ValueError("Pick a numeric column")
        numeric = pd.to_numeric(work[col], errors="coerce").dropna()
        if numeric.empty:
            raise ValueError(f"'{col}' has no numeric values")
        ax.hist(numeric, bins=bins, color=palette[0], edgecolor="#FFFFFF", linewidth=0.8)
        _style_axes(ax, spec)
        ax.set_xlabel(col)
        ax.set_ylabel("Rows")
        ax.xaxis.set_major_formatter(FuncFormatter(_compact))

    # ---------------------------------------------------------------- scatter --
    elif chart_type == "scatter":
        if x_col not in work.columns or y_col not in work.columns:
            raise ValueError("Scatter needs an X and a Y column")
        xs = pd.to_numeric(work[x_col], errors="coerce")
        ys = pd.to_numeric(work[y_col], errors="coerce")
        mask = xs.notna() & ys.notna()
        if group_col and group_col in work.columns:
            groups = work.loc[mask, group_col].astype(str)
            for i, (name, idx) in enumerate(groups.groupby(groups).groups.items()):
                ax.scatter(xs.loc[idx], ys.loc[idx], s=28, alpha=0.75,
                           color=palette[i % len(palette)], label=str(name), edgecolors="white", linewidths=0.4)
            ax.legend(title=group_col, fontsize=8, title_fontsize=8.5, frameon=False)
        else:
            ax.scatter(xs[mask], ys[mask], s=28, alpha=0.75, color=palette[0],
                       edgecolors="white", linewidths=0.4)
        _style_axes(ax, spec)
        ax.set_xlabel(x_col)
        ax.set_ylabel(y_col)
        ax.xaxis.set_major_formatter(FuncFormatter(_compact))

    # ------------------------------------------------------------------ box --
    elif chart_type == "box":
        if y_col not in work.columns:
            raise ValueError("Box plot needs a Y column")
        if x_col and x_col in work.columns:
            groups, labels = [], []
            order = work.groupby(x_col, observed=True)[y_col].apply(
                lambda s: pd.to_numeric(s, errors="coerce").sum())
            for name in order.sort_values(ascending=False).index[: (top_n or 15)]:
                subset = pd.to_numeric(work.loc[work[x_col] == name, y_col], errors="coerce").dropna()
                if len(subset):
                    groups.append(subset.values)
                    labels.append(str(name))
            if not groups:
                raise ValueError("No numeric data for the box plot")
            bp = ax.boxplot(groups, labels=labels, patch_artist=True, showfliers=True,
                            medianprops=dict(color="#111827", linewidth=1.4),
                            flierprops=dict(marker="o", markersize=3, alpha=0.5, markerfacecolor="#9CA3AF",
                                            markeredgecolor="#9CA3AF"))
            for i, box in enumerate(bp["boxes"]):
                box.set(facecolor=palette[i % len(palette)], alpha=0.75, edgecolor="#374151", linewidth=0.8)
        else:
            numeric = pd.to_numeric(work[y_col], errors="coerce").dropna()
            bp = ax.boxplot([numeric.values], patch_artist=True, labels=[y_col],
                            medianprops=dict(color="#111827", linewidth=1.4))
            for box in bp["boxes"]:
                box.set(facecolor=palette[0], alpha=0.75, edgecolor="#374151")
        _style_axes(ax, spec)
        ax.set_ylabel(y_col)

    # -------------------------------------------------------------- heatmap --
    elif chart_type == "heatmap":
        numeric_cols = [c for c in work.columns if pd.api.types.is_numeric_dtype(work[c])][:12]
        if len(numeric_cols) < 2:
            raise ValueError("Correlation heatmap needs at least 2 numeric columns")
        corr = work[numeric_cols].apply(pd.to_numeric, errors="coerce").corr()
        im = ax.imshow(corr.values, cmap="coolwarm", vmin=-1, vmax=1)
        ax.set_xticks(range(len(corr.columns)))
        ax.set_xticklabels(corr.columns, rotation=40, ha="right", fontsize=8)
        ax.set_yticks(range(len(corr.index)))
        ax.set_yticklabels(corr.index, fontsize=8)
        for i in range(len(corr.index)):
            for j in range(len(corr.columns)):
                value = corr.values[i, j]
                ax.text(j, i, f"{value:.2f}", ha="center", va="center", fontsize=7.5,
                        color="white" if abs(value) > 0.55 else "#1F2937")
        fig.colorbar(im, ax=ax, shrink=0.8)
        ax.grid(False)

    # ------------------------------------------------------- heatmap counts --
    elif chart_type == "heatmap_counts":
        if x_col not in work.columns or y_col not in work.columns:
            raise ValueError("Pick two categorical columns")
        xv = work[x_col].astype(str).str.strip()
        yv = work[y_col].astype(str).str.strip()
        if xv.nunique() > 15:
            xv = xv.where(xv.isin(xv.value_counts().head(15).index), "Other")
        if yv.nunique() > 15:
            yv = yv.where(yv.isin(yv.value_counts().head(15).index), "Other")
        ct = pd.crosstab(yv, xv)
        im = ax.imshow(ct.values, cmap="Blues")
        ax.set_xticks(range(len(ct.columns)))
        ax.set_xticklabels([str(c) for c in ct.columns], rotation=40, ha="right", fontsize=8)
        ax.set_yticks(range(len(ct.index)))
        ax.set_yticklabels([str(i) for i in ct.index], fontsize=8)
        for i in range(ct.shape[0]):
            for j in range(ct.shape[1]):
                ax.text(j, i, f"{int(ct.values[i, j]):,}", ha="center", va="center", fontsize=7.5,
                        color="white" if ct.values[i, j] > ct.values.max() * 0.6 else "#1F2937")
        fig.colorbar(im, ax=ax, shrink=0.8)
        ax.grid(False)

    # ------------------------------------------------------- pie / donut -----
    elif chart_type in ("pie", "donut"):
        if x_col not in work.columns:
            raise ValueError("Pick a category column")
        if y_col and y_col in work.columns and agg != "count":
            grouped = (work.assign(_v=pd.to_numeric(work[y_col], errors="coerce"))
                           .groupby(work[x_col].astype(str))["_v"].agg(agg))
        else:
            grouped = work[x_col].astype(str).value_counts()
        grouped = grouped.sort_values(ascending=False)
        if top_n:
            if len(grouped) > top_n:
                rest = grouped.iloc[top_n:]
                grouped = pd.concat([grouped.head(top_n), pd.Series({"Other": rest.sum()})])
        values = grouped.clip(lower=0).values
        labels = [str(i) for i in grouped.index]
        total = values.sum()
        colors = [palette[i % len(palette)] for i in range(len(values))]
        wedges, texts, autotexts = ax.pie(
            values, labels=labels, colors=colors, autopct=lambda p: f"{p * total / 100:,.0f}\n({p:.1f}%)",
            startangle=90, counterclock=False, textprops={"fontsize": 8.5, "color": "#374151"},
            wedgeprops=dict(edgecolor="white", linewidth=1.2),
        )
        for t in autotexts:
            t.set_color("white")
            t.set_fontsize(7.5)
            t.set_fontweight("bold")
        if chart_type == "donut":
            centre = plt.Circle((0, 0), 0.62, fc="white")
            ax.add_artist(centre)
        ax.set_ylabel("")

    # ------------------------------------------------------- bar family ------
    else:
        if x_col not in work.columns:
            raise ValueError("Pick a column for the X axis")
        use_y = y_col if (y_col and y_col in work.columns and agg != "count") else None

        # time series
        is_date_x = bool(spec.get("x_is_date"))
        if not is_date_x:
            try:
                is_date_x = pd.api.types.is_datetime64_any_dtype(work[x_col])
            except Exception:
                is_date_x = False

        if is_date_x:
            work["_x"] = _period_label(work[x_col], freq)
        else:
            work["_x"] = work[x_col].astype(str).str.strip().replace({"nan": "Missing", "None": "Missing"})

        if group_col and group_col in work.columns:
            work["_g"] = work[group_col].astype(str).str.strip().replace({"nan": "Missing"})
            if use_y:
                work["_v"] = pd.to_numeric(work[use_y], errors="coerce")
                pivot = work.pivot_table(index="_x", columns="_g", values="_v",
                                         aggfunc=agg, dropna=False)
            else:
                pivot = work.pivot_table(index="_x", columns="_g", values=work.index,
                                         aggfunc="count", dropna=False)
            if is_date_x:
                pivot = pivot.sort_index()
            if top_n:
                take = pivot.sum(axis=1).sort_values(ascending=False).head(top_n).index
                pivot = pivot.loc[pivot.index.isin(take)]
                if is_date_x:
                    pivot = pivot.sort_index()
            else:
                pivot = pivot.sort_index() if is_date_x else pivot.loc[pivot.sum(axis=1).sort_values(ascending=False).index]

            labels = [str(i) for i in pivot.index]
            bottom = np.zeros(len(pivot))
            for i, col in enumerate(pivot.columns):
                values = pivot[col].fillna(0).values
                if chart_type == "stacked_bar":
                    ax.bar(labels, values, bottom=bottom, color=palette[i % len(palette)],
                           label=str(col), width=0.68)
                    bottom += values
                elif chart_type == "line":
                    ax.plot(labels, values, marker="o", markersize=4, linewidth=1.8,
                            color=palette[i % len(palette)], label=str(col))
                elif chart_type == "area":
                    ax.fill_between(range(len(labels)), bottom, bottom + values,
                                    color=palette[i % len(palette)], alpha=0.55, label=str(col))
                    ax.plot(range(len(labels)), bottom + values, color=palette[i % len(palette)], linewidth=1.2)
                    bottom += values
                else:
                    ax.bar(labels, values, color=palette[i % len(palette)], label=str(col), width=0.68)
            if chart_type == "area":
                ax.set_xticks(range(len(labels)))
                ax.set_xticklabels(labels)
            ax.legend(title=group_col, fontsize=8, title_fontsize=8.5, frameon=False, ncol=min(4, len(pivot.columns)))
        else:
            grouped = work.groupby("_x", observed=True)
            if use_y:
                values = grouped[use_y].apply(lambda s: _agg_series(s, agg))
            else:
                values = grouped.size().astype(float)
            series = values.sort_values(ascending=False)
            if is_date_x:
                series = values.sort_index()
            if top_n and not is_date_x:
                series = series.head(top_n)
            labels = [str(i) for i in series.index]
            yvals = series.values

            if chart_type == "line":
                ax.plot(labels, yvals, marker="o", markersize=4.5, linewidth=2, color=palette[0])
            elif chart_type == "area":
                ax.fill_between(range(len(labels)), yvals, color=palette[0], alpha=0.35)
                ax.plot(range(len(labels)), yvals, color=palette[0], linewidth=2)
                ax.set_xticks(range(len(labels)))
                ax.set_xticklabels(labels)
            elif chart_type == "hbar":
                ax.barh(labels[::-1], yvals[::-1], color=palette[0], height=0.65)
                if spec.get("show_values") and len(labels) <= 20:
                    for i, v in enumerate(yvals[::-1]):
                        ax.text(v, i, f"  {_compact(v)}", va="center", fontsize=8.5, color="#374151")
                ax.xaxis.set_major_formatter(FuncFormatter(_compact))
                ax.grid(axis="y", visible=False)
                ax.grid(axis="x", color="#E5E7EB", linewidth=0.8)
            else:
                ax.bar(labels, yvals, color=palette[0], width=0.68)
                if spec.get("show_values") and len(labels) <= 20:
                    for i, v in enumerate(yvals):
                        ax.text(i, v, _compact(v), ha="center", va="bottom", fontsize=8.5, color="#374151")

        if chart_type != "hbar":
            _style_axes(ax, spec)
        else:
            ax.set_facecolor("#FFFFFF")
            for spine in ("top", "right"):
                ax.spines[spine].set_visible(False)
            ax.tick_params(colors="#4B5563", labelsize=9)
            ax.set_axisbelow(True)

        ylabel = {
            "sum": f"Total {y_col}" if use_y else "Rows",
            "mean": f"Average {y_col}" if use_y else "Rows",
            "median": f"Median {y_col}" if use_y else "Rows",
            "min": f"Min {y_col}" if use_y else "Rows",
            "max": f"Max {y_col}" if use_y else "Rows",
            "std": f"Std dev {y_col}" if use_y else "Rows",
            "nunique": f"Unique {y_col}" if use_y else "Rows",
        }.get(agg, y_col or "Value")
        if chart_type != "hbar":
            ax.set_ylabel(ylabel)

    if spec.get("xlim") == "tight" or chart_type in ("line", "area"):
        try:
            ax.margins(x=0.02)
        except Exception:
            pass

    ax.set_title(_title_for(spec), fontsize=12.5, fontweight="bold", color="#111827", pad=12)
    fig.tight_layout()
    meta["chart_type"] = chart_type
    meta["title"] = _title_for(spec)
    return fig, meta


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #
def render_chart(df: pd.DataFrame, spec: dict[str, Any], out_path, dpi: int = 130) -> dict[str, Any]:
    """Render to a PNG file, return meta."""
    fig, meta = build_figure(df, spec)
    try:
        fig.savefig(out_path, dpi=dpi, facecolor="#FFFFFF", bbox_inches="tight")
    finally:
        plt.close(fig)
    meta["path"] = str(out_path)
    return meta


def render_to_bytes(df: pd.DataFrame, spec: dict[str, Any], fmt: str, dpi: int = 200) -> bytes:
    """Render to an in-memory buffer in the requested format."""
    fmt = (fmt or "png").lower()
    fig, _ = build_figure(df, spec)
    buffer = io.BytesIO()
    save_kwargs: dict[str, Any] = {"facecolor": "#FFFFFF", "bbox_inches": "tight"}
    if fmt in ("png", "jpg", "jpeg", "webp", "tiff", "tif"):
        save_kwargs["dpi"] = dpi
    if fmt in ("jpg", "jpeg"):
        save_kwargs["pil_kwargs"] = {"quality": 95}
    try:
        fig.savefig(buffer, format=fmt, **save_kwargs)
    finally:
        plt.close(fig)
    buffer.seek(0)
    return buffer.getvalue()


def export_mime(fmt: str) -> str:
    for item in EXPORT_FORMATS:
        if item["id"] == fmt:
            return item["mime"]
    return "application/octet-stream"
