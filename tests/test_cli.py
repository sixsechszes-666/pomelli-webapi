"""Argument parsing, which is fiddlier than it looks.

The flags that are not tied to a subcommand are declared twice, once on the main
parser and once on each subcommand, so that the natural word orders all work.
The trap that makes that non-obvious: a subparser re-applies its own defaults
after the main parser has already parsed, so an ordinary shared flag would be
reset to None the moment the subcommand was reached. The defaults on the second
copy are suppressed for exactly that reason, and these tests hold it in place.
"""

from __future__ import annotations

import pytest

from pomelli_webapi.cli import build_parser


def parse(*argv):
    return build_parser().parse_args(list(argv))


def test_the_jar_can_be_named_before_or_after_the_subcommand():
    before = parse("--cookies", "jar.json", "ping")
    after = parse("ping", "--cookies", "jar.json")
    assert before.cookies == after.cookies == "jar.json"


def test_a_flag_set_before_the_subcommand_is_not_wiped_by_it():
    args = parse("--proxy", "http://p:1", "ping")
    assert args.proxy == "http://p:1"
    assert args.no_proxy is None or args.no_proxy is False


def test_a_flag_set_after_the_subcommand_is_read():
    assert parse("ping", "--proxy", "http://p:1").proxy == "http://p:1"
    assert parse("ping", "--no-proxy").no_proxy is True
    assert parse("ping", "--browser").browser is True


def test_the_later_flag_wins_when_both_are_given():
    assert parse("--proxy", "http://a:1", "ping", "--proxy", "http://b:2").proxy == "http://b:2"


def test_the_jar_source_defaults_to_unset_so_the_browser_is_searched():
    args = parse("ping")
    assert args.cookies is None
    assert not args.browser
    assert not args.no_proxy
    assert args.proxy is None
    assert not args.verbose


def test_verbose_works_on_both_sides_of_the_subcommand():
    assert parse("-v", "ping").verbose is True
    assert parse("ping", "-v").verbose is True


def test_render_takes_the_picks_wherever_the_flags_are():
    args = parse("--cookies", "jar.json", "render", "photo.jpg",
                 "-p", "Studio", "-p", "Flatlay", "-o", "out", "-a", "square")
    assert args.image == "photo.jpg"
    assert args.pick == ["Studio", "Flatlay"]
    assert args.out == "out" and args.aspect == "square"
    assert args.cookies == "jar.json"


def test_a_subcommand_is_required():
    with pytest.raises(SystemExit):
        parse()
