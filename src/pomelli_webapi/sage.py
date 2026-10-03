"""Talk to the LabsSageService RPC directly, with no browser in the loop.

Pomelli is not a batchexecute app. It talks to

    google.internal.labs.sage.v1.LabsSageService

over the plain clients6 RPC style: one POST per method, a JSON array in, a JSON
array out. No ``)]}'`` prefix, no byte-count chunking, no double-wrapped
``wrb.fr`` payload, and no ``at`` / ``bl`` / ``f.sid`` tokens, because none of
those exist outside the Boq/WIZ framework.

What it does need, and what the page sends on every call:

    content-type    application/json+protobuf   (plain application/json gets a 404)
    x-goog-api-key  a public clients6 key, injected at runtime
    authorization   SAPISIDHASH / SAPISID1PHASH / SAPISID3PHASH, three
                    timestamped SHA-1 hashes in one header
    x-goog-authuser 0
    x-user-agent    grpc-web-javascript/0.1
    origin          https://labs.google.com

The hashes are the classic scheme, ``sha1("<ts> <cookie value> <origin>")`` over
SAPISID, __Secure-1PAPISID and __Secure-3PAPISID under one timestamp. Nothing is
signed with a secret, so the whole thing is reproducible from cookies alone and
the browser becomes disposable.

Because the authorization header carries a local timestamp, a clock that is off
by minutes is a hard failure that looks exactly like a dead session, which is why
:class:`~pomelli_webapi.exceptions.SessionExpiredError` measures the skew against
the response's Date header and says so.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import random
import time
from collections.abc import Iterable, Sequence
from email.utils import parsedate_to_datetime
from typing import Any

from curl_cffi import requests

# curl_cffi renamed the base of its exception tree between releases: everything
# it raises, timeouts and proxy failures included, descends from
# RequestException in 0.15 and from RequestsError before that. curl_cffi also
# hangs its whole tree off OSError, so catching OSError is the version-proof net.
try:
    from curl_cffi.requests.exceptions import RequestException as _CurlError
except ImportError:  # pragma: no cover - curl_cffi < 0.6
    from curl_cffi.requests.exceptions import RequestsError as _CurlError

from .constants import (
    API_KEY,
    AUTH_COOKIE_LABELS,
    AUTH_MARKERS,
    GEN_METHODS,
    GEN_TIMEOUT,
    METHODS,
    ORIGIN,
    RETRY_STATUS,
    RPC_USER_AGENT,
    SAGE_HOST,
    SAGE_SERVICE,
    SESSION_COOKIE_NAMES,
    is_readonly,
)
from .exceptions import (
    NetworkError,
    QuotaExceededError,
    SageError,
    SessionExpiredError,
)
from .utils.cookies import as_cookies
from .utils.logger import logger

PROXY_ENV = "POMELLI_PROXY"


def default_proxies(proxy: str | None = None) -> dict[str, str]:
    """Turn a proxy setting into the mapping curl_cffi wants.

    Order: the explicit argument, then ``POMELLI_PROXY``, then nothing. An empty
    value, or one of ``none`` / ``direct`` / ``off``, means go direct - which is
    the escape hatch for a machine that can reach Google without one.
    """
    value = proxy if proxy is not None else os.environ.get(PROXY_ENV, "")
    if not value or value.strip().lower() in ("none", "direct", "off"):
        return {}
    value = value.strip()
    return {"http": value, "https": value}


def _hash(ts: int, value: str, origin: str = ORIGIN) -> str:
    return hashlib.sha1(f"{ts} {value} {origin}".encode()).hexdigest()


def _server_skew(response) -> int | None:
    """How far this machine's clock is from the Date header, in seconds."""
    raw = response.headers.get("date") if hasattr(response, "headers") else None
    if not raw:
        return None
    try:
        return round(time.time() - parsedate_to_datetime(raw).timestamp())
    except Exception:
        return None


