"""Pull cookies out of a local browser, for when you would rather not export them.

This is the optional path. The library works from a jar you hand it; this
helper exists so that a machine already signed in to Google does not have to be
hand-copied from devtools.

``browser-cookie3`` ships behind the ``browser`` extra: ``pip install
pomelli-webapi[browser]``. It is imported lazily, so a missing extra turns into
an empty result rather than an ImportError at package import time.
"""

from __future__ import annotations

import importlib.util
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.cookiejar import CookieJar

from .cookies import Cookies
from .logger import logger

HAS_BROWSER_COOKIE3 = importlib.util.find_spec("browser_cookie3") is not None


def _browser_readers():
    import browser_cookie3 as bc3

    return [
        bc3.chrome, bc3.chromium, bc3.opera, bc3.opera_gx, bc3.brave,
        bc3.edge, bc3.vivaldi, bc3.firefox, bc3.librewolf, bc3.safari,
    ]


def load_browser_cookies(domain_name: str = ".google.com", verbose: bool = False) -> Cookies:
    """Load cookies for a domain from every supported local browser.

    Parameters
    ----------
    domain_name : `str`, optional
        Domain to filter by. Defaults to ``.google.com``, which is where the
        SAPISID family lives. Note that browser-cookie3 matches this loosely and
        a profile signed in to several Google domains can return all of them -
        which is exactly the case :class:`Cookies` is built to handle, so pass
        the whole jar through rather than collapsing it.
    verbose : `bool`, optional
        Log which browsers answered.

    Returns
    -------
    `Cookies`
        Every matching cookie from every browser, as full records. Empty if the
        optional dependency is absent, or if no browser has any.

    """
    if not HAS_BROWSER_COOKIE3:
        if verbose:
            logger.debug(
                "Optional dependency 'browser-cookie3' is not installed, so no "
                "browser cookies were loaded. Install it with "
                "`pip install pomelli-webapi[browser]`."
            )
        return Cookies()

    records: list[dict] = []

    def read(reader) -> list[dict]:
        try:
            jar: CookieJar = reader(domain_name=domain_name)
        except Exception as e:  # every browser fails differently; none is fatal
            if verbose:
                logger.debug(f"{reader.__name__}: {type(e).__name__}: {e}")
            return []
        out = []
        for cookie in jar:
            if cookie.is_expired():
                continue
            out.append({
                "name": cookie.name,
                "value": cookie.value,
                "domain": cookie.domain or "",
                "path": cookie.path or "/",
                "expires": cookie.expires,
            })
        return out

    readers = _browser_readers()
    with ThreadPoolExecutor(max_workers=len(readers)) as pool:
        futures = {pool.submit(read, fn): fn.__name__ for fn in readers}
        for future in as_completed(futures):
            got = future.result()
            if got:
                if verbose:
                    logger.debug(f"{futures[future]}: {len(got)} cookies")
                records.extend(got)

    return Cookies(records)


__all__ = ["HAS_BROWSER_COOKIE3", "load_browser_cookies"]
