"""Dashboard exports - everything built with matplotlib (same library as the
notebook), so nothing new to install for PDF generation.

* build_dashboard_pdf()  -> multi-page PDF: cover + KPI cards + insight text
                            + chart grid pages + data table pages
* build_dashboard_html() -> single self-contained .html file (images embedded)
* build_dashboard_png()  -> one poster image with all charts combined
"""
from __future__ import annotations

import base64
import io
import textwrap
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.patches import FancyBboxPatch
import numpy as np

PAGE_W, PAGE_H = 11.69, 8.27  # A4 landscape (inches)
ACCENT = "#0066FF"
INK = "#0A1324"
MUTED = "#64748B"
LIGHT = "#F8FAFD"


# --------------------------------------------------------------------------- #
# small drawing helpers
# --------------------------------------------------------------------------- #
def _new_page() -> plt.Figure:
    fig = plt.figure(figsize=(PAGE_W, PAGE_H), dpi=100)
    fig.patch.set_facecolor("#FFFFFF")
    return fig


def _header(fig: plt.Figure, title: str, subtitle: str = "") -> None:
    fig.text(0.05, 0.955, title, fontsize=15, fontweight="bold", color=INK, va="top")
    if subtitle:
        fig.text(0.05, 0.918, subtitle, fontsize=9, color=MUTED, va="top")
    line = plt.Line2D([0.05, 0.95], [0.905, 0.905], transform=fig.transFigure,
                      color="#E5E7EB", linewidth=1)
    fig.add_artist(line)


def _footer(fig: plt.Figure, page: int, total: int | None = None, note: str = "") -> None:
    label = f"Page {page}" + (f" of {total}" if total else "")
    fig.text(0.95, 0.03, label, fontsize=8, color=MUTED, ha="right")
    if note:
        fig.text(0.05, 0.03, note, fontsize=8, color=MUTED)


def _kpi_cards(fig: plt.Figure, kpis: list[dict[str, Any]], top: float = 0.86) -> None:
    """Draw KPI cards in rows of 4."""
    if not kpis:
        return
    per_row = 4
    card_w, card_h = 0.212, 0.13
    gap = 0.013
    left = 0.05
    for i, kpi in enumerate(kpis[:8]):
        row, col = divmod(i, per_row)
        x = left + col * (card_w + gap)
        y = top - row * (card_h + 0.035) - card_h
        ax = fig.add_axes([x, y, card_w, card_h])
        ax.axis("off")
        ax.add_patch(FancyBboxPatch((0.02, 0.04), 0.96, 0.92,
                                    boxstyle="round,pad=0.015,rounding_size=0.06",
                                    linewidth=1, edgecolor="#E5E7EB", facecolor="#F9FAFB",
                                    transform=ax.transAxes, clip_on=False))
        ax.text(0.07, 0.68, str(kpi.get("label", ""))[:34], fontsize=8.5, color=MUTED,
                transform=ax.transAxes, va="center")
        ax.text(0.07, 0.34, str(kpi.get("value", ""))[:20], fontsize=15, fontweight="bold",
                color=INK, transform=ax.transAxes, va="center")
        ax.text(0.07, 0.13, str(kpi.get("sub", ""))[:44], fontsize=7.2, color=MUTED,
                transform=ax.transAxes, va="center")


def _draw_table(fig: plt.Figure, columns: list[str], rows: list[list[str]],
                title: str = "", rect: list[float] | None = None,
                font_size: float | None = None) -> None:
    rect = rect or [0.05, 0.09, 0.9, 0.76]
    ax = fig.add_axes(rect)
    ax.axis("off")
    if title:
        ax.set_title(title, fontsize=11, fontweight="bold", color=INK, loc="left", pad=8)

    if not rows:
        ax.text(0.5, 0.5, "No data", ha="center", va="center", color=MUTED, transform=ax.transAxes)
        return

    n_rows, n_cols = len(rows), len(columns)
    if font_size is None:
        font_size = max(6.0, min(9.5, 10.5 - n_cols * 0.45 - (0.6 if n_rows > 14 else 0)))

    table = ax.table(cellText=rows, colLabels=columns, loc="center", cellLoc="right")
    table.auto_set_font_size(False)
    table.set_fontsize(font_size)
    table.scale(1, 1.25)

    for (r, c), cell in table.get_celld().items():
        cell.set_edgecolor("#E5E7EB")
        cell.set_linewidth(0.6)
        if r == 0:
            cell.set_facecolor(ACCENT)
            cell.set_text_props(color="white", fontweight="bold")
            cell.set_height(0.075)
        else:
            cell.set_facecolor("#FFFFFF" if r % 2 else "#F9FAFB")
            cell.set_text_props(color="#1F2937")
            if c == 0:
                cell.set_text_props(ha="left")
                cell._loc = "left"


