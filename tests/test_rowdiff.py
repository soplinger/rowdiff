import json
from decimal import Decimal

import pytest

import rowdiff
from rowdiff import (
    Options,
    RowDiffError,
    compare,
    main,
    parse_table,
    render_json,
    render_markdown,
    render_text,
)


def diff(old_text, new_text, **option_kwargs):
    old = parse_table(old_text, name="old.csv")
    new = parse_table(new_text, name="new.csv")
    return compare(old, new, Options(**option_kwargs))


def test_identical_files_have_no_differences():
    report = diff("id,v\n1,a\n2,b\n", "id,v\n1,a\n2,b\n", keys=("id",))
    assert not report.has_differences
    assert report.unchanged == 2
    assert "No differences." in render_text(report)


def test_added_and_removed_rows_are_reported_with_line_numbers():
    report = diff("id,v\n1,a\n2,b\n", "id,v\n2,b\n3,c\n", keys=("id",))
    assert [r.key for r in report.removed] == [{"id": "1"}]
    assert report.removed[0].line == 2
    assert [r.key for r in report.added] == [{"id": "3"}]
    assert report.added[0].line == 3


def test_changed_cells_show_old_and_new_values():
    report = diff("id,name,qty\n1,Bolt,4\n", "id,name,qty\n1,Bolt,5\n", keys=("id",))
    assert len(report.changed) == 1
    change = report.changed[0]
    assert change.old_line == 2 and change.new_line == 2
    assert [(c.column, c.old, c.new) for c in change.cells] == [("qty", "4", "5")]


def test_reordered_rows_are_not_differences_when_keyed():
    report = diff("id,v\n1,a\n2,b\n", "id,v\n2,b\n1,a\n", keys=("id",))
    assert not report.has_differences
    assert report.unchanged == 2


def test_numbers_compare_by_value_by_default_and_as_text_when_strict():
    assert not diff("id,p\n1,1.0\n", "id,p\n1,1\n", keys=("id",)).has_differences
    strict = diff("id,p\n1,1.0\n", "id,p\n1,1\n", keys=("id",), strict_numbers=True)
    assert len(strict.changed) == 1


def test_leading_zeros_and_exponents_are_compared_as_text():
    assert len(diff("id,zip\n1,02134\n", "id,zip\n1,2134\n", keys=("id",)).changed) == 1
    assert len(diff("id,n\n1,1E+11\n", "id,n\n1,100000000000\n", keys=("id",)).changed) == 1
    assert len(diff("id,n\n1,1e3\n", "id,n\n1,1000\n", keys=("id",)).changed) == 1


def test_numeric_regex_accepts_plain_decimals():
    for text in ["0", "1.0", "2.250", "10.04", ".5", "-3", "+7."]:
        assert rowdiff.NUMBER_RE.fullmatch(text), text
    for text in ["02134", "007", "1e3", "1E+11", "NaN", "inf", "1,000"]:
        assert not rowdiff.NUMBER_RE.fullmatch(text), text


def test_tolerance_allows_small_numeric_drift():
    assert not diff("id,p\n1,10.00\n", "id,p\n1,10.04\n", keys=("id",), tolerance=Decimal("0.05")).has_differences
    tight = diff("id,p\n1,10.00\n", "id,p\n1,10.04\n", keys=("id",), tolerance=Decimal("0.01"))
    assert len(tight.changed) == 1


def test_ignore_case_and_whitespace_normalization():
    old = "id,name\n1,Acme  Corp\n"
    new = "id,name\n1,acme corp\n"
    assert len(diff(old, new, keys=("id",)).changed) == 1
    assert not diff(old, new, keys=("id",), ignore_case=True, ignore_whitespace=True).has_differences


def test_composite_key_matches_on_every_key_column():
    old = "region,sku,qty\nnorth,A,1\nsouth,A,2\n"
    new = "region,sku,qty\nsouth,A,2\nnorth,A,9\n"
    report = diff(old, new, keys=("region", "sku"))
    assert [c.new for c in report.changed[0].cells] == ["9"]
    assert report.changed[0].key == {"region": "north", "sku": "A"}
    assert report.unchanged == 1


def test_added_removed_and_reordered_columns_are_detected():
    report = diff("id,a,b\n1,x,y\n", "id,b,a,c\n1,y,x,z\n", keys=("id",))
    assert report.columns_added == ["c"]
    assert report.columns_removed == []
    assert report.columns_reordered is True
    assert report.has_differences
    relaxed = diff("id,a,b\n1,x,y\n", "id,b,a\n1,y,x\n", keys=("id",), ignore_column_order=True)
    assert not relaxed.has_differences


