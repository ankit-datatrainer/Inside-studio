"""Robust file loading for Excel / CSV / TSV / PDF.

Handles: multiple sheets, sheet previews, different CSV encodings and
delimiters, header-row detection, PDF text-table extraction, multi-file
merging, and the classic "index column exported by Excel" artifact
(Unnamed: 0).
"""
from __future__ import annotations

import csv
import io
import re
from pathlib import Path
from typing import Any

import pandas as pd

CSV_EXTENSIONS = {".csv", ".tsv", ".txt"}
EXCEL_EXTENSIONS = {".xlsx", ".xlsm", ".xls"}
PDF_EXTENSIONS = {".pdf"}
ALL_EXTENSIONS = CSV_EXTENSIONS | EXCEL_EXTENSIONS | PDF_EXTENSIONS

ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1")


def source_kind(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in EXCEL_EXTENSIONS:
        return "excel"
    if ext in CSV_EXTENSIONS:
        return "csv"
    if ext in PDF_EXTENSIONS:
        return "pdf"
    raise ValueError(f"Unsupported file type: {ext}")


# --------------------------------------------------------------------------- #
# Excel
# --------------------------------------------------------------------------- #
def list_sheets(path: Path) -> list[dict[str, Any]]:
    """Return name + shape for every sheet (shape from a cheap read)."""
    kind = source_kind(path)
    if kind == "pdf":
        return [{"name": "(PDF document)", "rows": None, "cols": None}]
    if kind != "excel":
        return [{"name": "(csv file)", "rows": None, "cols": None}]
    xl = pd.ExcelFile(path)
    out = []
    for name in xl.sheet_names:
        try:
            preview = xl.parse(name, nrows=5)
            rows = len(xl.parse(name, usecols=[0])) if preview.shape[1] else 0
            out.append({"name": str(name), "rows": int(rows), "cols": int(preview.shape[1])})
        except Exception:
            out.append({"name": str(name), "rows": None, "cols": None})
    return out


# --------------------------------------------------------------------------- #
# CSV helpers
# --------------------------------------------------------------------------- #
def _sniff_delimiter(text: str) -> str:
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        return dialect.delimiter
    except csv.Error:
        return "\t" if sample.count("\t") > sample.count(",") else ","


def _read_csv_any(path: Path, header_row: int = 0) -> tuple[pd.DataFrame, dict[str, Any]]:
    last_error: Exception | None = None
    for enc in ENCODINGS:
        try:
            raw = path.read_text(encoding=enc, errors="strict")
            delimiter = _sniff_delimiter(raw)
            df = pd.read_csv(
                io.StringIO(raw),
                sep=delimiter,
                header=header_row,
                engine="python",
                skip_blank_lines=True,
            )
            return df, {"encoding": enc, "delimiter": delimiter}
        except (UnicodeDecodeError, pd.errors.ParserError, Exception) as exc:  # noqa: BLE001
            last_error = exc
            continue
    raise ValueError(f"Could not read CSV file: {last_error}")


# --------------------------------------------------------------------------- #
# PDF helpers
# --------------------------------------------------------------------------- #
def _read_pdf_tables(path: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Extract tabular data from a PDF using pymupdf.

    Strategy:
    1. Try to find tables on each page (pymupdf's built-in table finder).
    2. Fall back to extracting text and parsing CSV-like lines.
    """
    try:
        import pymupdf  # noqa: F811
    except ImportError:
        raise ValueError("pymupdf (PyMuPDF) is required to read PDF files. pip install pymupdf")

    doc = pymupdf.open(str(path))
    all_dfs: list[pd.DataFrame] = []
    text_lines: list[str] = []

    for page_num in range(len(doc)):
        page = doc[page_num]

        # Try table extraction first
        try:
            tables = page.find_tables()
            for table in tables:
                tdf = table.to_pandas()
                if tdf.shape[0] > 0 and tdf.shape[1] > 1:
                    all_dfs.append(tdf)
        except Exception:
            pass

        # Also collect text for fallback
        text = page.get_text("text")
        if text.strip():
            text_lines.append(text)

    doc.close()

    if all_dfs:
        # Merge tables with same columns
        groups: dict[str, list[pd.DataFrame]] = {}
        for df in all_dfs:
            key = "|".join(str(c) for c in df.columns)
            groups.setdefault(key, []).append(df)

        # pick the largest group
        biggest_group = max(groups.values(), key=lambda g: sum(len(d) for d in g))
        merged = pd.concat(biggest_group, ignore_index=True)
        return merged, {"kind": "pdf", "tables_found": len(all_dfs), "method": "table_extraction"}

    # Fallback: parse text lines as CSV-like
    if text_lines:
        full_text = "\n".join(text_lines)
        # try to detect delimiter in text
        lines = [l.strip() for l in full_text.split("\n") if l.strip()]
        if len(lines) >= 2:
            # check if tab or comma separated
            delimiter = _sniff_delimiter("\n".join(lines[:20]))
            try:
                df = pd.read_csv(
                    io.StringIO("\n".join(lines)),
                    sep=delimiter,
                    engine="python",
                    skip_blank_lines=True,
                )
                if df.shape[1] > 1:
                    return df, {"kind": "pdf", "method": "text_parsing"}
            except Exception:
                pass

        # Last resort: put all text in a single-column DataFrame
        return pd.DataFrame({"Text": lines}), {"kind": "pdf", "method": "text_only"}

    raise ValueError("Could not extract any data from the PDF file.")


# --------------------------------------------------------------------------- #
# Multi-file merging
# --------------------------------------------------------------------------- #
def read_multiple_files(paths: list[Path]) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Read multiple files and try to merge them into one DataFrame.

    Files with the same columns are concatenated.
    Files with different columns are kept separate (returns the largest).
    """
    if not paths:
        raise ValueError("No files provided")

    if len(paths) == 1:
        return read_table(paths[0])

    dfs: list[tuple[pd.DataFrame, dict[str, Any], str]] = []
    errors: list[str] = []

    for path in paths:
        try:
            df, info = read_table(path)
            dfs.append((df, info, path.name))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{path.name}: {exc}")

    if not dfs:
        raise ValueError(f"Could not read any files. Errors: {'; '.join(errors)}")

    # Try to merge files with compatible columns
    groups: dict[str, list[tuple[pd.DataFrame, str]]] = {}
    for df, _info, name in dfs:
        col_key = "|".join(sorted(str(c) for c in df.columns))
        groups.setdefault(col_key, []).append((df, name))

    # Take the largest group (most files with the same schema)
    best_group = max(groups.values(), key=lambda g: sum(len(d) for d, _ in g))
    merged_dfs = [df for df, _ in best_group]
    file_names = [name for _, name in best_group]

    merged = pd.concat(merged_dfs, ignore_index=True)

    info: dict[str, Any] = {
        "kind": "multi",
        "files_merged": len(best_group),
        "file_names": file_names,
        "total_files": len(dfs),
        "skipped": len(dfs) - len(best_group),
    }
    if errors:
        info["read_errors"] = errors

    merged, tidy_info = tidy_frame(merged)
    info.update(tidy_info)
    return merged, info


# --------------------------------------------------------------------------- #
# main entry point
# --------------------------------------------------------------------------- #
def read_table(
    path: Path,
    sheet: str | None = None,
    header_row: int = 0,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Read a table from disk and return (dataframe, info)."""
    kind = source_kind(path)
    info: dict[str, Any] = {"kind": kind}

    if kind == "excel":
        if sheet is None:
            xl = pd.ExcelFile(path)
            sheet = xl.sheet_names[0]
        df = pd.read_excel(path, sheet_name=sheet, header=header_row)
        info["sheet"] = sheet
    elif kind == "pdf":
        df, pdf_info = _read_pdf_tables(path)
        info.update(pdf_info)
    else:
        df, csv_info = _read_csv_any(path, header_row=header_row)
        info.update(csv_info)

    df, tidy_info = tidy_frame(df)
    info.update(tidy_info)
    return df, info


def tidy_frame(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Light automatic tidy-up that is always safe:

    * remove completely empty rows / columns
    * drop the unnamed index column Excel often adds
    * trim whitespace in column names
    """
    notes = {"empty_rows_removed": 0, "empty_cols_removed": 0, "index_cols_removed": []}

    before_rows = len(df)
    df = df.dropna(how="all")
    notes["empty_rows_removed"] = before_rows - len(df)

    before_cols = df.shape[1]
    df = df.dropna(axis=1, how="all")
    notes["empty_cols_removed"] = before_cols - df.shape[1]

    drop_cols = [
        c for c in df.columns
        if str(c).strip().lower().startswith("unnamed:")
    ]
    # only drop an unnamed column if it looks like a row index (0..n-1)
    for col in drop_cols:
        values = pd.to_numeric(df[col], errors="coerce")
        if values.notna().all() and (values.diff().dropna() == 1).all():
            df = df.drop(columns=[col])
            notes["index_cols_removed"].append(str(col))

    df.columns = [str(c).strip() for c in df.columns]
    df = df.reset_index(drop=True)
    return df, notes
