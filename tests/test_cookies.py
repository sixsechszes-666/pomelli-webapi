"""The cookie scoping rules, which are the whole reason this library works.

A signed-in Google profile holds three copies of every authentication cookie -
one each for .google.com, .google.ru and .youtube.com. Collapsing that jar into
a {name: value} dict keeps whichever copy came last, and Google then answers
SESSION_COOKIE_INVALID naming the cookie, which reads as an expired session and
sends you to a browser login you never needed.
"""

from __future__ import annotations

import json

import pytest

from pomelli_webapi import AuthError, Cookies
from pomelli_webapi.utils.cookies import domain_matches, path_matches, specificity


def three_domains() -> list[dict]:
    return [
        {"name": "HSID", "value": "for-google-com", "domain": ".google.com", "path": "/"},
        {"name": "HSID", "value": "for-google-ru", "domain": ".google.ru", "path": "/"},
        {"name": "HSID", "value": "for-youtube", "domain": ".youtube.com", "path": "/"},
        {"name": "SAPISID", "value": "sapisid", "domain": ".google.com", "path": "/"},
        {"name": "__Secure-1PAPISID", "value": "p1", "domain": ".google.com", "path": "/"},
        {"name": "__Secure-3PAPISID", "value": "p3", "domain": ".google.com", "path": "/"},
    ]


def test_picks_the_copy_for_the_host_it_is_sent_to():
    jar = Cookies(three_domains())
    assert jar.scoped("labssage-pa.clients6.google.com")["HSID"] == "for-google-com"
    assert jar.scoped("www.youtube.com")["HSID"] == "for-youtube"


def test_a_google_ru_cookie_is_never_sent_to_a_google_com_host():
    jar = Cookies(three_domains())
    assert "for-google-ru" not in jar.header("labs.google.com")


def test_host_only_cookie_beats_a_domain_cookie_of_the_same_name():
    jar = Cookies([
        {"name": "NID", "value": "broad", "domain": ".google.com", "path": "/"},
        {"name": "NID", "value": "exact", "domain": "labs.google.com", "path": "/"},
    ])
    assert jar.scoped("labs.google.com")["NID"] == "exact"


def test_a_narrower_path_wins():
    jar = Cookies([
        {"name": "T", "value": "root", "domain": ".google.com", "path": "/"},
        {"name": "T", "value": "rpc", "domain": ".google.com", "path": "/$rpc"},
    ])
    assert jar.scoped("labs.google.com", "/$rpc/Service/Method")["T"] == "rpc"
    assert jar.scoped("labs.google.com", "/other")["T"] == "root"


def test_specificity_orders_path_then_host_only_then_longer_domain():
    root = {"name": "x", "domain": ".google.com", "path": "/"}
    narrow = {"name": "x", "domain": ".google.com", "path": "/$rpc"}
    host_only = {"name": "x", "domain": "a.google.com", "path": "/"}
    assert sorted([root, narrow], key=specificity)[0] is narrow
    assert sorted([root, host_only], key=specificity)[0] is host_only


def test_matching_helpers():
    assert domain_matches("a.google.com", ".google.com")
    assert domain_matches("google.com", ".google.com")
    assert not domain_matches("notgoogle.com", ".google.com")
    assert not domain_matches("a.google.ru", ".google.com")
    assert domain_matches("anything", "")
    assert path_matches("/a/b", "/a")
    assert path_matches("/a", "")
    assert not path_matches("/b", "/a")


def test_plain_mapping_is_kept_as_records_and_sent_everywhere():
    jar = Cookies({"SAPISID": "s", "__Secure-1PAPISID": "p"})
    assert len(jar) == 2
    assert jar.scoped("labssage-pa.clients6.google.com")["SAPISID"] == "s"
    assert jar.header("www.gstatic.com")


def test_from_file_unwraps_a_saved_session_object(tmp_path):
    path = tmp_path / "session.json"
    path.write_text(json.dumps({"cookies": three_domains(), "url": "x"}), encoding="utf-8")
    jar = Cookies.from_file(path)
    assert len(jar) == 6
    assert jar.scoped("labs.google.com")["HSID"] == "for-google-com"


def test_check_names_what_is_missing():
    with pytest.raises(AuthError) as empty:
        Cookies().check(["SAPISID"])
    assert "no cookies supplied" in str(empty.value)

    with pytest.raises(AuthError) as wrong:
        Cookies({"SID": "x"}).check(["SAPISID", "__Secure-1PAPISID"])
    assert "SID" in str(wrong.value)

    Cookies({"SAPISID": "x"}).check(["SAPISID", "__Secure-1PAPISID"])


def test_header_is_semicolon_separated_and_absent_when_empty():
    assert Cookies().header("labs.google.com") == ""
    header = Cookies({"A": "1", "B": "2"}).header("labs.google.com")
    assert "A=1" in header and "B=2" in header and "; " in header