def _place_image(fig: plt.Figure, image_path: str, rect: list[float]) -> bool:
    path = Path(image_path)
    if not path.exists():
        return False
    try:
        image = plt.imread(path)
    except Exception:
        return False
    ax = fig.add_axes(rect)
    ax.imshow(image)
    ax.axis("off")
    return True


# --------------------------------------------------------------------------- #
# PDF
# --------------------------------------------------------------------------- #
def build_dashboard_pdf(
    out_path: Path,
    dataset_name: str,
    dataset_info: dict[str, Any],
    kpis: list[dict[str, Any]],
    insights: list[dict[str, str]],
    charts: list[dict[str, Any]],
    tables: list[dict[str, Any]],
    columns_per_row: int = 2,
) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    charts = [c for c in charts if Path(c.get("image", "")).exists()]
    generated = datetime.now().strftime("%d %b %Y, %H:%M")

    with PdfPages(out_path) as pdf:
        # ---- page 1: cover + KPIs -----------------------------------------
        fig = _new_page()
        _header(fig, "Data Analysis Dashboard", dataset_name)

        fig.text(0.05, 0.86, "Dataset overview", fontsize=11, fontweight="bold", color=INK)
        info_lines = [
            f"Rows: {dataset_info.get('rows', '-'):,}    Columns: {dataset_info.get('cols', '-')}",
            f"Source file: {dataset_info.get('source_file', '-')}",
            f"Sheet / table: {dataset_info.get('sheet', '-')}",
            f"Generated: {generated}",
        ]
        for i, line in enumerate(info_lines):
            fig.text(0.05, 0.825 - i * 0.028, line, fontsize=9, color=MUTED)

        _kpi_cards(fig, kpis, top=0.72)

        if insights:
            fig.text(0.05, 0.50, "Key findings", fontsize=11, fontweight="bold", color=INK)
            y = 0.465
            wrapped_count = 0
            for item in insights[:8]:
                text = str(item.get("text", "")).replace("**", "").replace("`", "")
                for line in textwrap.wrap(text, width=110)[:2]:
                    fig.text(0.05, y, "- " + line if line == textwrap.wrap(text, width=110)[0] else "  " + line,
                             fontsize=8.8, color="#374151")
                    y -= 0.026
                y -= 0.008
                wrapped_count += 1
                if y < 0.13:
                    break
        _footer(fig, 1, note="Insight Studio")
        pdf.savefig(fig)
        plt.close(fig)

        page = 2
        # ---- chart pages in a grid ----------------------------------------
        per_page = max(1, columns_per_row * 2)
        for start in range(0, len(charts), per_page):
            chunk = charts[start:start + per_page]
            fig = _new_page()
            _header(fig, "Charts", dataset_name)
            rows = 2 if len(chunk) > columns_per_row else 1
            cols = columns_per_row
            area_top, area_bottom = 0.865, 0.08
            area_h = area_top - area_bottom
            cell_h = area_h / rows
            cell_w = 0.9 / cols
            for i, chart in enumerate(chunk):
                r, c = divmod(i, cols)
                visible_in_row = min(cols, len(chunk) - r * cols)
                offset = (cols - visible_in_row) * cell_w / 2
                x = 0.05 + c * cell_w + offset
                y = area_top - (r + 1) * cell_h + 0.02
                rect = [x + 0.01, y, cell_w - 0.02, cell_h - 0.05]
                _place_image(fig, chart.get("image", ""), rect)
            _footer(fig, page, note="Insight Studio")
            pdf.savefig(fig)
            plt.close(fig)
            page += 1

        # ---- table pages ---------------------------------------------------
        for table in tables:
            columns_ = table.get("columns", [])[:12]
            rows_ = table.get("rows", [])
            if not columns_ or not rows_:
                continue
            per_page_rows = 20 if len(columns_) <= 6 else 13
            chunks = [rows_[i:i + per_page_rows] for i in range(0, len(rows_), per_page_rows)]
            for chunk_index, chunk in enumerate(chunks):
                fig = _new_page()
                title = table.get("title", "Table")
                subtitle = dataset_name + (f"  (part {chunk_index + 1})" if len(chunks) > 1 else "")
                _header(fig, title, subtitle)
                _draw_table(fig, columns_, chunk)
                _footer(fig, page, note="Insight Studio")
                pdf.savefig(fig)
                plt.close(fig)
                page += 1

        # ---- metadata -------------------------------------------------------
        meta = pdf.infodict()
        meta["Title"] = f"Data Analysis Dashboard - {dataset_name}"
        meta["Author"] = "Insight Studio"
        meta["Subject"] = "Automated data analysis report"
        meta["CreationDate"] = datetime.now()

    return out_path


