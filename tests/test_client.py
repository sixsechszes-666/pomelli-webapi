"""The client, exercised end to end against a fake transport.

No session, no proxy, no network. Everything this library does is one of two
calls - an RPC POST or a plain request - so a transport that answers by URL
covers the whole flow: upload, queue, poll, download.

The tests are synchronous and drive the coroutines with ``asyncio.run``, so the
package needs nothing beyond pytest to be tested.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json

import pytest

from pomelli_webapi import (
    APIError,
    AuthError,
    Cookies,
    PomelliClient,
    QuotaExceededError,
    RenderError,
    SageClient,
    SageError,
    SessionExpiredError,
)
from pomelli_webapi.constants import API_KEY, ORIGIN

from .fakes import FakeResponse, FakeSession, rpc_handler

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

JAR = Cookies({
    "SAPISID": "sapisid-value",
    "__Secure-1PAPISID": "sapisid-value",
    "__Secure-3PAPISID": "sapisid-value",
    "SID": "sid-value",
})


def run(coro):
    return asyncio.run(coro)


def item_row(item_id: str, status: int, resid: str | None = None) -> list:
    """A GetBusinessItems row with the result where the server puts it."""
    config = [None] * 13
    if resid:
        config[12] = [[resid, "image/png"]]
    return ["biz", item_id, status, "2026-09-22T01:23:00Z", "2026-09-22T01:23:40Z", config]


def make_session(extra: dict | None = None) -> FakeSession:
    """Routes for the RPC methods, plus the two non-RPC endpoints by URL."""
    routes = dict(extra or {})

    def handler(url: str, method: str, data, headers: dict):
        if "pomelli_uploads/resource" in url:
            return FakeResponse(
                200, None, text="",
                headers={"x-goog-upload-url": "https://upload.example/session/xyz"},
            )
        if url.startswith("https://upload.example/"):
            return FakeResponse(200, {"id": "resid-up", "mimeType": "image/png"})
        if "pomelli_downloads/accounts" in url:
            return FakeResponse(200, None, content=PNG)
        return None

    base = rpc_handler(routes)
    return FakeSession(
        lambda url, method, data, headers: handler(url, method, data, headers)
        or base(url, method, data, headers)
    )


def make_client(session: FakeSession, tmp_path) -> PomelliClient:
    return PomelliClient(
        JAR, "direct", session=session, style_cache=tmp_path / "styles", poll=0.01,
    )


@pytest.fixture
def photo(tmp_path):
    path = tmp_path / "photo.jpg"
    path.write_bytes(PNG)
    return path


# ---------------------------------------------------------------------- headers


def test_headers_are_the_ones_the_page_sends(tmp_path):
    client = make_client(FakeSession(), tmp_path)
    headers = client.sage.headers()

    assert headers["content-type"] == "application/json+protobuf"
    assert headers["x-goog-api-key"] == API_KEY
    assert headers["x-goog-authuser"] == "0"
    assert headers["x-user-agent"] == "grpc-web-javascript/0.1"
    assert headers["origin"] == ORIGIN
    assert headers["referer"] == ORIGIN + "/"

    # Three labels, one timestamp: a browser emits exactly this on a normal
    # account, where all three source cookies carry the same value.
    tokens = headers["authorization"].split(" ")
    labels, values = tokens[0::2], tokens[1::2]
    assert labels == ["SAPISIDHASH", "SAPISID1PHASH", "SAPISID3PHASH"]
    assert len(values) == 3

    stamps, digests = set(), []
    for value in values:
        ts, _, digest = value.partition("_")
        stamps.add(ts)
        digests.append((ts, digest))
    assert len(stamps) == 1
    assert all(d for _, d in digests)

    # The scheme itself, which is what makes the browser disposable:
    # sha1("<ts> <cookie value> <origin>") over each of the three cookies.
    for (ts, digest), (_, cookie) in zip(digests, [
        ("SAPISIDHASH", "SAPISID"),
        ("SAPISID1PHASH", "__Secure-1PAPISID"),
        ("SAPISID3PHASH", "__Secure-3PAPISID"),
    ], strict=False):
        expected = hashlib.sha1(f"{ts} {JAR.get(cookie)} {ORIGIN}".encode()).hexdigest()
        assert digest == expected

    assert "SAPISID=sapisid-value" in headers["cookie"]
    assert "SID=sid-value" in headers["cookie"]


def test_a_label_is_dropped_when_its_cookie_is_missing_but_the_header_survives(tmp_path):
    jar = Cookies({"SAPISID": "s", "__Secure-1PAPISID": "p1"})
    client = PomelliClient(jar, "direct", session=FakeSession(), style_cache=tmp_path)
    header = client.sage.auth_header()
    assert header.count("HASH") == 2
    assert "SAPISID3PHASH" not in header


def test_a_jar_without_any_sapisid_is_refused_at_construction():
    with pytest.raises(AuthError) as caught:
        PomelliClient(Cookies({"SID": "x"}), strict=True)
    assert "SAPISID" in str(caught.value)


def test_the_same_jar_builds_when_validation_is_off():
    client = PomelliClient(Cookies({"SID": "x"}), strict=False)
    assert client.sage.auth_header() == ""


# ------------------------------------------------------------- the item config


def test_a_shot_type_is_an_id_in_slot_5_of_a_six_slot_job(tmp_path):
    from pomelli_webapi.types import Source

    session = make_session({"ListBusinesses": [["biz"]]})
    client = make_client(session, tmp_path)
    run(client.set_shot("item-1", Source("res-1", "image/png"), aspect=3, template=5))

    body = session.bodies("MutateBusinessItem")[0]
    assert body[0] == "biz" and body[1] == "item-1"

    config = body[2]
    assert len(config) == 1 and len(config[0]) == 21
    job = config[0][20]
    assert len(job) == 6, "the browser trims trailing nulls on a product-shot job"
    assert job[2] == 3
    assert job[5] == [[["res-1", "image/png"]], "biz", None, [5]]


def test_a_style_is_named_by_reference_resids_in_slot_8_of_a_nine_slot_job(tmp_path):
    from pomelli_webapi.types import Source

    session = make_session({"ListBusinesses": [["biz"]]})
    client = make_client(session, tmp_path)
    refs = [Source("ref-a", "image/png"), Source("ref-b", "image/png")]
    run(client.set_shot("item-1", Source("res-1", "image/png"), aspect=1, style=refs))

    job = session.bodies("MutateBusinessItem")[0][2][0][20]
    assert len(job) == 9, "a style job is recorded nine slots long"
    assert job[2] == 1
    assert job[8] == [
        ["res-1", "image/png"],
        [["ref-a", "image/png"], ["ref-b", "image/png"]],
        "biz",
    ]


def test_a_pick_with_neither_a_template_nor_a_style_never_reaches_the_wire(tmp_path):
    from pomelli_webapi.types import Source

    session = make_session({"ListBusinesses": [["biz"]]})
    client = make_client(session, tmp_path)
    with pytest.raises(APIError):
        run(client.set_shot("item-1", Source("res-1", "image/png")))
    assert session.method_calls("MutateBusinessItem") == []


def test_the_source_is_attached_the_way_the_recording_shows(tmp_path):
    from pomelli_webapi.types import Source

    session = make_session({"ListBusinesses": [["biz"]]})
    client = make_client(session, tmp_path)
    run(client.attach_source(Source("res-1", "image/png")))

    body = session.bodies("MutateBusiness")[0]
    assert body[0] == "biz"
    assert body[1] == [[None] * 5 + [[[["res-1", "image/png"]]]], [None] * 7 + [[["res-1"]]]]


# ------------------------------------------------------------------- the shoot


def test_a_photoshoot_renders_and_downloads_every_pick(tmp_path, photo):
    session = make_session({
        "ListBusinesses": [["biz"]],
        "GetAccount": ["acct"],
        "AllocateIds": [["it1", "it2"]],
        "CreateBusinessItem": [],
        "MutateBusinessItem": [],
        "MutateBusiness": [],
        "GetBusinessItems": [[item_row("it1", 3, "r1"), item_row("it2", 3, "r2")]],
    })
    client = make_client(session, tmp_path)

    lines: list[str] = []
    report = run(client.photoshoot(str(photo), ["Studio", "Flatlay"], on_progress=lines.append))

    assert report.ok and len(report.images) == 2
    assert [img.label for img in report.images] == ["Studio", "Flatlay"]
    assert [img.resid for img in report.images] == ["r1", "r2"]
    assert all(img.data == PNG for img in report.images)
    assert all(img.mime == "image/png" for img in report.images)
    assert report.expected == 2 and not report.quota
    assert report.stopped == "settled"

    written = report.save(tmp_path / "out")
    assert len(written) == 2
    assert written[0].read_bytes() == PNG
    assert "studio" in written[0].name
    assert str(written[0]).endswith(".png")

    # The serializable view carries sizes and hashes, never the bytes. This is
    # the trap that used to kill a finished run at the last step.
    blob = json.dumps(report.as_dict())
    assert base64.b64encode(PNG).decode() not in blob
    assert str(len(PNG)) in blob


def test_progress_lines_name_what_happened(tmp_path, photo):
    session = make_session({
        "ListBusinesses": [["biz"]],
        "GetAccount": ["acct"],
        "AllocateIds": [["it1"]],
        "CreateBusinessItem": [],
        "MutateBusinessItem": [],
        "MutateBusiness": [],
        "GetBusinessItems": [[item_row("it1", 3, "r1")]],
    })
    client = make_client(session, tmp_path)
    lines: list[str] = []
    run(client.photoshoot(str(photo), ["Studio"], on_progress=lines.append))

    joined = "\n".join(lines)
    assert "uploaded photo.jpg" in joined
    assert "queued Studio as it1" in joined
    assert "downloaded Studio" in joined


def test_the_photo_is_uploaded_once_and_reused_across_presets(tmp_path, photo):
    session = make_session({
        "ListBusinesses": [["biz"]],
        "GetAccount": ["acct"],
        "AllocateIds": [["it1", "it2"]],
        "CreateBusinessItem": [],
        "MutateBusinessItem": [],
        "MutateBusiness": [],
        "GetBusinessItems": [[item_row("it1", 3, "r1"), item_row("it2", 3, "r2")]],
    })
    client = make_client(session, tmp_path)
    reports = run(client.photoshoot_many(str(photo), [["Studio"], ["Flatlay"]]))

    assert len(reports) == 2 and all(r.ok for r in reports)
    uploads = [c for c in session.calls if "pomelli_uploads/resource" in c["url"]]
    assert len(uploads) == 1, "the second preset must reuse the first upload"


def test_an_upload_that_answers_a_sign_in_page_is_named_as_the_cookie_problem(tmp_path, photo):
    def handler(url: str, method: str, data, headers: dict):
        if "pomelli_uploads/resource" in url:
            return FakeResponse(
                200, None, text="<html>sign in</html>",
                headers={"content-type": "text/html; charset=utf-8"},
            )
        return None

    client = make_client(FakeSession(handler), tmp_path)
    with pytest.raises(RenderError) as caught:
        run(client.photoshoot(str(photo), ["Studio"]))
    assert "cookies" in str(caught.value)


# ---------------------------------------------------------------------- quota


def test_a_quota_refusal_stops_the_batch_and_keeps_what_is_already_rendering(tmp_path, photo):
    def queue(body):
        if body[1] == "it2":
            return FakeResponse(429, None, text="Resource has been exhausted")
        return FakeResponse(200, [])

    session = make_session({
        "ListBusinesses": [["biz"]],
        "GetAccount": ["acct"],
        "AllocateIds": [["it1", "it2", "it3"]],
        "CreateBusinessItem": [],
        "MutateBusinessItem": queue,
        "MutateBusiness": [],
        "GetBusinessItems": [[item_row("it1", 3, "r1")]],
    })
    client = make_client(session, tmp_path)
    report = run(client.photoshoot(str(photo), ["Studio", "Flatlay", "In Use"]))

    assert report.quota is True
    assert [q.item_id for q in report.queued] == ["it1"]
    assert len(report.refused) == 1 and report.refused[0]["item_id"] == "it2"
    assert len(report.images) == 1, "the render already paid for is still downloaded"

    # it3 is never attempted: the account is out, so asking again is pointless
    # and each attempt would spend a round trip to be told the same thing.
    assert len(session.method_calls("MutateBusinessItem")) == 2
    assert session.method_calls("CreateBusinessItem")[-1]["url"].endswith("CreateBusinessItem")
    assert len(session.method_calls("CreateBusinessItem")) == 2


def test_a_quota_refusal_with_nothing_queued_raises_and_names_the_reason(tmp_path, photo):
    session = make_session({
        "ListBusinesses": [["biz"]],
        "GetAccount": ["acct"],
        "AllocateIds": [["it1"]],
        "CreateBusinessItem": [],
        "MutateBusinessItem": FakeResponse(429, None, text="quota exhausted"),
    })
    client = make_client(session, tmp_path)
    with pytest.raises(QuotaExceededError) as caught:
        run(client.photoshoot(str(photo), ["Studio"]))
    assert "quota" in str(caught.value).lower()


def test_a_pick_that_cannot_be_queued_does_not_abandon_the_others(tmp_path, photo):
    def queue(body):
        if body[1] == "it1":
            return FakeResponse(400, [3, "bad item config"])
        return FakeResponse(200, [])

    session = make_session({
        "ListBusinesses": [["biz"]],
        "GetAccount": ["acct"],
        "AllocateIds": [["it1", "it2"]],
        "CreateBusinessItem": [],
        "MutateBusinessItem": queue,
        "MutateBusiness": [],
        "GetBusinessItems": [[item_row("it2", 3, "r2")]],
    })
    client = make_client(session, tmp_path)
    report = run(client.photoshoot(str(photo), ["Studio", "Flatlay"]))

    assert len(report.refused) == 1
    assert report.refused[0]["error"].startswith("SageError")
    assert [q.item_id for q in report.queued] == ["it2"]
    assert len(report.images) == 1


def test_when_nothing_can_be_queued_the_run_says_so(tmp_path, photo):
    session = make_session({
        "ListBusinesses": [["biz"]],
        "AllocateIds": [["it1"]],
        "CreateBusinessItem": [],
        "MutateBusinessItem": FakeResponse(400, [3, "bad item config"]),
    })
    client = make_client(session, tmp_path)
    with pytest.raises(RenderError) as caught:
        run(client.photoshoot(str(photo), ["Studio"]))
    assert "nothing could be queued" in str(caught.value)


def test_an_item_that_lands_without_a_result_is_reported_as_failed_not_dropped(tmp_path, photo):
    session = make_session({
        "ListBusinesses": [["biz"]],
        "GetAccount": ["acct"],
        "AllocateIds": [["it1"]],
        "CreateBusinessItem": [],
        "MutateBusinessItem": [],
        "MutateBusiness": [],
        "GetBusinessItems": [[item_row("it1", 7)]],   # finished badly
    })
    client = make_client(session, tmp_path)
    report = run(client.photoshoot(str(photo), ["Studio"]))

    assert report.images == []
    assert len(report.failed) == 1 and report.failed[0].item_id == "it1"
    assert "did not finish" in report.failed[0].error
    assert not report.ok


# --------------------------------------------------------- error classification


def test_a_marker_in_the_body_is_what_proves_the_session_died():
    session = FakeSession(lambda *a: FakeResponse(
        401, [16, "SESSION_COOKIE_INVALID: cookie HSID"], headers={},
    ))
    client = SageClient(JAR, proxy="direct", session=session, retries=0)
    with pytest.raises(SessionExpiredError) as caught:
        run(client.call("GetLimitInfo", []))
    assert "SESSION_COOKIE_INVALID" in str(caught.value)
    assert caught.value.skew is None


def test_a_bare_401_is_not_taken_as_a_dead_session():
    session = FakeSession(lambda *a: FakeResponse(
        401, [16, "Expected OAuth 2 access token"], headers={},
    ))
    client = SageClient(JAR, proxy="direct", session=session, retries=0)
    with pytest.raises(SageError) as caught:
        run(client.call("GetInstagramProfile", []))
    assert not isinstance(caught.value, SessionExpiredError)
    assert caught.value.status == 401


def test_a_429_is_a_quota_refusal_and_is_not_retried_by_the_transport():
    session = FakeSession(lambda *a: FakeResponse(429, None, text="exhausted"))
    client = SageClient(JAR, proxy="direct", session=session, retries=3)
    with pytest.raises(QuotaExceededError) as caught:
        run(client.call("MutateBusinessItem", []))
    assert caught.value.status == 429
    assert not caught.value.retryable
    # One attempt: the batch needs to see the refusal to decide what to keep,
    # and four attempts with backoff per pick would only delay that.
    assert len(session.calls) == 1


def test_a_400_is_an_answer_not_a_hiccup_so_it_is_never_retried():
    session = FakeSession(lambda *a: FakeResponse(400, [3, "bad params"]))
    client = SageClient(JAR, proxy="direct", session=session, retries=3)
    with pytest.raises(SageError):
        run(client.call("GetBusinessItems", []))
    assert len(session.calls) == 1


def test_a_transient_status_is_retried_and_then_succeeds():
    seen: list[int] = []

    def flaky(*args):
        seen.append(1)
        if len(seen) == 1:
            return FakeResponse(503, "unavailable")
        return FakeResponse(200, [["biz"]])

    client = SageClient(
        JAR, proxy="direct", session=FakeSession(flaky), retries=2, backoff=0.0,
    )
    assert run(client.list_businesses()) == [["biz"]]
    assert len(seen) == 2


def test_a_transport_failure_becomes_a_network_error_after_the_last_attempt():
    def boom(*args):
        raise OSError("connection reset by peer")

    client = SageClient(
        JAR, proxy="direct", session=FakeSession(boom), retries=1, backoff=0.0,
    )
    with pytest.raises(SageError) as caught:
        run(client.call("GetLimitInfo", []))
    assert caught.value.status == 0
    assert "connection reset" in str(caught.value)
