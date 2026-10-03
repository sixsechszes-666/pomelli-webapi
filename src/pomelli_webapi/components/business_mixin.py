"""Who is signed in, and what has been made on this account."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from typing import Any

from ..constants import STATUS_DONE
from ..exceptions import APIError, SageError
from ..types.business import Account, BusinessItem
from ..utils.logger import logger
from ..utils.parsing import item_rows_of, item_status, result_resid, strings_of


class BusinessMixin:
    """Account, business and business-item calls.

    Mixed into :class:`~pomelli_webapi.client.PomelliClient`; every method here
    assumes ``self.sage`` is a
    :class:`~pomelli_webapi.sage.SageClient` and ``self._business`` /
    ``self._account`` are its caches.
    """

    sage: Any
    _business: str
    _account: str
    poll: float

    # -------------------------------------------------------------- identity

    async def get_business(self) -> str:
        """The account's business id, read once and cached.

        Read rather than configured, because it is a per-account value and a
        hard-coded one makes the library single-account.
        """
        if not self._business:
            found = await self.sage.first_business()
            if not found:
                raise APIError(
                    "ListBusinesses returned no business id. Either the session "
                    "belongs to an account that has never opened Pomelli, or the "
                    "cookies are stale."
                )
            self._business = found
        return self._business

    async def get_account(self) -> str:
        """The account id the download path is built from, read once and cached."""
        if not self._account:
            found = await self.sage.first_account()
            if not found:
                raise APIError("GetAccount returned no account id")
            self._account = found
        return self._account

    async def account(self) -> Account:
        """Everything the account calls say, in one object."""
        business = await self.get_business()
        account_id = await self.get_account()
        businesses: list[str] = []
        try:
            businesses = strings_of(await self.sage.list_businesses())
        except SageError as e:
            logger.debug(f"ListBusinesses failed while describing the account: {e}")
        return Account(
            account_id=account_id, business_id=business, businesses=businesses,
        )

    async def limits(self) -> Any:
        """Whatever ``GetLimitInfo`` reports.

        Worth knowing: on an account that was out of render quota this call
        still answers normally and carries no counter, so it is a liveness check
        and not a budget one.
        """
        return await self.sage.get_limit_info()

    # ----------------------------------------------------------------- items

    async def business_items(self, page: int = 1, size: int = 50) -> list[BusinessItem]:
        """A page of the account's items, newest first as the server orders them."""
        business = await self.get_business()
        payload = await self.sage.list_business_items(business, page, size)
        return [_to_item(business, row) for row in item_rows_of(payload)]

    async def item_states(self, item_ids: Sequence[str]) -> dict[str, BusinessItem]:
        """Status and result for each id, keyed by id.

        ``GetBusinessItems`` answers ``[]`` for an item that has not landed yet,
        so a missing key means "not yet", not "failed".
        """
        ids = list(item_ids)
        if not ids:
            return {}
        business = await self.get_business()
        payload = await self.sage.get_business_items(business, ids)
        out: dict[str, BusinessItem] = {}
        for row in item_rows_of(payload):
            item = _to_item(business, row)
            out[item.item_id] = item
        return out

    async def wait_for_items(
        self,
        item_ids: Sequence[str],
        *,
        timeout: float = 900.0,
        poll: float | None = None,
    ) -> dict[str, BusinessItem]:
        """Poll until every item is finished, failed, or the clock runs out.

        As long as one item is still rendering the call keeps going: a batch of
        six finishes over about a minute, and stopping at the first result would
        undercount it.
        """
        interval = self.poll if poll is None else poll
        ids = list(item_ids)
        started = time.time()
        settled: dict[str, BusinessItem] = {}

        while True:
            try:
                states = await self.item_states(ids)
            except SageError as e:
                if not e.retryable:
                    raise
                states = {}

            for item_id in ids:
                item = states.get(item_id)
                if item is None:
                    continue
                if item.resid or item.failed:
                    settled[item_id] = item

            if len(settled) >= len(ids) or time.time() - started >= timeout:
                break
            await asyncio.sleep(interval)

        return settled

    async def delete_item(self, item_id: str) -> Any:
        """Remove one item from the account."""
        business = await self.get_business()
        return await self.sage.call("DeleteBusinessItem", [business, item_id])


def _to_item(business: str, row: list) -> BusinessItem:
    created = row[3] if len(row) > 3 and isinstance(row[3], str) else ""
    updated = row[4] if len(row) > 4 and isinstance(row[4], str) else ""
    config = row[5] if len(row) > 5 and isinstance(row[5], list) else []
    return BusinessItem(
        business=business,
        item_id=row[1],
        status=item_status(row),
        created=created,
        updated=updated,
        resid=result_resid(row),
        config=config,
    )


__all__ = ["STATUS_DONE", "BusinessMixin"]