# --------------------------------------------------------------------------- #
# self-contained HTML dashboard
# --------------------------------------------------------------------------- #
def _img_b64(path: Path) -> str | None:
    if not path.exists():
        return None
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{data}"


def build_dashboard_html(
    out_path: Path,
    dataset_name: str,
    dataset_info: dict[str, Any],
    kpis: list[dict[str, Any]],
    insights: list[dict[str, str]],
    charts: list[dict[str, Any]],
    tables: list[dict[str, Any]],
) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    generated = datetime.now().strftime("%d %b %Y, %H:%M")

    kpi_cards = "".join(
        f'<div class="kpi"><div class="kpi-label">{k.get("label", "")}</div>'
        f'<div class="kpi-value">{k.get("value", "")}</div>'
        f'<div class="kpi-sub">{k.get("sub", "")}</div></div>'
        for k in kpis
    )

    insight_items = "".join(
        f'<li>{str(i.get("text", "")).replace("**", "").replace("`", "")}</li>' for i in insights
    )

    chart_cards = ""
    for chart in charts:
        src = _img_b64(Path(chart.get("image", "")))
        if not src:
            continue
        chart_cards += (
            f'<div class="card"><img src="{src}" alt="{chart.get("title", "")}"/></div>'
        )

    table_cards = ""
    for table in tables:
        cols = table.get("columns", [])
        rows = table.get("rows", [])
        if not cols or not rows:
            continue
        head = "".join(f"<th>{c}</th>" for c in cols)
        body = "".join(
            "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows[:100]
        )
        table_cards += (
            f'<div class="card wide"><h3>{table.get("title", "")}</h3>'
            f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div></div>'
        )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Data Analysis Dashboard - {dataset_name}</title>
