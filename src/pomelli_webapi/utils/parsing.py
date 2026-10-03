"""Read the response shapes this service uses, defensively.

Every answer from LabsSageService is a JSON array, and the payload is usually
wrapped one level deeper than it looks: ``AllocateIds [50]`` answers
``[[id, id, ...]]`` and ``GetBusinessItems`` answers ``[[row, row, ...]]``. So
iterating the response directly yields the wrapper, not the contents. For ids
that reads as "the server refused to reserve anything", and for item rows it
reads as "nothing has finished rendering yet" - which is the one wrong answer
that looks like patience: a poll loop waits out its whole timeout and reports a
run that rendered nothing, with no error anywhere.

Nothing here indexes by position. The rows are found by shape, so a build that
adds or drops a wrapper level still reads correctly.
"""

from __future__ import annotations

from typing import Any

# How deep to look for the payload before giving up. The service has never
# wrapped more than twice; the limit is only there to keep a malformed answer
# from looping.
_MAX_DEPTH = 4


def strings_of(payload: Any) -> list[str]:
    """Every string out of a nested answer, in order.

    ``[[a, b], [c]]`` and ``[a, b]`` both yield ``[a, b, c]``.
    """
    node = payload
    for _ in range(_MAX_DEPTH):
        if not isinstance(node, list) or not node:
            return []
        if isinstance(node[0], str):
            return [x for x in node if isinstance(x, str)]
        if isinstance(node[0], list) and len(node) == 1:
            node = node[0]
            continue
        out: list[str] = []
        for item in node:
            out.extend(strings_of(item) if isinstance(item, list) else
                       ([item] if isinstance(item, str) else []))
        return out
    return []


def looks_like_item_row(value: Any) -> bool:
    """A business item row: business id, item id, integer status, then times."""
    return (
        isinstance(value, list)
        and len(value) > 2
        and isinstance(value[0], str)
        and isinstance(value[1], str)
        and isinstance(value[2], int)
    )


def item_rows_of(payload: Any) -> list[list]:
    """The item rows out of a ``GetBusinessItems`` answer."""
    node = payload
    for _ in range(_MAX_DEPTH):
        if not isinstance(node, list) or not node:
            return []
        if looks_like_item_row(node[0]):
            return [r for r in node if looks_like_item_row(r)]
        if isinstance(node[0], list) and len(node) == 1:
            node = node[0]
            continue
        return [r for r in node if looks_like_item_row(r)]
    return []


def result_resid(row: list) -> str | None:
    """The finished resource id inside an item row, if it has one yet.

    The row is ``[business, id, status, created, updated, config]``, and the
    config carries the result at index 12 as ``[resid, mime]``. Read
    defensively: an item that is still rendering has null there, and one that
    failed may have something else entirely.
    """
    try:
        block = row[5][12]
        pair = block[0]
        if isinstance(pair, list) and isinstance(pair[0], str) and pair[0]:
            return pair[0]
    except (IndexError, TypeError):
        pass
    return None


def item_status(row: list) -> int | None:
    try:
        value = row[2]
    except (IndexError, TypeError):
        return None
    return value if isinstance(value, int) else None


__all__ = ["item_rows_of", "item_status", "looks_like_item_row", "result_resid", "strings_of"]
