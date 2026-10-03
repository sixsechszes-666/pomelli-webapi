"""Errors raised by pomelli-webapi.

The split matters more than the names. A caller has to be able to tell apart
four situations that all look like "the request failed":

- the cookies were never supplied or make no sense (AuthError) - fix your side;
- the session was fine yesterday and Google has now dropped it
  (SessionExpiredError) - re-capture the cookies;
- the account is out of render quota (QuotaExceededError) - waiting is the only
  cure, and every further pick in the batch will be refused the same way;
- the server answered something unexpected (SageError) - read `.payload`.

Anything the wizard itself got wrong, as opposed to one HTTP call answering
badly, is a RenderError.
"""

from __future__ import annotations

from typing import Any


class PomelliError(Exception):
    """Base class for everything this package raises."""


class APIError(PomelliError):
    """The call was malformed on this side: an unknown template, too many picks."""


class AuthError(PomelliError):
    """No usable cookies, or cookies that cannot build an authorization header."""


class RenderError(PomelliError):
    """The photoshoot flow failed as a whole, not one HTTP call inside it."""


class SageError(PomelliError):
    """A LabsSageService call answered with a non-200.

    `status` is the HTTP status, or 0 when the request never got an answer at
    all. `payload` is Google's decoded error body, which for this service is a
    plain array like ``[3, "message", ...]``.
    """

    def __init__(self, status: int, method: str, payload: Any):
        self.status = status
        self.method = method
        self.payload = payload
        super().__init__(f"{method} -> HTTP {status}: {explain(payload)}")

    @property
    def retryable(self) -> bool:
        """A transport failure (status 0) or one of the transient statuses."""
        return self.status == 0 or self.status in (
            408, 425, 429, 500, 502, 503, 504
        )


class NetworkError(SageError):
    """The request never got an HTTP answer: DNS, TLS, timeout, proxy down."""

    def __init__(self, method: str, cause: BaseException):
        self.cause = cause
        super().__init__(0, method, f"{type(cause).__name__}: {cause}")


class SessionExpiredError(SageError):
    """Google says the session is gone.

    The message carries the clock skew measured against the response's Date
    header, because the authorization header hashes a local timestamp: a machine
    whose clock has drifted cannot authenticate no matter how fresh the cookies
    are, and it looks exactly like a dead session from here.
    """

    def __init__(self, status: int, method: str, payload: Any, skew: int | None = None):
        self.skew = skew
        super().__init__(status, method, payload)
        if skew is not None and abs(skew) > 120:
            self.args = (
                f"{self.args[0]}  [local clock is {skew:+d}s off the server; "
                f"SAPISIDHASH hashes a timestamp, so fix the clock before "
                f"capturing cookies again]",
            )


class QuotaExceededError(SageError):
    """The account is out of render quota: Google answered 429 exhausted.

    This is not a per-item failure, and treating it as one wastes the rest of a
    batch. Every pick after the first refusal is refused the same way, on an
    account that has already spent the renders it was given.
    """

    @property
    def retryable(self) -> bool:
        """Never: this is a state of the account, not a hiccup.

        A 429 from this service means the renders are spent, so repeating the
        call inside the transport only delays the decision the batch has to make
        anyway - and it hides the refusal behind four attempts and a backoff.
        The caller is the one that knows whether there is anything left worth
        keeping, so the error is raised on the first answer.
        """
        return False


def explain(payload: Any) -> str:
    """Pull the human sentence out of Google's ``[code, "message", ...]`` body."""
    if isinstance(payload, list) and len(payload) >= 2 and isinstance(payload[1], str):
        return payload[1]
    if isinstance(payload, dict):
        err = payload.get("error")
        if isinstance(err, dict) and isinstance(err.get("message"), str):
            return err["message"]
        if isinstance(err, str):
            return err
    if isinstance(payload, str):
        return payload[:300]
    import json

    return json.dumps(payload, ensure_ascii=False, default=str)[:300]


def is_quota(exc: BaseException) -> bool:
    """Is this refusal about the account's quota rather than this request?

    The status is the reliable signal. The strings are a fallback, because the
    refusal arrives as a bare 429 from the RPC layer and as a plain sentence
    once it has been through a saved report and back.
    """
    if isinstance(exc, QuotaExceededError):
        return True
    if getattr(exc, "status", None) == 429:
        return True
    text = str(exc).lower()
    return "429" in text or "quota" in text or "exhausted" in text


__all__ = [
    "APIError",
    "AuthError",
    "NetworkError",
    "PomelliError",
    "QuotaExceededError",
    "RenderError",
    "SageError",
    "SessionExpiredError",
    "explain",
    "is_quota",
]
