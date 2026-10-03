"""Turn the names a human types into the ids the API wants.

Step 2 of the wizard shows two kinds of card in one grid under one counter:
product shots, which are named by a small integer, and Style Inspirations, which
are not named at all - a style is identified by the resource ids of its own
reference images. So a name is resolved against both tables and the kind travels
with it, rather than making the caller know which section a name came from.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from ..constants import (
    ASPECT_IDS,
    DEFAULT_ASPECT,
    MAX_PICKS,
    MIME_TYPES,
    STYLE_ALIASES,
    STYLES,
    TEMPLATE_IDS,
    TEMPLATE_IDS_EXTRA,
    normalize,
)
from ..exceptions import APIError
from ..types.photoshoot import Plan


def _squash(text: str) -> str:
    """A label with every separator removed, for `flat-lay` vs `Flatlay`."""
    return normalize(text).replace(" ", "")


def style_id(label: str) -> str:
    """The curated style id for a label, or ``""`` if this is not a style.

    A caller may name a style by its id or by the label the picker shows, so
    both resolve. The id is checked raw before anything is normalized, because
    normalizing turns ``curated-blooms`` into ``curated blooms``.
    """
    raw = (label or "").strip()
    if raw in STYLES:
        return raw
    key = normalize(raw)
    for known in STYLES:
        if normalize(known) == key:
            return known
    if key in STYLE_ALIASES:
        return STYLE_ALIASES[key]
    squashed = _squash(raw)
    for known, body in STYLES.items():
        if _squash(body["label"]) == squashed:
            return known
    for alias, known in STYLE_ALIASES.items():
        if _squash(alias) == squashed:
            return known
    # Substring, last resort, and only when unambiguous - "In Use" is a product
    # shot and must not be swallowed by a style that contains it.
    hits = [k for k, body in STYLES.items() if key and key in normalize(body["label"])]
    return hits[0] if len(hits) == 1 else ""


def template_id(label: str) -> int:
    """The shot type id for a template label, or ``0`` if this is not a template."""
    key = normalize(label)
    table = {**TEMPLATE_IDS, **TEMPLATE_IDS_EXTRA}
    for name, tid in table.items():
        if normalize(name) == key:
            return tid
    squashed = _squash(label)
    for name, tid in table.items():
        if _squash(name) == squashed:
            return tid
    hits = [tid for name, tid in table.items() if key and key in normalize(name)]
    return hits[0] if len(hits) == 1 else 0


def aspect_id(name: str | int | None) -> int:
    """Resolve an aspect ratio name or number to the id the config carries."""
    if isinstance(name, int):
        return name
    if not name:
        return DEFAULT_ASPECT
    return ASPECT_IDS.get(normalize(str(name)), DEFAULT_ASPECT)


def aspect_label(value: int) -> str:
    from ..constants import ASPECT_LABELS

    return ASPECT_LABELS.get(value, str(value))


def mime_for(name: str) -> str:
    """Content type for a filename, defaulting to PNG the way the app does."""
    return MIME_TYPES.get(Path(name).suffix.lower(), "image/png")


def make_plan(names: Sequence[str]) -> list[Plan]:
    """Resolve pick names into render jobs.

    Raises
    ------
    `APIError`
        For a name that is neither a known shot type nor a known style, and for
        a list longer than the six picks a photoshoot holds.

    """
    out: list[Plan] = []
    for raw in names:
        sid = style_id(raw)
        if sid:
            out.append(Plan(label=raw, kind="style", style=sid))
            continue
        tid = template_id(raw)
        if tid:
            out.append(Plan(label=raw, kind="template", template=tid))
            continue
        raise APIError(
            f"unknown template or style {raw!r}. product shots: "
            f"{', '.join(TEMPLATE_IDS)}. styles: "
            f"{', '.join(body['label'] for body in STYLES.values())}"
        )
    if not out:
        raise APIError("no picks given: a photoshoot needs at least one")
    if len(out) > MAX_PICKS:
        raise APIError(
            f"{len(out)} picks; Pomelli allows {MAX_PICKS} per photoshoot"
        )
    return out


def templates() -> dict[str, int]:
    """Every shot type name, mapped to its id."""
    return {**TEMPLATE_IDS, **TEMPLATE_IDS_EXTRA}


def styles() -> dict[str, str]:
    """Every curated style id, mapped to the label the picker shows."""
    return {sid: body["label"] for sid, body in STYLES.items()}


__all__ = [
    "aspect_id", "aspect_label", "make_plan", "mime_for", "style_id", "styles",
    "template_id", "templates",
]
