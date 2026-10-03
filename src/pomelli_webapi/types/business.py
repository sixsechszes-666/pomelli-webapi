"""The account side: who is signed in, and what a business item is."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class BusinessItem:
    """One row of ``GetBusinessItems``.

    Attributes
    ----------
    item_id : `str`
        The id the item was created under. Also the id the render lands in.
    status : `int | None`
        The raw state the server reported: 1 the moment an item is created, 2
        while it renders, 3 with the finished resource. The bundle carries no
        enum, so a number above 3 is carried through unchanged rather than
        given a name.
    resid : `str | None`
        The finished resource id, once there is one.
    config : `list`
        The raw item config, for when you need to see what was actually sent.

    """

    business: str = ""
    item_id: str = ""
    status: int | None = None
    created: str = ""
    updated: str = ""
    resid: str | None = None
    config: list = field(default_factory=list)

    @property
    def done(self) -> bool:
        return self.status == 3

    @property
    def failed(self) -> bool:
        return isinstance(self.status, int) and self.status > 3

    def as_dict(self) -> dict:
        return {
            "business": self.business, "item_id": self.item_id,
            "status": self.status, "resid": self.resid,
            "created": self.created, "updated": self.updated,
        }


@dataclass
class Account:
    """What ``GetAccount`` and ``ListBusinesses`` say about the signed-in user.

    Attribute names follow the plainest reading of the responses: the account id
    is the first string ``GetAccount`` returns, and the business id is the first
    string of the first row of ``ListBusinesses``. Both are read at runtime
    rather than configured, so a second account needs no code change.
    """

    account_id: str = ""
    business_id: str = ""
    businesses: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "account_id": self.account_id, "business_id": self.business_id,
            "businesses": list(self.businesses),
        }


__all__ = ["Account", "BusinessItem"]
