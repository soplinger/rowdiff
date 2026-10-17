# rowdiff

**Key-aware CSV diff for the terminal. Tells you which rows were added, removed or changed, and exactly which cells moved.**

`diff` compares lines, so one reordered or re-quoted row floods the output. `rowdiff` matches rows by a key column instead, so you see the real changes: a price that moved, a stock count that fell to zero, a row that disappeared. It runs offline on the Python 3 standard library, with no dependencies beyond pytest for the tests.

## Features

- **Key matching**: one key column, or a composite key with `-k region,sku`.
- **Positional mode**: with no key, rows are matched by position.
- **Added, removed and changed rows**, each with its line number in both files.
- **Per-cell changes**: every changed value is shown as `old -> new`.
- **Column changes**: added and removed columns are reported, and so is column reordering.
- **Numeric comparison**: `1.0` equals `1` and `2.250` equals `2.25`. Leading zeros and exponents are compared as text, so `02134` and `1E+11` are never treated as equal to `2134` or `100000000000`. Use `--strict-numbers` to compare every value as text.
- **Tolerance**: `--tolerance 0.05` accepts small numeric drift.
- **Normalization**: `--ignore-case` and `--ignore-whitespace`.
- **Column selection**: `--columns` compares only the listed columns, and `--ignore` skips them.
- **Order-insensitive mode**: `--ignore-column-order` stops reordering from counting as a change.
- **Strict input checks**: duplicate keys, empty key values, ragged rows, duplicate headers and empty files are all reported with file and line number. Nothing is matched silently.
- **Delimiter guessing** between comma, semicolon, tab and pipe, or set explicitly with `-d`.
- **UTF-8 BOM and CRLF handling**, and quoted fields that contain commas or newlines.
- **Three output formats**: text, JSON (stable keys for scripts) and Markdown (pipes escaped, ready for a PR comment).
- **CI-friendly exit codes**: `0` no differences, `1` differences, `2` error. `-q` prints nothing.

## Quick start

With the sample files in `examples/`:

```console
$ python3 rowdiff.py examples/old.csv examples/new.csv --key sku
rowdiff: old.csv -> new.csv  (key: sku)
Columns added:    category
+ sku=E500 (new line 5)
- sku=B200 (old line 3)
~ sku=A100 (old line 2, new line 2)
    price: "4.50" -> "4.75"
~ sku=C300 (old line 4, new line 3)
    stock: "0" -> "5"
Summary: 1 added, 1 removed, 2 changed, 1 unchanged
```

`D400` is not reported: its price changed from `2.25` to `2.250`, which is the same number. `category` is new, so it is listed as an added column and not compared cell by cell.

## Requirements

Python 3.9 or newer. Nothing needs to be installed to run the tool. The tests need `pytest`.

## How to run

```console
python3 rowdiff.py OLD.csv NEW.csv [--key COL ...] [options]
```

Common options:

| Option | Meaning |
|---|---|
| `-k, --key COL` | Key column. Repeat it or comma-separate for a composite key. |
| `--columns A,B` | Compare only these columns. |
| `--ignore A,B` | Skip these columns. |
| `--tolerance N` | Largest allowed absolute numeric difference (default `0`). |
| `--strict-numbers` | Compare numbers as text. |
| `--ignore-case`, `--ignore-whitespace` | Normalize values before comparing. |
| `--ignore-column-order` | Do not report column reordering. |
| `-d, --delimiter D` | One character, or `comma`, `semicolon`, `tab`, `pipe`. |
| `--encoding E` | Input encoding (default `utf-8-sig`). |
| `-f, --format {text,json,markdown}` | Output format. |
| `-q, --quiet` | Print nothing; use the exit code. |

Examples:

```console
# Composite key, ignore the last-modified column, machine-readable output
python3 rowdiff.py before.csv after.csv -k region,sku --ignore updated_at -f json

# Compare only prices, accepting a cent of drift. Exit 1 still fires on any added
# or removed row, or on added or removed columns, because those always count as differences.
python3 rowdiff.py prices_old.csv prices_new.csv -k sku --columns price --tolerance 0.01 -q
echo $?
```

## How to test

```console
python3 -m pytest tests
```

The tests cover key matching, numeric rules, tolerance, normalization, column changes, positional mode, every input error with its line number, BOM and CRLF handling, the three output formats and the CLI exit codes.

## Project layout

```
rowdiff.py              the tool: parsing, comparison, rendering and CLI in one file
examples/old.csv        sample "before" file
examples/new.csv        sample "after" file
tests/conftest.py       puts the project root on the import path
tests/test_rowdiff.py   the test suite
```

## Notes on matching

- Header names are trimmed. Values are not trimmed unless `--ignore-whitespace` is given.
- Key columns are matched after normalization, so `--ignore-case` also applies to keys.
- Row order never matters when a key is used.
- Line numbers count blank lines and the physical lines inside quoted fields.