<style>
  :root {{ --accent:{ACCENT}; --ink:{INK}; --muted:{MUTED}; --line:#E5E7EB; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; font-family: 'Segoe UI', system-ui, -apple-system, sans-serif; background:#F3F4F6; color:var(--ink); }}
  header {{ background:linear-gradient(120deg,#312E81, {ACCENT}); color:#fff; padding:28px 36px; }}
  header h1 {{ margin:0 0 6px; font-size:22px; }}
  header p {{ margin:0; opacity:.85; font-size:13px; }}
  main {{ max-width:1200px; margin:0 auto; padding:26px 20px 60px; }}
  .kpis {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(190px,1fr)); gap:14px; }}
  .kpi {{ background:#fff; border:1px solid var(--line); border-radius:12px; padding:14px 16px; }}
  .kpi-label {{ font-size:12px; color:var(--muted); }}
  .kpi-value {{ font-size:24px; font-weight:700; margin:4px 0; }}
  .kpi-sub {{ font-size:11px; color:var(--muted); }}
  section {{ margin-top:26px; }}
  h2 {{ font-size:16px; margin:0 0 12px; }}
  ul.insights {{ background:#fff; border:1px solid var(--line); border-radius:12px; padding:16px 22px; }}
  ul.insights li {{ margin:6px 0; font-size:13.5px; color:#374151; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(460px,1fr)); gap:16px; }}
  .card {{ background:#fff; border:1px solid var(--line); border-radius:12px; padding:14px; }}
  .card.wide {{ grid-column:1/-1; }}
  .card h3 {{ margin:0 0 10px; font-size:14px; }}
  .card img {{ width:100%; height:auto; display:block; border-radius:8px; }}
  .table-wrap {{ overflow:auto; max-height:460px; }}
  table {{ width:100%; border-collapse:collapse; font-size:12.5px; }}
  th, td {{ border-bottom:1px solid var(--line); padding:7px 9px; text-align:right; white-space:nowrap; }}
  th {{ background:#EEF2FF; color:#3730A3; position:sticky; top:0; }}
  td:first-child, th:first-child {{ text-align:left; }}
  footer {{ text-align:center; color:var(--muted); font-size:11.5px; margin-top:40px; }}
</style>
</head>
<body>
<header>
  <h1>Data Analysis Dashboard</h1>
  <p>{dataset_name} &middot; {dataset_info.get("rows", "-")} rows &middot; {dataset_info.get("cols", "-")} columns &middot; generated {generated}</p>
</header>
<main>
  <section><div class="kpis">{kpi_cards}</div></section>
  {"<section><h2>Key findings</h2><ul class='insights'>" + insight_items + "</ul></section>" if insight_items else ""}
  {"<section><h2>Charts</h2><div class='grid'>" + chart_cards + "</div></section>" if chart_cards else ""}
  {"<section><h2>Tables</h2><div class='grid'>" + table_cards + "</div></section>" if table_cards else ""}
  <footer>Generated by Insight Studio - automated pandas + matplotlib analysis</footer>
</main>
</body>
</html>"""
    out_path.write_text(html, encoding="utf-8")
    return out_path


# --------------------------------------------------------------------------- #
# single-image poster PNG
# --------------------------------------------------------------------------- #
def build_dashboard_png(
    out_path: Path,
    dataset_name: str,
    kpis: list[dict[str, Any]],
    charts: list[dict[str, Any]],
    columns: int = 2,
) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    charts = [c for c in charts if Path(c.get("image", "")).exists()]
    if not charts:
        raise ValueError("Add at least one chart to the dashboard first")

    rows = int(np.ceil(len(charts) / columns))
    kpi_rows = int(np.ceil(min(len(kpis), 8) / 4))
    fig_h = 1.05 + kpi_rows * 0.95 + rows * 3.1
    fig_w = 4.1 * columns

    fig = plt.figure(figsize=(fig_w, fig_h), dpi=110)
    fig.patch.set_facecolor("#F9FAFB")

    fig.text(0.03, 0.985, "Data Analysis Dashboard", fontsize=17, fontweight="bold",
             color=INK, va="top")
    fig.text(0.03, 0.985 - 0.30 / fig_h, dataset_name, fontsize=10, color=MUTED, va="top")

    top = 1 - (0.85 / fig_h)
    if kpis:
        card_w = 0.9 / 4
        for i, kpi in enumerate(kpis[:8]):
            r, c = divmod(i, 4)
            x = 0.05 + c * card_w
            y = top - (r + 1) * (0.75 / fig_h) - 0.02
            ax = fig.add_axes([x, y, card_w - 0.012, 0.62 / fig_h])
            ax.axis("off")
            ax.add_patch(FancyBboxPatch((0.02, 0.06), 0.96, 0.88, boxstyle="round,pad=0.02,rounding_size=0.08",
                                        linewidth=1, edgecolor="#E5E7EB", facecolor="#FFFFFF",
                                        transform=ax.transAxes, clip_on=False))
            ax.text(0.08, 0.62, str(kpi.get("label", ""))[:32], fontsize=8.5, color=MUTED,
                    transform=ax.transAxes, va="center")
            ax.text(0.08, 0.28, str(kpi.get("value", ""))[:20], fontsize=14, fontweight="bold",
                    color=INK, transform=ax.transAxes, va="center")

    grid_top = top - kpi_rows * (0.75 / fig_h) - 0.04
    cell_h = (fig_h - 1.15 - kpi_rows * 0.95) / rows
    cell_h_n = cell_h / fig_h
    cell_w_n = 0.9 / columns

    for i, chart in enumerate(charts):
        r, c = divmod(i, columns)
        visible_in_row = min(columns, len(charts) - r * columns)
        offset = (columns - visible_in_row) * cell_w_n / 2
        x = 0.05 + c * cell_w_n + offset
        y = grid_top - (r + 1) * cell_h_n + 0.008
        ax = fig.add_axes([x, y, cell_w_n - 0.015, cell_h_n - 0.03])
        try:
            ax.imshow(plt.imread(chart.get("image", "")))
        except Exception:
            continue
        ax.axis("off")

    fig.savefig(out_path, dpi=140, facecolor="#F9FAFB", bbox_inches="tight")
    plt.close(fig)
    return out_path