def _retry_after(response) -> float | None:
    raw = response.headers.get("retry-after") if hasattr(response, "headers") else None
    if not raw:
        return None
    try:
        return max(0.0, min(float(raw), 60.0))
    except ValueError:
        return None


def _decode(response) -> Any:
    text = (response.text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _payload(response) -> Any:
    try:
        return response.json()
    except Exception:
        return (response.text or "")[:400]


class SageClient:
    """One async HTTP client against LabsSageService.

    Holds a single ``curl_cffi.AsyncSession``, so the TLS handshake and the
    connection are paid for once instead of on every call. That matters when a
    survey walks 40-odd methods back to back.

    Parameters
    ----------
    cookies : `Cookies | dict | list | CookieJar | None`
        The signed-in jar. A list of records from a browser export is the safe
        form; a plain ``{name: value}`` mapping works when you copied SAPISID
        and friends by hand.
    proxy : `str`, optional
        Proxy URL. Falls back to ``POMELLI_PROXY``, then to no proxy.
    api_key : `str`, optional
        The clients6 key. The default is the one the app itself ships, so it
        only needs overriding if Google rotates it.
    impersonate : `str`, optional
        TLS fingerprint to present. curl_cffi's default here is ``chrome``.
    authuser : `int`, optional
        Which signed-in Google account to act as. The app uses 0.
    timeout : `float`, optional
        Per-call timeout for reads, in seconds. Generations get `GEN_TIMEOUT`.
    retries : `int`, optional
        Extra attempts for transient statuses and transport failures.
    strict : `bool`, optional
        Validate the jar on construction. Set ``False`` to build a client around
        a jar you intend to fix later.

    """

    def __init__(
        self,
        cookies: Any = None,
        *,
        proxy: str | None = None,
        api_key: str = API_KEY,
        impersonate: str = "chrome",
        authuser: int = 0,
        timeout: float = 60.0,
        retries: int = 3,
        backoff: float = 0.6,
        session: Any = None,
        strict: bool = True,
    ):
        self.cookies = as_cookies(cookies)
        self.api_key = api_key
        self.impersonate = impersonate
        self.authuser = authuser
        self.timeout = timeout
        self.retries = max(0, int(retries))
        self.backoff = backoff
        self.proxies = default_proxies(proxy)
        self._session = session
        self._owns_session = session is None

        if strict:
            self.cookies.check([name for _, name in AUTH_COOKIE_LABELS])

    # ------------------------------------------------------------- lifecycle

    @property
    def session(self):
        """The underlying AsyncSession, created on first use."""
        if self._session is None:
            self._session = requests.AsyncSession()
        return self._session

    async def close(self) -> None:
        if self._session is not None and self._owns_session:
            closer = getattr(self._session, "close", None)
            if callable(closer):
                try:
                    result = closer()
                    if asyncio.iscoroutine(result):
                        await result
                except Exception:
                    pass
        if self._owns_session:
            self._session = None

    async def __aenter__(self) -> SageClient:
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    def describe(self) -> str:
        """One line: who we are, through what, with how many auth labels."""
        where = self.proxies.get("https") if self.proxies else "direct"
        labels = self.auth_header().count("HASH")
        return (
            f"{len(self.cookies)} cookies, {labels} auth labels, "
            f"proxy {where}, retries {self.retries}"
        )

    # ------------------------------------------------------------------ auth

    @staticmethod
    def host() -> str:
        return SAGE_HOST.split("://", 1)[1]

    def scoped(self, host: str | None = None) -> dict[str, str]:
        """The cookies a browser would attach for this host."""
        return self.cookies.scoped(host or self.host())

    def auth_header(self, ts: int | None = None) -> str:
        """The three-label authorization value the page sends.

        All three hashes share one timestamp. On a normal account the three
        source cookies carry the same value, so the digests come out identical,
        which is exactly the header the browser emits. A missing cookie drops
        its label rather than the whole header.
        """
        ts = int(time.time()) if ts is None else ts
        jar = self.scoped()
        parts = []
        for label, cookie in AUTH_COOKIE_LABELS:
            value = jar.get(cookie)
            if value:
                parts.append(f"{label} {ts}_{_hash(ts, value)}")
        return " ".join(parts)

    def cookie_header(self, host: str | None = None) -> str:
        return self.cookies.header(host or self.host())

    def headers(self) -> dict:
        return {
            "content-type": "application/json+protobuf",
            "authorization": self.auth_header(),
            "x-goog-api-key": self.api_key,
            "x-goog-authuser": str(self.authuser),
            "x-user-agent": RPC_USER_AGENT,
            "origin": ORIGIN,
            "referer": ORIGIN + "/",
            "cookie": self.cookie_header(),
        }

    # ------------------------------------------------------------------ call

    @staticmethod
    def url_for(method: str) -> str:
        return f"{SAGE_HOST}/$rpc/{SAGE_SERVICE}/{method}"

    def timeout_for(self, method: str) -> float:
        return GEN_TIMEOUT if method in GEN_METHODS else self.timeout

    def backoff_delay(self, attempt: int) -> float:
        """Exponential with jitter, so parallel callers do not sync up."""
        base = min(self.backoff * (2 ** attempt), 8.0)
        return base * (0.5 + random.random() / 2)

    def classify(self, method: str, response) -> SageError:
        """Turn a non-200 into the most specific error it supports."""
        payload = _payload(response)
        blob = payload if isinstance(payload, str) else json.dumps(
            payload, ensure_ascii=False, default=str
        )
        upper = blob.upper()
        if response.status_code == 429:
            return QuotaExceededError(response.status_code, method, payload)
        if any(marker in upper for marker in AUTH_MARKERS):
            return SessionExpiredError(
                response.status_code, method, payload, _server_skew(response)
            )
        return SageError(response.status_code, method, payload)

    async def call(
        self,
        method: str,
        params: Any = None,
        *,
        timeout: float | None = None,
        retries: int | None = None,
        raw: bool = False,
        **kwargs,
    ) -> Any:
        """POST one method and hand back the decoded payload.

        `method` is the bare name, e.g. ``"GetLimitInfo"``. ``raw=True`` returns
        the response object instead, for headers or bytes, and skips the retry
        loop: the point of raw is to see the failure as it came back.

        Transient statuses and transport errors are retried with jittered
        backoff; anything else is raised on the first answer, because a 400 means
        the params are wrong and repeating them changes nothing.
        """
        url = self.url_for(method)
        body = json.dumps([] if params is None else params, ensure_ascii=False)
        wait = self.timeout_for(method) if timeout is None else timeout
        tries = self.retries if retries is None else max(0, int(retries))
        headers = self.headers()

        last: BaseException | None = None
        for attempt in range(tries + 1):
            try:
                response = await self.session.post(
                    url, data=body, headers=headers,
                    impersonate=self.impersonate, proxies=self.proxies or None,
                    timeout=wait, **kwargs,
                )
            except (_CurlError, OSError) as e:
                last = e
                if attempt == tries:
                    break
                await asyncio.sleep(self.backoff_delay(attempt))
                continue

            if raw:
                return response
            if response.status_code == 200:
                return _decode(response)

            error = self.classify(method, response)
            last = error
            if not error.retryable or attempt == tries:
                raise error
            await asyncio.sleep(_retry_after(response) or self.backoff_delay(attempt))

        if isinstance(last, SageError):
            raise last
        raise NetworkError(method, last or RuntimeError("no attempt ran"))

    async def call_many(
        self, calls: Iterable[Any], *, delay: float = 0.0, **kwargs
    ) -> list[dict]:
        """Run several methods and collect the outcome of each.

        Each item is a method name, or a ``(method, params)`` pair. An item with
        no params of its own falls back to the ``params=`` keyword. Errors are
        recorded per item instead of aborting the batch, which is what makes
        this usable for walking the method list: most methods reject an empty
        body, and that rejection is the useful part.
        """
        default_params = kwargs.pop("params", None)
        out: list[dict] = []
        for i, item in enumerate(calls):
            if isinstance(item, str):
                method, params = item, default_params
            else:
                method = item[0]
                params = item[1] if len(item) > 1 else default_params
            if i and delay:
                await asyncio.sleep(delay)
            try:
                out.append({
                    "method": method, "ok": True,
                    "body": await self.call(method, params, **kwargs),
                })
            except SageError as e:
                out.append({
                    "method": method, "ok": False, "status": e.status,
                    "error": str(e), "auth": isinstance(e, SessionExpiredError),
                    "body": e.payload,
                })
        return out

    async def survey(
        self,
        methods: Sequence[str] | None = None,
        *,
        delay: float = 0.15,
        params: Any = None,
        readonly_only: bool = True,
    ) -> list[dict]:
        """Call methods with an empty body and report who answers.

        This is how parameter shapes get learned: a method that returns a real
        payload for ``[]`` needs no params, and one that answers 400 names the
        field it wanted.

        ``readonly_only`` keeps the sweep on Get/List/Validate. The others
        include DeleteBusiness, PublishToInstagram and GenerateImage, and an
        empty body on those is a request to delete, publish or render with no
        target.

        Liveness is settled by GetLimitInfo up front rather than inferred from
        the first 401 in the sweep: a per-method 401 is a fact about that
        method, and one method demanding its own OAuth token says nothing about
        the session.
        """
        names = list(methods or METHODS)
        if readonly_only:
            names = [m for m in names if is_readonly(m)]

        try:
            await self.get_limit_info()
        except SageError as e:
            return [{
                "method": "GetLimitInfo", "ok": False, "status": e.status,
                "error": str(e), "auth": isinstance(e, SessionExpiredError),
                "body": e.payload,
            }]

        return await self.call_many(names, delay=delay, params=params)

    async def try_shapes(self, method: str, shapes: Sequence[Any], **kwargs) -> list[dict]:
        """Feed candidate param shapes to one method and keep every answer.

        The shapes are guesses, so all of them are reported rather than stopping
        at the first success: a method that quietly accepts a plausible-looking
        wrong shape is worth knowing about.
        """
        return await self.call_many([(method, shape) for shape in shapes], **kwargs)

    # -------------------------------------------------------- named methods

    async def get_limit_info(self) -> Any:
        return await self.call("GetLimitInfo", [])

    async def list_businesses(self) -> Any:
        return await self.call("ListBusinesses", [])

    async def list_business_items(self, business: str, page: int = 1,
                                  size: int = 50) -> Any:
        return await self.call("ListBusinessItems", [business, None, page, None, size])

    async def get_business_items(self, business: str, ids: list[str]) -> Any:
        return await self.call("GetBusinessItems", [business, list(ids)])

    async def get_business(self, business: str) -> Any:
        return await self.call("GetBusiness", [business])

    async def get_resource_info(self, ids: list[str]) -> Any:
        return await self.call("GetResourceInfo", [list(ids)])

    async def allocate_ids(self, count: int = 50) -> Any:
        """Reserve resource ids. The page asks for 50 at a time."""
        return await self.call("AllocateIds", [count])

    async def get_account(self) -> Any:
        return await self.call("GetAccount", [])

    async def get_photoshoot_suggestions(self, *params) -> Any:
        return await self.call("GetPhotoshootSuggestions", list(params))

    async def generate_image(self, *params) -> Any:
        return await self.call("GenerateImage", list(params))

    async def upscale_image(self, *params) -> Any:
        return await self.call("UpscaleImage", list(params))

    async def extract_product_from_url(self, *params) -> Any:
        return await self.call("ExtractProductFromUrl", list(params))

    async def create_business_item(self, *params) -> Any:
        return await self.call("CreateBusinessItem", list(params))

    async def first_business(self) -> str | None:
        """The account's business id, read rather than configured.

        ListBusinesses answers with a nested array; the id is the first string
        of the first row. Kept here so nothing downstream has to know the shape,
        and so a second account does not need a code edit.
        """
        try:
            rows = await self.list_businesses()
        except SageError as e:
            logger.debug(f"ListBusinesses failed: {e}")
            return None
        stack = [rows]
        while stack:
            node = stack.pop(0)
            if isinstance(node, list):
                if node and isinstance(node[0], str) and node[0]:
                    return node[0]
                stack.extend(x for x in node if isinstance(x, (list, dict)))
            elif isinstance(node, dict):
                for key in ("id", "businessId", "name"):
                    value = node.get(key)
                    if isinstance(value, str) and value:
                        return value
        return None

    async def first_account(self) -> str | None:
        """The account id the download path is built from."""
        got = await self.get_account()
        if isinstance(got, list) and got and isinstance(got[0], str):
            return got[0]
        for value in _flatten(got):
            if isinstance(value, str) and value:
                return value
        return None

    # --------------------------------------------------------- plain requests

    async def request(
        self,
        url: str,
        *,
        method: str = "GET",
        host: str = "labs.google.com",
        headers: dict | None = None,
        data: Any = None,
        timeout: float | None = None,
        retries: int | None = None,
        ok: Iterable[int] = (200,),
    ):
        """One request against the same session, for upload and download.

        The cookie header is built per host rather than handed to curl_cffi's
        ``cookies=``, which takes a dict and was seen to send nothing here - the
        trap that made the first upload attempt come back as a Google sign-in
        page. An empty jar sends no header at all, which matters for gstatic,
        where none of these cookies belong.
        """
        wait = self.timeout if timeout is None else timeout
        tries = self.retries if retries is None else max(0, int(retries))
        sent = {}
        jar = self.cookie_header(host)
        if jar:
            sent["cookie"] = jar
        sent.update(headers or {})

        last: Any = None
        for attempt in range(tries + 1):
            try:
                response = await self.session.request(
                    method, url, headers=sent, data=data,
                    impersonate=self.impersonate, proxies=self.proxies or None,
                    timeout=wait,
                )
            except (_CurlError, OSError) as e:
                last = e
                if attempt == tries:
                    break
                await asyncio.sleep(self.backoff_delay(attempt))
                continue

            if response.status_code in ok:
                return response
            last = response
            if response.status_code not in RETRY_STATUS or attempt == tries:
                break
            await asyncio.sleep(_retry_after(response) or self.backoff_delay(attempt))

        if isinstance(last, OSError):
            raise NetworkError(f"{method} {url}", last)
        raise SageError(
            getattr(last, "status_code", 0), f"{method} {url}",
            (getattr(last, "text", "") or "")[:300],
        )


def _flatten(payload: Any, depth: int = 4) -> list:
    out: list = []
    node = payload
    for _ in range(depth):
        if not isinstance(node, list) or not node:
            return out
        if isinstance(node[0], list) and len(node) == 1:
            node = node[0]
            continue
        for item in node:
            if isinstance(item, list):
                out.extend(_flatten(item, depth - 1))
            else:
                out.append(item)
        return out
    return out


def check_session(cookies: Any, *, proxy: str | None = None) -> list[str]:
    """Non-fatal problems with a jar, for a CLI to print instead of raising."""
    jar = as_cookies(cookies)
    problems: list[str] = []
    if not jar:
        problems.append("the jar is empty")
        return problems
    names = jar.names()
    if not any(n in names for _, n in AUTH_COOKIE_LABELS):
        problems.append(
            "none of SAPISID / __Secure-1PAPISID / __Secure-3PAPISID is present, "
            "so the authorization header cannot be built"
        )
    if not any(n in names for n in SESSION_COOKIE_NAMES):
        problems.append("no SID cookie, so the google.com session itself is missing")
    if not proxy and not os.environ.get(PROXY_ENV):
        problems.append(
            f"no proxy set (pass proxy=... or set {PROXY_ENV}); on a network that "
            f"cannot reach Google directly this fails at the first call"
        )
    return problems


__all__ = ["PROXY_ENV", "SageClient", "check_session", "default_proxies"]
