"""The Photoshoot wizard, spoken over HTTP.

Every step the wizard takes is one of six calls, and none of their parameter
shapes are readable in the bundle: they were recorded off live runs. What the
recordings settled:

    AllocateIds [50]                     -> 50 reserved ids, one per output
    POST labs.google.com/pomelli_uploads/resource?authuser=0
                                         resumable, two steps: `start` answers
                                         an x-goog-upload-url, then
                                         `upload, finalize` with the bytes
                                         answers {"id": <resid>, "mimeType": ...}
    MutateBusiness [business, [A, B]]    A = [null x5, [[[resid, mime]]]]
                                         B = [null x7, [[resid]]]
    CreateBusinessItem [business, [null x12, []], item_id]
    MutateBusinessItem [business, item_id, [[21 fields]]]
                                         field 2 is the aspect ratio, and either
                                         field 5 (a product shot) or field 8 (a
                                         style) carries the job
    GetBusinessItems [business, [ids]]   item status walks 1 -> 2 -> 3, and 3
                                         hands the finished resource back in
                                         field 5[12][0]
    GET labs.google.com/pomelli_downloads/accounts/<acct>/resources/<resid>

The item config is nested one level deeper than it looks: the outer array is 21
slots and carries the whole job in slot 20, and the job is itself a shorter array
whose slot 2 is the aspect ratio, slot 5 a shot type id and slot 8 a style. The
browser trims trailing nulls, so a product-shot job is recorded six slots long
and a style job nine; both recorded shapes are reproduced exactly, because a
longer array than the browser ever sent would be a guess.

A product shot is named by a small integer; a style is not named at all, which is
why a style run downloads and uploads its own reference images before it renders
anything.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

from ..constants import (
    DEFAULT_ASPECT,
    DOWNLOAD_URL,
    FALLBACK_BUILD,
    GSTATIC,
    ORIGIN,
    PHOTOSHOOT_URL,
    RPC_USER_AGENT,
    STYLES,
    UPLOAD_URL,
)
from ..exceptions import (
    APIError,
    PomelliError,
    QuotaExceededError,
    RenderError,
    SageError,
    is_quota,
)
from ..types.photoshoot import ItemState, RenderedImage, RenderReport
from ..types.source import Source
from ..utils.logger import logger
from ..utils.parsing import strings_of
from ..utils.planning import aspect_id, aspect_label, make_plan, mime_for

Progress = Callable[[str], None]


class PhotoshootMixin:
    """Upload, queue, wait, download. No browser anywhere."""

    sage: Any
    timeout: float
    retries: int
    poll: float
    style_cache: Path

    _business: str
    _account: str
    _build_label: str
    _style_sources: dict
    _style_bodies: dict

    # -------------------------------------------------------------- identity

    async def get_build(self) -> str:
        """The live app build id, which the gstatic asset paths need.

        Read off the page rather than pinned, because it is the build date in
        base64 and changes whenever the app ships. One GET, cached for the life
        of the client.
        """
        if self._build_label:
            return self._build_label
        label = FALLBACK_BUILD
        try:
            response = await self.sage.request(
                PHOTOSHOOT_URL, host="labs.google.com", timeout=60, retries=1
            )
            found = re.findall(r"/_/bettany/([A-Za-z0-9_\-]+)/", response.text)
            if found:
                label = found[-1]
        except Exception as e:
            logger.debug(
                f"could not read the build id off the page ({e}); falling back "
                f"to {FALLBACK_BUILD}"
            )
        self._build_label = label
        return label

    # ---------------------------------------------------------------- upload

    async def upload(self, path: str | Path) -> Source:
        """Push a file and get back the resource id the API names it by."""
        file = Path(path)
        return await self.upload_bytes(file.read_bytes(), file.name)

    async def upload_bytes(self, data: bytes, name: str = "upload.png") -> Source:
        """The two-step resumable upload, done by hand.

        Step one announces the length and answers an upload URL; step two posts
        the bytes to it and answers the resource id. The browser does exactly
        this, and there is no multipart form anywhere in the flow.
        """
        base = {
            "origin": ORIGIN,
            "referer": PHOTOSHOOT_URL,
            "x-user-agent": RPC_USER_AGENT,
        }
        start = await self.sage.request(
            UPLOAD_URL,
            method="POST",
            headers={
                **base,
                "x-goog-upload-protocol": "resumable",
                "x-goog-upload-command": "start",
                "x-goog-upload-header-content-length": str(len(data)),
                "content-type": "application/x-www-form-urlencoded;charset=UTF-8",
            },
            data=b"",
            timeout=60,
        )

        content_type = (start.headers.get("content-type") or "").lower()
        target = _upload_url_of(start)
        if not target or "html" in content_type:
            raise RenderError(
                "the upload endpoint answered with a sign-in page instead of an "
                "upload URL, so these cookies are not valid for labs.google.com. "
                "Capture them from a browser signed in to the account that has "
                "Pomelli open."
            )

        final = await self.sage.request(
            target,
            method="POST",
            headers={
                **base,
                "x-goog-upload-command": "upload, finalize",
                "x-goog-upload-offset": "0",
                "content-type": "application/x-www-form-urlencoded;charset=utf-8",
            },
            data=data,
            timeout=max(300.0, self.timeout),
        )

        try:
            got = final.json()
        except Exception:
            got = None
        resid = got.get("id") if isinstance(got, dict) else None
        if not resid:
            raise RenderError(
                f"upload finalize answered {final.status_code} with "
                f"{(final.text or '')[:200]!r}, and no resource id"
            )
        return Source(resid, got.get("mimeType") or mime_for(name), name)

    # ---------------------------------------------------------------- styles

    async def style_image(self, name: str) -> bytes:
        """One style reference PNG from gstatic, cached on disk per build."""
        cached = self._style_bodies.get(name)
        if cached:
            return cached
        build = await self.get_build()
        dest = self.style_cache / build / Path(name).name
        if dest.exists() and dest.stat().st_size:
            body = dest.read_bytes()
        else:
            url = f"{GSTATIC}/_/bettany/{build}/sage%2Fassets/{name}"
            response = await self.sage.request(
                url, host="www.gstatic.com", timeout=120, retries=1
            )
            body = response.content
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(body)
        self._style_bodies[name] = body
        return body

    async def style_refs(self, style: str) -> list[Source]:
        """Upload a style's reference images and return their resource ids.

        Cached per client, so two picks built from the same style share one set
        of uploads and a run of four styles pays for each style once.
        """
        if style not in STYLES:
            raise APIError(f"unknown style id {style!r}")
        if style in self._style_sources:
            return self._style_sources[style]
        refs: list[Source] = []
        for name in STYLES[style]["images"]:
            refs.append(await self.upload_bytes(await self.style_image(name), Path(name).name))
        self._style_sources[style] = refs
        return refs

    # ---------------------------------------------------------- wizard calls

    async def allocate(self, count: int) -> list[str]:
        """Reserve item ids. The page asks for 50 at a time."""
        payload = await self.sage.allocate_ids(max(count, 1))
        ids = strings_of(payload)
        if len(ids) < count:
            raise RenderError(f"AllocateIds({count}) returned {len(ids)} usable ids")
        return ids[:count]

    async def create_item(self, item_id: str) -> Any:
        """Create the business item the render will land in."""
        business = await self.get_business()
        return await self.sage.call(
            "CreateBusinessItem", [business, [None] * 12 + [[]], item_id]
        )

    async def attach_source(self, source: Source) -> Any:
        """Put the source image on the business.

        The item config carries the source too, so this is not what makes a
        render possible - but it is part of the recorded sequence, and skipping
        it makes the account's server-side state drift from the UI's.

        Both fields are a list of one row, and the row is itself a list, built
        into named locals because the two shapes differ by one nesting level.
        """
        business = await self.get_business()
        shot = [[[source.resid, source.mime]]]
        ids = [[source.resid]]
        return await self.sage.call(
            "MutateBusiness", [business, [[None] * 5 + [shot], [None] * 7 + [ids]]]
        )

    async def set_shot(
        self,
        item_id: str,
        source: Source,
        *,
        aspect: int = DEFAULT_ASPECT,
        template: int = 0,
        style: list[Source] | None = None,
    ) -> Any:
        """Fill in an item's config: what to make, from what, at what ratio.

        The two kinds are separate slots, so a pick of both kinds goes in one
        request with both set rather than costing two renders. Passing neither
        is a programming error and raises rather than sending an empty job.
        """
        if not template and not style:
            raise APIError("an item needs a template or a style")
        business = await self.get_business()
        job: list[Any] = [None] * 6
        job[2] = aspect
        if template:
            job[5] = [[source.as_pair()], business, None, [template]]
        if style:
            job += [None] * (9 - len(job))
            job[8] = [source.as_pair(), [s.as_pair() for s in style], business]
        return await self.sage.call(
            "MutateBusinessItem", [business, item_id, [[None] * 20 + [job]]]
        )

    # -------------------------------------------------------------- download

    async def download(self, resid: str, *, timeout: float = 300.0) -> bytes:
        """The finished image, straight off its resource URL."""
        account = await self.get_account()
        url = DOWNLOAD_URL.format(account=account, resid=resid) + "?authuser=0"
        response = await self.sage.request(
            url, host="labs.google.com", timeout=timeout, retries=1
        )
        return response.content

    async def download_url(self, resid: str) -> str:
        """The URL a finished render can be fetched from."""
        account = await self.get_account()
        return DOWNLOAD_URL.format(account=account, resid=resid) + "?authuser=0"

    # ----------------------------------------------------------- the shoot

    async def photoshoot(
        self,
        image: str | Path | Source,
        picks: Sequence[str],
        *,
        aspect: str | int | None = None,
        timeout: float = 900.0,
        on_progress: Progress | None = None,
    ) -> RenderReport:
        """Upload one product photo, render each pick, download the results.

        Parameters
        ----------
        image : `str | Path | Source`
            The product photo. Pass a `Source` that has already been uploaded to
            reuse it across several calls instead of paying for the upload twice.
        picks : `Sequence[str]`
            Shot type names and/or style labels, at most six, as step 2 shows
            them in one grid.
        aspect : `str | int | None`
            ``"square"``, ``"story"``, ``"feed"``, or a raw id. Defaults to what
            the recorded runs used, square.
        timeout : `float`
            How long to wait for the batch to finish rendering.
        on_progress : `Callable[[str], None]`, optional
            Called with a line of progress. A plain callable; nothing here
            prints on its own.

        Returns
        -------
        `RenderReport`
            The images, and an entry for every pick even when it produced
            nothing. Image bytes stay on the report, so serializing
            ``report.as_dict()`` never has to deal with them.

        """
        jobs = make_plan(picks)
        say: Progress = on_progress or (lambda _msg: None)
        ratio = aspect_id(aspect)
        started = time.time()

        source = image if isinstance(image, Source) else await self.upload(image)
        if not isinstance(image, Source):
            say(f"uploaded {Path(str(image)).name} -> {source.resid} ({source.mime})")

        try:
            await self.attach_source(source)
        except SageError as e:
            say(f"! attach_source failed, carrying on: {e}")

        # Style references are uploaded once each, before anything is queued, so
        # a failure there costs nothing already rendered.
        for job in jobs:
            if job.is_style:
                refs = await self.style_refs(job.style)
                say(f"style {STYLES[job.style]['label']}: {len(refs)} reference "
                    f"images uploaded")

        report = RenderReport(
            image=image if isinstance(image, str) else str(getattr(image, "name", image)),
            aspect=aspect_label(ratio),
            upload={"resid": source.resid, "mime": source.mime},
        )

        ids = await self.allocate(len(jobs))
        quota: BaseException | None = None
        for job, item_id in zip(jobs, ids, strict=False):
            # One pick failing to queue must not abandon the picks that did.
            # Rendering costs the account real quota, so a batch that dies on
            # item 3 of 6 would throw away two renders it already paid for and
            # never download them.
            try:
                await self.create_item(item_id)
                await self.set_shot(
                    item_id, source, aspect=ratio, template=job.template,
                    style=(self._style_sources.get(job.style)
                           if job.is_style else None),
                )
            except (SageError, PomelliError) as e:
                report.refused.append({**job.as_dict(), "item_id": item_id,
                                       "error": f"{type(e).__name__}: {e}"})
                say(f"! could not queue {job.label}: {e}")
                if is_quota(e):
                    # Not this pick's fault and not fixable by asking again: the
                    # account is out. Stop queueing and keep what is rendering.
                    quota = e
                    break
                continue
            report.queued.append(ItemState(item_id=item_id, label=job.label))
            say(f"queued {job.label} as {item_id}")

        if not report.queued:
            if quota is not None:
                raise QuotaExceededError(
                    429, "MutateBusinessItem",
                    f"out of render quota, nothing queued ({len(report.refused)} "
                    f"of {len(jobs)} picks refused)",
                ) from quota
            raise RenderError(
                f"nothing could be queued ({len(report.refused)} picks failed): "
                + "; ".join(f"{f['label']}: {f['error']}" for f in report.refused[:3])
            )
        if quota is not None:
            report.quota = True
            say(f"! out of quota after {len(report.queued)} of {len(jobs)} picks; "
                f"rendering what was queued and stopping there")

        settled = await self.wait_for_items(
            [q.item_id for q in report.queued], timeout=timeout, poll=self.poll
        )
        report.stopped = "settled" if len(settled) >= len(report.queued) else "timeout"
        say(f"render: {len(settled)} of {len(report.queued)} settled")

        await self._harvest(report, settled, say)
        report.seconds = round(time.time() - started, 1)
        return report

    async def _harvest(
        self, report: RenderReport, settled: dict, say: Progress
    ) -> None:
        """Download every finished render, in the order the picks were named."""
        by_id = {q.item_id: q.label for q in report.queued}
        for item in report.queued:
            state = settled.get(item.item_id)
            if state is None:
                continue
            if state.failed:
                report.failed.append(ItemState(
                    item_id=item.item_id, label=item.label,
                    status=state.status, error="the item did not finish rendering",
                ))
                continue
            resid = state.resid
            if not resid:
                continue
            url = await self.download_url(resid)
            try:
                data = await self.download(resid)
            except PomelliError as e:
                report.failed.append(ItemState(
                    item_id=item.item_id, label=item.label, status=state.status,
                    error=f"{type(e).__name__}: {e}",
                ))
                continue
            report.images.append(RenderedImage(
                label=by_id.get(item.item_id, item.item_id), resid=resid, data=data,
                url=url, mime=_mime_of(data),
            ))
            say(f"downloaded {by_id.get(item.item_id, item.item_id)} "
                f"({len(data) / 1024:.0f} KB)")

    # ------------------------------------------------------------ one image

    async def render_one(
        self,
        image: str | Path,
        pick: str,
        *,
        aspect: str | int | None = None,
        timeout: float = 900.0,
        on_progress: Progress | None = None,
    ) -> RenderedImage | None:
        """A convenience wrapper for the common case: one photo, one pick."""
        report = await self.photoshoot(
            image, [pick], aspect=aspect, timeout=timeout, on_progress=on_progress
        )
        return report.images[0] if report.images else None

    async def photoshoot_many(
        self,
        image: str | Path,
        presets: Iterable[Sequence[str]],
        *,
        aspect: str | int | None = None,
        timeout: float = 900.0,
        on_progress: Progress | None = None,
    ) -> list[RenderReport]:
        """Run several presets over one photo, stopping when the quota ends.

        The photo is uploaded once and reused, so the second preset onward costs
        no upload. A quota refusal stops the sequence rather than being retried
        per preset, because every further pick would be refused the same way.
        """
        source = await self.upload(image)
        reports: list[RenderReport] = []
        for picks in presets:
            try:
                reports.append(await self.photoshoot(
                    source, picks, aspect=aspect, timeout=timeout,
                    on_progress=on_progress,
                ))
            except QuotaExceededError as e:
                if on_progress:
                    on_progress(f"stopping: {e}")
                break
        return reports


def _upload_url_of(response) -> str:
    """Pick the upload URL out of the start response.

    Google answers with the header twice and the second copy is the longer one
    carrying ``upload_protocol=resumable``, so take the longest rather than
    whichever lookup happens to return first.
    """
    values: list[str] = []
    getter = getattr(response.headers, "get_list", None)
    if callable(getter):
        try:
            values = list(getter("x-goog-upload-url") or [])
        except Exception:
            values = []
    if not values:
        one = response.headers.get("x-goog-upload-url")
        values = [one] if one else []
    return max(values, key=len) if values else ""


def _mime_of(data: bytes) -> str:
    """Sniff the container from the magic bytes, since downloads carry no type."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


__all__ = ["PhotoshootMixin", "Progress"]
