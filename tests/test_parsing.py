"""The response shapes, parsed by shape rather than by index.

Getting this wrong is silent in both directions. Reading `[[id, id]]` as a flat
list of ids finds no strings at all, which reads as "the server refused to
reserve anything". Reading `[[row, row]]` that way finds no rows, which reads as
"nothing has finished rendering yet" - and a poll loop that believes that waits
out its whole timeout and reports a run that rendered nothing, with no error
anywhere.
"""

from __future__ import annotations

from pomelli_webapi.utils.parsing import (
    item_rows_of,
    item_status,
    looks_like_item_row,
    result_resid,
    strings_of,
)


def row(item_id: str, status: int, resid: str | None = None) -> list:
    config = [None] * 13
    if resid:
        config[12] = [[resid, "image/png"]]
    return ["biz", item_id, status, "2026-09-22T01:23:00Z", "2026-09-22T01:23:40Z", config]


def test_allocate_ids_comes_back_inside_one_wrapper():
    assert strings_of([["a1", "a2", "a3"]]) == ["a1", "a2", "a3"]


def test_a_flat_list_still_reads():
    assert strings_of(["a1", "a2"]) == ["a1", "a2"]


def test_empty_and_malformed_answers_are_empty_not_errors():
    assert strings_of([]) == []
    assert strings_of(None) == []
    assert strings_of([[]]) == []
    assert strings_of([0]) == []


def test_item_rows_are_found_inside_their_wrapper():
    rows = [row("i1", 3, "r1"), row("i2", 2)]
    assert len(item_rows_of([rows])) == 2


def test_a_bare_row_list_still_reads():
    rows = [row("i1", 3, "r1")]
    assert len(item_rows_of(rows)) == 1


def test_a_status_is_not_mistaken_for_a_row():
    assert not looks_like_item_row([1, 2, 3])
    assert not looks_like_item_row(["biz", "i1", "3"])
    assert looks_like_item_row(row("i1", 1))


def test_result_resid_reads_field_5_index_12():
    assert result_resid(row("i1", 3, "res-42")) == "res-42"


def test_result_resid_is_none_while_rendering_or_when_malformed():
    assert result_resid(row("i1", 2)) is None
    assert result_resid(["biz", "i1", 1]) is None
    assert result_resid([]) is None


def test_item_status_survives_a_short_row():
    assert item_status(row("i1", 2)) == 2
    assert item_status(["biz", "i1"]) is None
    assert item_status(["biz", "i1", "two"]) is None
