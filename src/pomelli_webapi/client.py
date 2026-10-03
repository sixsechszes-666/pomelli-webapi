"""The client itself: one object that holds the session and the caches."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .components.business_mixin import BusinessMixin
from .components.photoshoot_mixin import PhotoshootMixin
from .constants import API_KEY, AUTH_COOKIE_LABELS
from .exceptions import AuthError
from .sage import SageClient
from .utils.cookies import Cookies, as_cookies
from .utils.load_browser_cookies import load_browser_cookies

# Where the style reference PNGs get cached. They are static art that ships with
# the app, so they are fetched once per build and reused after that.
DEFAULT_STYLE_CACHE = Path(
    os.environ.get("POMELLI_CACHE")
    or Path.home() / ".cache" / "pomelli-webapi" / "style_assets"
)


class PomelliClient(BusinessMixin, PhotoshootMixin):
    """Async client for the Pomelli web app, driven entirely over HTTP.

    Parameters
    ----------
    cookies : `Cookies | dict | list | CookieJar | str | Path | None`
        The signed-in Google jar. A list of records from a browser export or a
        CDP dump is the safe form; a plain ``{name: value}`` mapping works when
        you have SAPISID and friends by hand; a path reads the jar from JSON.
        When omitted, the local browsers are searched for ``.google.com``
        cookies, which needs the optional ``browser`` extra - and if that finds
        nothing, `AuthError` is raised rather than a 401 several calls later.
    proxy : `str`, optional
        Proxy URL. Falls back to ``POMELLI_PROXY``, then to a direct connection.
    business, account : `str`, optional
        Pre-set ids. Normally read from the account at first use, so a second
        account needs no code change; override only to skip two calls.
    timeout : `float`, optional
        Per-call timeout for reads, in seconds.
    retries : `int`, optional
        Extra attempts for transient statuses and transport failures.
    poll : `float`, optional
        How often to ask whether a render has finished.
    style_cache : `str | Path`, optional
        Where to keep the downloaded style reference images, per build.
    strict : `bool`, optional
        Validate the jar on construction and raise `AuthError` if it cannot
        build an authorization header.

    Examples
    --------
    >>> import asyncio
    >>> from pomelli_webapi import PomelliClient
    >>>
    >>> async def main():
    ...     async with PomelliClient() as client:          # cookies from the browser
    ...         report = await client.photoshoot("shoe.jpg", ["Studio", "Flatlay"])
    ...         report.save("out")
    ...         print(report)
    >>>
    >>> asyncio.run(main())

    Resolving a `Cookies` object once and keeping it also works, and is what a
    long-lived process should do:

    >>> from pomelli_webapi import Cookies
    >>> jar = Cookies.from_file("cookies.json")
    >>> client = PomelliClient(jar)

    """

    def __init__(
        self,
        cookies: Any = None,
        proxy: str | None = None,
        *,
        business: str = "",
        account: str = "",
        timeout: float = 90.0,
        retries: int = 3,
        poll: float = 4.0,
        style_cache: str | Path | None = None,
        api_key: str = API_KEY,
        impersonate: str = "chrome",
        authuser: int = 0,
        strict: bool = True,
        session: Any = None,
    ):
        jar = _resolve_cookies(cookies, strict=strict)
        self._sage = SageClient(
            jar, proxy=proxy, api_key=api_key, impersonate=impersonate,
            authuser=authuser, timeout=60.0, retries=retries, session=session,
            strict=False,
        )
        self.timeout = timeout
        self.retries = max(0, int(retries))
        self.poll = poll
        self.style_cache = Path(style_cache or DEFAULT_STYLE_CACHE)

        self._business = business
        self._account = account
        self._build_label = ""
        self._style_sources: dict[str, list] = {}
        self._style_bodies: dict[str, bytes] = {}
        self._ready = False

    # ------------------------------------------------------------- lifecycle

    @property
    def sage(self) -> SageClient:
        """The underlying RPC client, for a method this package does not wrap."""
        return self._sage

    @property
    def cookies(self) -> Cookies:
        return self._sage.cookies

    @property
    def proxy(self) -> dict[str, str]:
        return self._sage.proxies

    async def init(self) -> PomelliClient:
        """Warm the caches: which business, which account, which app build.

        Not required - every call resolves what it needs on the way - but it
        turns the first real call's three extra round trips into one place, and
        it is where a bad session surfaces first.
        """
        await self.get_business()
        await self.get_account()
        self._ready = True
        return self

    async def close(self) -> None:
        await self._sage.close()

    async def __aenter__(self) -> PomelliClient:
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    def describe(self) -> str:
        """One line about the session, for a CLI header."""
        where = self.proxy.get("https") if self.proxy else "direct"
        return (
            f"cookies {len(self.cookies)}, business "
            f"{self._business or 'not resolved'}, account "
            f"{self._account or 'not resolved'}, proxy {where}"
        )

    # ------------------------------------------------------------ diagnostics

    async def ping(self) -> dict:
        """Is the session alive, and does it have renders left?

        This is the cheap check to run before a batch. It answers honestly about
        authentication, and deliberately does not claim to know the remaining
        budget: on an account that had spent every render, ``GetLimitInfo``
        still answered normally and carried no counter.
        """
        out: dict[str, Any] = {"ok": False, "limits": None, "error": ""}
        try:
            out["limits"] = await self.limits()
            out["ok"] = True
        except Exception as e:
            out["error"] = f"{type(e).__name__}: {e}"
        return out


def _resolve_cookies(cookies: Any, *, strict: bool = True) -> Cookies:
    """Turn whatever was passed into a jar, falling back to the local browsers."""
    if cookies is None:
        jar = load_browser_cookies()
        if not jar and strict:
            raise AuthError(
                "no cookies given and none found in the local browsers. Either "
                "pass the jar yourself - Cookies.from_file('cookies.json') or "
                "Cookies({'SAPISID': ...}) - or install the browser extra: "
                "`pip install pomelli-webapi[browser]`."
            )
        return jar
    if isinstance(cookies, (str, Path)):
        jar = Cookies.from_file(cookies)
    else:
        jar = as_cookies(cookies)
    if strict:
        # The SageClient checks this too; doing it here means the error names the
        # client constructor rather than a step inside it.
        jar.check([name for _, name in AUTH_COOKIE_LABELS])
    return jar


__all__ = ["DEFAULT_STYLE_CACHE", "PomelliClient"]
