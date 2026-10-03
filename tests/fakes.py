"""Test doubles for the HTTP layer.

The point of these is that the whole client can be exercised without a session,
a proxy or a network. Everything the library does is one of two calls -
``SageClient.call`` (a JSON-RPC POST) and ``SageClient.request`` (an upload,
download or page fetch) - so a transport that answers by URL covers the lot.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any


class FakeHeaders(dict):
    """A header mapping that also answers curl_cffi's ``get_list``."""

    def get_list(self, name: str) -> list[str]:
        value = self.get(name) or self.get(name.lower())
        return [value] if value else []


class FakeResponse:
    def __init__(
        self,
        status_code: int = 200,
        body: Any = None,
        *,
        text: str | None = None,
        headers: dict | None = None,
        content: bytes = b"",
    ):
        self.status_code = status_code
        self._body = body
        self._text = text if text is not None else json.dumps(body if body is not None else [])
        self.headers = FakeHeaders(headers or {})
        self.content = content

    @property
    def text(self) -> str:
        return self._text

    def json(self) -> Any:
        if self._body is None and self._text:
            return json.loads(self._text)
        return self._body


class FakeSession:
    """An AsyncSession stand-in driven by a URL -> response function.

    The handler is called as ``handler(url, method, data, headers)``. Returning
    ``None`` means "no opinion", which the default replies to with ``[]``.
    """

    def __init__(self, handler: Callable[..., Any] | None = None):
        self.handler = handler
        self.calls: list[dict] = []
        self.closed = False

    def _answer(self, method: str, url: str, data: Any, headers: dict | None):
        self.calls.append({"method": method, "url": url, "data": data,
                           "headers": headers or {}})
        if self.handler is None:
            return FakeResponse(200, [])
        got = self.handler(url, method, data, headers or {})
        if got is None:
            return FakeResponse(200, [])
        if isinstance(got, FakeResponse):
            return got
        return FakeResponse(200, got)

    async def post(self, url: str, data: Any = None, headers: dict | None = None, **kw):
        return self._answer("POST", url, data, headers)

    async def request(self, method: str, url: str, data: Any = None,
                      headers: dict | None = None, **kw):
        return self._answer(method, url, data, headers)

    async def close(self) -> None:
        self.closed = True

    # ------------------------------------------------------------------ views

    def method_calls(self, name: str) -> list[dict]:
        """Every call whose URL ends with this RPC method name."""
        return [c for c in self.calls if c["url"].rstrip("/").endswith("/" + name)]

    def bodies(self, name: str) -> list[Any]:
        return [json.loads(c["data"]) for c in self.method_calls(name)]


def rpc_handler(routes: dict[str, Any]):
    """A handler that dispatches on the RPC method name in the URL.

    ``routes`` maps a method name to a payload, a callable taking the request
    body, or a FakeResponse for a failure. Unknown methods answer ``[]``.
    """

    def handler(url: str, method: str, data: Any, headers: dict):
        for name, answer in routes.items():
            if url.rstrip("/").endswith("/" + name):
                if callable(answer):
                    parsed = json.loads(data) if data else []
                    return answer(parsed)
                return answer
        return None

    return handler
