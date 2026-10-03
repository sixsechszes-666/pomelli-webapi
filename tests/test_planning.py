"""Resolving a name a human typed into an id the API wants."""

from __future__ import annotations

import pytest

from pomelli_webapi import MAX_PICKS, APIError
from pomelli_webapi.utils.planning import (
    aspect_id,
    aspect_label,
    make_plan,
    mime_for,
    style_id,
    template_id,
)


def test_shot_types_resolve_by_name_and_by_shape_of_name():
    assert template_id("Studio") == 5
    assert template_id("studio") == 5
    assert template_id("  Model Try On ") == 1
    assert template_id("flat-lay") == 9
    assert template_id("Amazon Product Image") == 12


def test_a_name_that_is_not_a_shot_type_is_not_guessed():
    assert template_id("Blooms") == 0
    assert template_id("") == 0


def test_styles_resolve_by_id_label_and_alias():
    assert style_id("curated-blooms") == "curated-blooms"
    assert style_id("Floral Whimsy") == "curated-blooms"
    assert style_id("floral whimsy") == "curated-blooms"
    assert style_id("Mangacore") == "curated-manga"
    assert style_id("manga") == "curated-manga"


def test_a_shot_type_is_never_swallowed_by_a_style_containing_it():
    # "In Use" is a product shot; a loose substring rule would lose it.
    assert style_id("In Use") == ""
    assert template_id("In Use") == 2


def test_plan_keeps_the_kind_with_the_name():
    plans = make_plan(["Studio", "Floral Whimsy"])
    assert [(p.kind, p.template, p.style) for p in plans] == [
        ("template", 5, ""), ("style", 0, "curated-blooms"),
    ]
    assert plans[1].is_style


def test_unknown_name_and_too_many_picks_raise_before_anything_is_sent():
    with pytest.raises(APIError) as unknown:
        make_plan(["Studio", "Nonsense"])
    assert "Nonsense" in str(unknown.value)

    with pytest.raises(APIError) as empty:
        make_plan([])
    assert "at least one" in str(empty.value)

    with pytest.raises(APIError) as many:
        make_plan(["Studio"] * (MAX_PICKS + 1))
    assert str(MAX_PICKS) in str(many.value)


def test_aspect_names_and_ids():
    assert aspect_id("square") == 3
    assert aspect_id("Square (1:1)") == 3
    assert aspect_id("story") == 1
    assert aspect_id("9:16") == 1
    assert aspect_id("feed") == 2
    assert aspect_id(2) == 2
    assert aspect_id(None) == 3
    assert aspect_id("nonsense") == 3
    assert aspect_label(1) == "Story (9:16)"


def test_mime_defaults_to_png_like_the_app_does():
    assert mime_for("photo.jpg") == "image/jpeg"
    assert mime_for("PHOTO.JPEG") == "image/jpeg"
    assert mime_for("photo.webp") == "image/webp"
    assert mime_for("photo") == "image/png"