def test_ignored_and_only_columns_limit_the_comparison():
    old = "id,a,b\n1,x,y\n"
    new = "id,a,b\n1,z,q\n"
    only_b = diff(old, new, keys=("id",), columns=("b",))
    assert [c.column for c in only_b.changed[0].cells] == ["b"]
    ignore_b = diff(old, new, keys=("id",), ignore_columns=("b",))
    assert [c.column for c in ignore_b.changed[0].cells] == ["a"]


def test_positional_mode_compares_rows_by_position():
    report = diff("v\na\nb\n", "v\na\nc\nd\n")
    assert report.key_columns == ["row"]
    assert [c.new for c in report.changed[0].cells] == ["c"]
    assert [r.key for r in report.added] == [{"row": "3"}]


def test_duplicate_keys_are_reported_with_both_lines():
    with pytest.raises(RowDiffError) as exc:
        diff("id,v\n1,a\n1,b\n", "id,v\n1,a\n", keys=("id",))
    assert "old.csv:3" in exc.value.messages[0]
    assert "first seen on line 2" in exc.value.messages[0]


def test_empty_key_value_is_an_error_not_a_silent_match():
    with pytest.raises(RowDiffError) as exc:
        diff("id,v\n,a\n", "id,v\n,a\n", keys=("id",))
    assert "empty value in key column 'id'" in exc.value.messages[0]


def test_missing_key_or_ignored_key_is_an_error():
    with pytest.raises(RowDiffError, match="key column 'sku' not found"):
        diff("id,v\n1,a\n", "id,v\n1,a\n", keys=("sku",))
    with pytest.raises(RowDiffError, match="cannot ignore key column 'id'"):
        diff("id,v\n1,a\n", "id,v\n1,a\n", keys=("id",), ignore_columns=("id",))


def test_ragged_rows_report_the_physical_line_including_blank_lines():
    with pytest.raises(RowDiffError) as exc:
        parse_table("id,v\n\n1,a\n2\n", name="x.csv")
    assert exc.value.messages == ["x.csv:4: expected 2 fields, found 1"]


def test_empty_file_and_duplicate_header_are_errors():
    with pytest.raises(RowDiffError, match="empty file"):
        parse_table("", name="e.csv")
    with pytest.raises(RowDiffError, match="duplicate column 'id'"):
        parse_table("id,id\n1,2\n", name="d.csv")


def test_bom_crlf_and_quoted_fields_with_commas_and_newlines():
    text = '﻿id,note\r\n1,"hello, world"\r\n2,"two\nlines"\r\n'
    table = parse_table(text, name="q.csv")
    assert table.header == ["id", "note"]
    assert table.rows[0].values["note"] == "hello, world"
    assert table.rows[1].values["note"] == "two\nlines"
    assert table.rows[1].line == 3


def test_delimiter_is_guessed_from_the_header():
    table = parse_table("a;b;c\n1;2;3\n", name="s.csv")
    assert table.delimiter == ";"
    assert table.rows[0].values == {"a": "1", "b": "2", "c": "3"}
    assert rowdiff.resolve_delimiter("tab") == "\t"
    with pytest.raises(ValueError):
        rowdiff.resolve_delimiter("ab")


def test_json_output_round_trips_with_summary():
    report = diff("id,v\n1,a\n2,b\n", "id,v\n2,c\n3,d\n", keys=("id",))
    data = json.loads(render_json(report))
    assert data["has_differences"] is True
    assert data["summary"] == {
        "added": 1, "removed": 1, "changed": 1, "unchanged": 0,
        "columns_added": 0, "columns_removed": 0,
    }


def test_markdown_escapes_pipes_in_values():
    report = diff("id,v\n1,a\n", "id,v\n1,a|b\n", keys=("id",))
    text = render_markdown(report)
    assert "| id=1 | v | a | a\\|b |" in text


def test_cli_exit_codes_and_quiet_mode(tmp_path, capsys):
    old = tmp_path / "old.csv"
    new = tmp_path / "new.csv"
    bad = tmp_path / "bad.csv"
    old.write_text("id,v\n1,a\n", encoding="utf-8")
    new.write_text("id,v\n1,a\n", encoding="utf-8")
    bad.write_text("id,v\n1\n", encoding="utf-8")

    assert main([str(old), str(new), "--key", "id"]) == 0
    capsys.readouterr()

    new.write_text("id,v\n1,b\n", encoding="utf-8")
    assert main([str(old), str(new), "-k", "id", "-q"]) == 1
    assert capsys.readouterr().out == ""

    assert main([str(old), str(bad), "-k", "id"]) == 2
    assert "bad.csv:2: expected 2 fields" in capsys.readouterr().err

    assert main([str(old), str(new), "--tolerance", "-1"]) == 2
    assert main([str(old), str(tmp_path / "missing.csv")]) == 2
