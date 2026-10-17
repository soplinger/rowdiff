#!/usr/bin/env python3
"""rowdiff: compare two CSV files row by row and report what changed.

Rows are matched by one or more key columns, so reordering a file does not
count as a change. Without --key, rows are matched by position.

Exit codes: 0 no differences, 1 differences found, 2 input or usage error.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

EXIT_SAME = 0
EXIT_DIFFERENT = 1
EXIT_ERROR = 2

DELIMITER_CANDIDATES = ",;\t|"
DELIMITER_NAMES = {"comma": ",", "semicolon": ";", "tab": "\t", "pipe": "|"}
# Plain decimals only. Leading zeros and exponents are compared as text, so
# "02134" != "2134" and "1E+11" != "100000000000" (the usual spreadsheet damage).
NUMBER_RE = re.compile(r"[+-]?(?:(?:0|[1-9]\d*)(?:\.\d*)?|\.\d+)")


class RowDiffError(Exception):
    """Unreadable input or an unusable comparison. Carries every message found."""

    def __init__(self, messages):
        self.messages = list(messages)
        super().__init__("\n".join(self.messages))


@dataclass
class Row:
    line: int
    values: dict


@dataclass
class Table:
    name: str
    header: list
    rows: list
    delimiter: str


@dataclass
class Options:
    keys: tuple = ()
    columns: tuple = ()
    ignore_columns: tuple = ()
    tolerance: Decimal = Decimal(0)
    strict_numbers: bool = False
    ignore_case: bool = False
    ignore_whitespace: bool = False
    ignore_column_order: bool = False


@dataclass
class RowRef:
    key: dict
    line: int
    values: dict


@dataclass
class CellChange:
    column: str
    old: str
    new: str


@dataclass
class RowChange:
    key: dict
    old_line: int
    new_line: int
    cells: list = field(default_factory=list)


@dataclass
class Report:
    old_name: str
    new_name: str
    key_columns: list
    columns_added: list
    columns_removed: list
    columns_reordered: bool
    columns_compared: list
    added: list = field(default_factory=list)
    removed: list = field(default_factory=list)
    changed: list = field(default_factory=list)
    unchanged: int = 0

    @property
    def has_differences(self):
        return bool(
            self.added
            or self.removed
            or self.changed
            or self.columns_added
            or self.columns_removed
            or self.columns_reordered
        )

    def summary(self):
        return {
            "added": len(self.added),
            "removed": len(self.removed),
            "changed": len(self.changed),
            "unchanged": self.unchanged,
            "columns_added": len(self.columns_added),
            "columns_removed": len(self.columns_removed),
        }


# ---------------------------------------------------------------- parsing

def guess_delimiter(text):
    """Pick the candidate that appears most often in the first non-blank line."""
    for line in text.splitlines():
        if line.strip():
            counts = {d: line.count(d) for d in DELIMITER_CANDIDATES}
            best = max(DELIMITER_CANDIDATES, key=lambda d: counts[d])
            return best if counts[best] else ","
    return ","


def resolve_delimiter(value):
    """Accept a single character or a name such as 'tab' or 'semicolon'."""
    if value is None:
        return None
    if value.lower() in DELIMITER_NAMES:
        return DELIMITER_NAMES[value.lower()]
    if len(value) == 1:
        return value
    raise ValueError(f"delimiter must be one character or one of {', '.join(DELIMITER_NAMES)}")


def parse_table(text, name="input", delimiter=None):
    """Parse CSV text into a Table. Line numbers count blank lines and quoted newlines."""
    text = text.lstrip("\ufeff")
    if delimiter is None:
        delimiter = guess_delimiter(text)
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    records = []
    start = 1
    try:
        for fields in reader:
            line = start
            start = reader.line_num + 1
            if not fields or (len(fields) == 1 and not fields[0].strip()):
                continue
            records.append((line, fields))
    except csv.Error as exc:
        raise RowDiffError([f"{name}:{reader.line_num}: malformed CSV: {exc}"]) from exc

    if not records:
        raise RowDiffError([f"{name}: empty file, no header row found"])

    header_line, raw_header = records[0]
    header = [cell.strip() for cell in raw_header]
    messages = []
    first_seen = {}
    for position, column in enumerate(header, 1):
        if not column:
            messages.append(f"{name}:{header_line}: header column {position} is empty")
        elif column in first_seen:
            messages.append(
                f"{name}:{header_line}: duplicate column '{column}' "
                f"(also column {first_seen[column]})"
            )
        else:
            first_seen[column] = position

    rows = []
    for line, fields in records[1:]:
        if len(fields) != len(header):
            messages.append(f"{name}:{line}: expected {len(header)} fields, found {len(fields)}")
            continue
        rows.append(Row(line, dict(zip(header, fields))))

    if messages:
        raise RowDiffError(messages)
    return Table(name, header, rows, delimiter)


def load_table(path, encoding="utf-8-sig", delimiter=None):
    path = Path(path)
    with open(path, encoding=encoding, newline="") as handle:
        text = handle.read()
    return parse_table(text, name=path.name, delimiter=delimiter)


# ---------------------------------------------------------------- comparing

def normalize(value, opts):
    if opts.ignore_whitespace:
        value = " ".join(value.split())
    if opts.ignore_case:
        value = value.casefold()
    return value


def values_equal(a, b, opts):
    a, b = normalize(a, opts), normalize(b, opts)
    if a == b:
        return True
    if opts.strict_numbers:
        return False
    if NUMBER_RE.fullmatch(a) and NUMBER_RE.fullmatch(b):
        return abs(Decimal(a) - Decimal(b)) <= opts.tolerance
    return False


def describe(key):
    return ", ".join(f"{column}={value}" for column, value in key.items())


def index_rows(table, opts, errors):
    """Map match-key -> (Row, display key). Problems are appended to errors."""
    index = {}
    for position, row in enumerate(table.rows, 1):
        if not opts.keys:
            index[(position,)] = (row, {"row": str(position)})
            continue
        raw = tuple(row.values[column] for column in opts.keys)
        empty = [column for column, value in zip(opts.keys, raw) if not value.strip()]
        if empty:
            errors.append(f"{table.name}:{row.line}: empty value in key column '{empty[0]}'")
            continue
        match = tuple(normalize(value, opts) for value in raw)
        if match in index:
            first_line = index[match][0].line
            errors.append(
                f"{table.name}:{row.line}: duplicate key {describe(dict(zip(opts.keys, raw)))} "
                f"(first seen on line {first_line})"
            )
            continue
        index[match] = (row, dict(zip(opts.keys, raw)))
    return index


def compare(old, new, opts=None):
    opts = opts or Options()
    errors = []
    for key in opts.keys:
        for table in (old, new):
            if key not in table.header:
                errors.append(f"{table.name}: key column '{key}' not found")
    for column in opts.ignore_columns:
        if column in opts.keys:
            errors.append(f"cannot ignore key column '{column}'")
        elif column not in old.header and column not in new.header:
            errors.append(f"ignored column '{column}' not found in either file")
    for column in opts.columns:
        if column not in old.header or column not in new.header:
            errors.append(f"column '{column}' must exist in both files to be compared")
    if errors:
        raise RowDiffError(errors)

    common = [column for column in old.header if column in new.header]
    columns_added = [column for column in new.header if column not in old.header]
    columns_removed = [column for column in old.header if column not in new.header]
    reordered = not opts.ignore_column_order and [c for c in new.header if c in old.header] != common
    compared = [
        column
        for column in common
        if column not in opts.keys
        and column not in opts.ignore_columns
        and (not opts.columns or column in opts.columns)
    ]

    index_errors = []
    old_index = index_rows(old, opts, index_errors)
    new_index = index_rows(new, opts, index_errors)
    if index_errors:
        raise RowDiffError(index_errors)

    report = Report(
        old_name=old.name,
        new_name=new.name,
        key_columns=list(opts.keys) or ["row"],
        columns_added=columns_added,
        columns_removed=columns_removed,
        columns_reordered=reordered,
        columns_compared=compared,
    )
    for match, (row, key) in old_index.items():
        if match not in new_index:
            report.removed.append(RowRef(key, row.line, row.values))
            continue
        new_row, _ = new_index[match]
        cells = [
            CellChange(column, row.values[column], new_row.values[column])
            for column in compared
            if not values_equal(row.values[column], new_row.values[column], opts)
        ]
        if cells:
            report.changed.append(RowChange(key, row.line, new_row.line, cells))
        else:
            report.unchanged += 1
    for match, (row, key) in new_index.items():
        if match not in old_index:
            report.added.append(RowRef(key, row.line, row.values))

    report.added.sort(key=lambda r: r.line)
    report.removed.sort(key=lambda r: r.line)
    report.changed.sort(key=lambda r: r.old_line)
    return report


# ---------------------------------------------------------------- rendering

def render_text(report):
    lines = [
        f"rowdiff: {report.old_name} -> {report.new_name}  (key: {', '.join(report.key_columns)})",
    ]
    if report.columns_added:
        lines.append(f"Columns added:    {', '.join(report.columns_added)}")
    if report.columns_removed:
        lines.append(f"Columns removed:  {', '.join(report.columns_removed)}")
    if report.columns_reordered:
        lines.append("Column order differs between the files")
    if not report.has_differences:
        lines.append("No differences.")
    for ref in report.added:
        lines.append(f"+ {describe(ref.key)} (new line {ref.line})")
    for ref in report.removed:
        lines.append(f"- {describe(ref.key)} (old line {ref.line})")
    for change in report.changed:
        lines.append(
            f"~ {describe(change.key)} (old line {change.old_line}, new line {change.new_line})"
        )
        for cell in change.cells:
            lines.append(f'    {cell.column}: "{cell.old}" -> "{cell.new}"')
    summary = report.summary()
    lines.append(
        f"Summary: {summary['added']} added, {summary['removed']} removed, "
        f"{summary['changed']} changed, {summary['unchanged']} unchanged"
    )
    return "\n".join(lines)


def report_to_dict(report):
    data = asdict(report)
    data["has_differences"] = report.has_differences
    data["summary"] = report.summary()
    return data


def render_json(report):
    return json.dumps(report_to_dict(report), indent=2, ensure_ascii=False)


def _cell(value):
    return value.replace("|", "\\|").replace("\n", " ")


def render_markdown(report):
    summary = report.summary()
    lines = [
        f"# rowdiff: `{report.old_name}` -> `{report.new_name}`",
        "",
        f"Key: `{', '.join(report.key_columns)}`",
        "",
        "| Added | Removed | Changed | Unchanged |",
        "|---:|---:|---:|---:|",
        f"| {summary['added']} | {summary['removed']} | {summary['changed']} | {summary['unchanged']} |",
    ]
    if report.columns_added or report.columns_removed or report.columns_reordered:
        lines += ["", "## Columns"]
        if report.columns_added:
            lines.append(f"- Added: {', '.join(report.columns_added)}")
        if report.columns_removed:
            lines.append(f"- Removed: {', '.join(report.columns_removed)}")
        if report.columns_reordered:
            lines.append("- Order differs between the files")
    if report.added:
        lines += ["", "## Added", "", "| Key | New line |", "|---|---:|"]
        lines += [f"| {_cell(describe(r.key))} | {r.line} |" for r in report.added]
    if report.removed:
        lines += ["", "## Removed", "", "| Key | Old line |", "|---|---:|"]
        lines += [f"| {_cell(describe(r.key))} | {r.line} |" for r in report.removed]
    if report.changed:
        lines += ["", "## Changed", "", "| Key | Column | Old | New |", "|---|---|---|---|"]
        for change in report.changed:
            for cell in change.cells:
                lines.append(
                    f"| {_cell(describe(change.key))} | {_cell(cell.column)} "
                    f"| {_cell(cell.old)} | {_cell(cell.new)} |"
                )
    return "\n".join(lines)


RENDERERS = {"text": render_text, "json": render_json, "markdown": render_markdown}


# ---------------------------------------------------------------- CLI

def split_list(values):
    items = []
    for value in values or []:
        items.extend(part.strip() for part in value.split(",") if part.strip())
    return tuple(items)


def build_parser():
    parser = argparse.ArgumentParser(
        prog="rowdiff",
        description="Compare two CSV files by key and report added, removed and changed rows.",
    )
    parser.add_argument("old", help="the older CSV file")
    parser.add_argument("new", help="the newer CSV file")
    parser.add_argument(
        "-k", "--key", action="append",
        help="key column; repeat or comma-separate for a composite key (default: match by row position)",
    )
    parser.add_argument("--columns", action="append", help="compare only these columns (comma-separated)")
    parser.add_argument("--ignore", action="append", help="skip these columns (comma-separated)")
    parser.add_argument("--tolerance", default="0", help="largest allowed absolute difference between numbers")
    parser.add_argument("--strict-numbers", action="store_true", help="compare numbers as text, so 1.0 differs from 1")
    parser.add_argument("--ignore-case", action="store_true", help="compare values case-insensitively")
    parser.add_argument("--ignore-whitespace", action="store_true", help="trim and collapse runs of whitespace")
    parser.add_argument("--ignore-column-order", action="store_true", help="do not report column reordering")
    parser.add_argument(
        "-d", "--delimiter",
        help="field delimiter: one character or comma, semicolon, tab, pipe (default: guessed from the header)",
    )
    parser.add_argument("--encoding", default="utf-8-sig", help="input encoding (default: utf-8-sig, which strips a BOM)")
    parser.add_argument("-f", "--format", choices=sorted(RENDERERS), default="text", help="output format")
    parser.add_argument("-q", "--quiet", action="store_true", help="print nothing and use the exit code only")
    return parser


def options_from_args(args):
    try:
        tolerance = Decimal(args.tolerance)
    except InvalidOperation:
        raise ValueError(f"--tolerance must be a number, not '{args.tolerance}'") from None
    if not tolerance.is_finite() or tolerance < 0:
        raise ValueError("--tolerance must be a finite number, zero or greater")
    return Options(
        keys=split_list(args.key),
        columns=split_list(args.columns),
        ignore_columns=split_list(args.ignore),
        tolerance=tolerance,
        strict_numbers=args.strict_numbers,
        ignore_case=args.ignore_case,
        ignore_whitespace=args.ignore_whitespace,
        ignore_column_order=args.ignore_column_order,
    )


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        opts = options_from_args(args)
        delimiter = resolve_delimiter(args.delimiter)
        old = load_table(args.old, args.encoding, delimiter)
        new = load_table(args.new, args.encoding, delimiter)
        report = compare(old, new, opts)
    except RowDiffError as exc:
        for message in exc.messages:
            print(f"rowdiff: error: {message}", file=sys.stderr)
        return EXIT_ERROR
    except (OSError, ValueError, InvalidOperation) as exc:
        print(f"rowdiff: error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    if not args.quiet:
        print(RENDERERS[args.format](report))
    return EXIT_DIFFERENT if report.has_differences else EXIT_SAME


if __name__ == "__main__":
    sys.exit(main())
