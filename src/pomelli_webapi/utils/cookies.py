"""Cookie handling, done the way a browser does it.

The one thing that must not be shortcut here: Google's profile holds three
copies of every authentication cookie, scoped to ``.google.com``,
``.google.ru`` and ``.youtube.com``. Collapsing that jar into a
``{name: value}`` dict keeps whichever copy came last, which is how a request
to a google.com endpoint ends up carrying .google.ru's HSID - and Google
answers ``SESSION_COOKIE_INVALID``, naming the cookie, which looks exactly like
an expired session and sends you chasing a browser login you did not need.

So cookies are kept as the full records and matched per host and path the way
RFC 6265 says to.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any

from ..exceptions import AuthError

# A host-only cookie for this domain is what Google sets for its own services.
GOOGLE_DOMAIN = ".google.com"


def _as_record(name: str, value: str, domain: str = "", path: str = "/") -> dict:
    return {"name": name, "value": value, "domain": domain, "path": path}


def domain_matches(host: str, cookie_domain: str) -> bool:
    """RFC 6265 domain matching, so we pick the right copy of a shared name."""
    if not cookie_domain:
        return True
    cd = cookie_domain.lstrip(".").lower()
    h = host.lower()
    if cookie_domain.startswith("."):
        return h == cd or h.endswith("." + cd)
    return h == cd


def path_matches(request_path: str, cookie_path: str) -> bool:
    if not cookie_path:
        return True
    return request_path.startswith(cookie_path)


def specificity(cookie: Mapping[str, Any]) -> tuple:
    """Sort key, most specific first, so the dict picks the browser's copy.

    Path beats domain: a cookie scoped to ``/$rpc`` is a narrower grant than one
    scoped to ``/``. Between two domain cookies the longer domain wins, and a
    host-only cookie beats any domain cookie of the same name, because it was
    set for that exact host and nothing else.
    """
    domain = cookie.get("domain") or ""
    return (
        -len(cookie.get("path") or "/"),
        0 if not domain.startswith(".") else 1,
        -len(domain),
    )


class Cookies:
    """A cookie jar that knows which copy belongs to which host.

    Accepts, in order of how much it knows:

    - a list of records (``{"name", "value", "domain", "path"}``), which is what
      a browser export or the CDP ``Network.getAllCookies`` gives you - the only
      form that survives the three-copies trap;
    - a ``http.cookiejar.CookieJar``, converted to records;
    - a plain ``{name: value}`` mapping, which carries no scope and is therefore
      sent to every host. Convenient for a hand-built jar of SAPISID cookies,
      and the shape you get from picking two values out of devtools.

    Examples
    --------
    >>> from pomelli_webapi import Cookies
    >>> cookies = Cookies.from_file("cookies.json")
    >>> cookies = Cookies({"SAPISID": "...", "__Secure-1PAPISID": "..."})

    """

    __slots__ = ("_records",)

    def __init__(self, cookies: Any = None):
        self._records: list[dict] = []
        if cookies:
            self._records = self._coerce(cookies)

    # ------------------------------------------------------------- builders

    @staticmethod
    def _coerce(cookies: Any) -> list[dict]:
        if isinstance(cookies, Cookies):
            return list(cookies._records)
        if isinstance(cookies, CookieJar):
            return [
                _as_record(c.name, c.value, c.domain or "", c.path or "/")
                for c in cookies
            ]
        if isinstance(cookies, Mapping):
            return [_as_record(str(k), str(v)) for k, v in cookies.items()]
        out: list[dict] = []
        for item in cookies:
            if isinstance(item, Mapping):
                if "name" not in item or "value" not in item:
                    raise AuthError(
                        f"cookie record {item!r} has no name/value; expected the "
                        f"shape a browser exports: name, value, domain, path"
                    )
                out.append(_as_record(
                    str(item["name"]), str(item["value"]),
                    str(item.get("domain") or ""), str(item.get("path") or "/"),
                ))
            else:
                raise AuthError(f"cannot read cookie {item!r} of type {type(item).__name__}")
        return out

    @classmethod
    def from_file(cls, path: str | Path) -> Cookies:
        """Read a jar saved as JSON: either a list of records or a mapping.

        A saved CDP session file wraps its cookies under a "cookies" key, so
        that shape is unwrapped too rather than making every caller know it.
        """
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(data, Mapping) and "cookies" in data:
            data = data["cookies"]
        return cls(data)

    # ---------------------------------------------------------------- access

    def __bool__(self) -> bool:
        return bool(self._records)

    def __len__(self) -> int:
        return len(self._records)

    def __iter__(self):
        return iter(self._records)

    def __repr__(self) -> str:
        names = ", ".join(sorted({r["name"] for r in self._records}))
        return f"Cookies({len(self._records)} records: {names or 'empty'})"

    def records(self) -> list[dict]:
        return list(self._records)

    def set(self, name: str, value: str, domain: str = GOOGLE_DOMAIN,
            path: str = "/") -> None:
        self._records = [r for r in self._records if r["name"] != name]
        self._records.append(_as_record(name, value, domain, path))

    def get(self, name: str) -> str | None:
        for r in self._records:
            if r["name"] == name:
                return r["value"]
        return None

    def names(self) -> set[str]:
        return {r["name"] for r in self._records}

    def to_dict(self) -> dict[str, str]:
        """Last value wins, name-keyed. Fine for a look-up, wrong for a request."""
        return {r["name"]: r["value"] for r in self._records}

    def scoped(self, host: str, path: str = "/") -> dict[str, str]:
        """The cookies a browser would actually attach for this host and path."""
        hits = [
            r for r in self._records
            if domain_matches(host, r.get("domain", ""))
            and path_matches(path, r.get("path", "/"))
        ]
        out: dict[str, str] = {}
        for r in sorted(hits, key=specificity):
            out.setdefault(r["name"], r["value"])
        return out

    def header(self, host: str, path: str = "/") -> str:
        """The scoped jar as one Cookie header.

        Built by hand rather than handed to curl_cffi's ``cookies=`` parameter,
        which takes a dict, collapses the duplicate names and was seen to send
        nothing at all on this endpoint.
        """
        jar = self.scoped(host, path)
        return "; ".join(f"{n}={v}" for n, v in jar.items())

    def check(self, needed: Iterable[str] = ()) -> None:
        """Raise AuthError if the jar is empty or misses every needed name."""
        if not self._records:
            raise AuthError(
                "no cookies supplied. Pass the SAPISID family from a signed-in "
                "browser: Cookies({'SAPISID': ..., '__Secure-1PAPISID': ...}), "
                "or Cookies.from_file('cookies.json') for a full export."
            )
        wanted = list(needed)
        if wanted and not (self.names() & set(wanted)):
            raise AuthError(
                f"none of {', '.join(wanted)} is in the jar, so the "
                f"authorization header cannot be built. Present: "
                f"{', '.join(sorted(self.names())) or 'nothing'}"
            )


def as_cookies(cookies: Any) -> Cookies:
    """Coerce whatever the caller passed into a Cookies, once, at the edge."""
    return cookies if isinstance(cookies, Cookies) else Cookies(cookies)


__all__ = [
    "GOOGLE_DOMAIN",
    "Cookies",
    "as_cookies",
    "domain_matches",
    "path_matches",
    "specificity",
]
