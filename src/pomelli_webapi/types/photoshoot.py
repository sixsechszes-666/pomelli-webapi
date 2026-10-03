"""What a photoshoot produces.

Two things are deliberate here.

First, image bytes are held on the object, not in any dictionary that gets
serialized. A run that generated and downloaded six renders used to die at the
last step with ``TypeError: Object of type bytes is not JSON serializable``,
which throws away everything already paid for. So the report carries
:class:`RenderedImage` objects and saving them is an explicit call.

Second, ``status`` on a rendered image is the raw integer the server sent. The
bundle carries no enum for item states, so nothing here invents a name for a
number that was merely observed.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Plan:
    """One thing to render: a product shot, or a style inspiration.

    A preset names things the way step 2 shows them, and step 2 puts both kinds
    of card in one grid under one counter - so a name is resolved against both
    tables and the kind travels with it.
    """

    label: str
    kind: str                 # "template" | "style"
    template: int = 0         # shot type id, when kind == "template"
    style: str = ""           # curated style id, when kind == "style"

    @property
    def is_style(self) -> bool:
        return self.kind == "style"

    def as_dict(self) -> dict:
        return {
            "label": self.label,
            "kind": self.kind,
            "template": self.template or None,
            "style": self.style or None,
        }


@dataclass
class ItemState:
    """One queued render, and how far it got."""

    item_id: str
    label: str
    status: int | None = None
    resid: str | None = None
    error: str = ""

    @property
    def done(self) -> bool:
        return bool(self.resid)

    def as_dict(self) -> dict:
        return {
            "item_id": self.item_id, "label": self.label, "status": self.status,
            "resid": self.resid, "error": self.error,
        }


@dataclass
class RenderedImage:
    """One finished render, held in memory until it is written out.

    Attributes
    ----------
    label : `str`
        The pick that produced it, as it was named in the call.
    resid : `str`
        The resource id, which is also the last segment of `url`.
    data : `bytes`
        The image itself.
    mime : `str`
        Content type, as reported by the download endpoint or guessed from the
        bytes.

    """

    label: str
    resid: str
    data: bytes
    url: str = ""
    mime: str = ""

    @property
    def size(self) -> int:
        return len(self.data)

    @property
    def suffix(self) -> str:
        """A file extension to save it under. PNG unless the type says otherwise."""
        return _suffix_of(self.mime) or ".png"

    @property
    def digest(self) -> str:
        """Short content hash: two renders of the same pick differ here."""
        return hashlib.sha1(self.data).hexdigest()[:8]

    def save(self, path: str | Path) -> Path:
        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.data)
        return dest

    def as_dict(self) -> dict:
        """Everything except the bytes."""
        return {
            "label": self.label, "resid": self.resid, "url": self.url,
            "mime": self.mime, "bytes": self.size, "sha": self.digest,
        }


@dataclass
class RenderReport:
    """The outcome of one photoshoot call.

    ``images`` holds the downloads in the order the picks were named; ``queued``
    and ``failed`` describe every pick, including the ones that produced
    nothing. A pick that could not even be queued appears in ``refused`` with
    its error, because rendering costs the account real quota and a batch that
    dies on its third pick must not abandon the two it already paid for.
    """

    image: str = ""
    aspect: str = ""
    upload: dict = field(default_factory=dict)
    queued: list[ItemState] = field(default_factory=list)
    refused: list[dict] = field(default_factory=list)
    failed: list[ItemState] = field(default_factory=list)
    images: list[RenderedImage] = field(default_factory=list)
    seconds: float = 0.0
    stopped: str = ""
    quota: bool = False

    @property
    def ok(self) -> bool:
        return bool(self.images)

    @property
    def expected(self) -> int:
        return len(self.queued) + len(self.refused)

    def save(self, directory: str | Path, prefix: str = "") -> list[Path]:
        """Write every image into a directory, named by pick order and hash."""
        out = Path(directory)
        written = []
        for i, img in enumerate(self.images, start=1):
            stem = _slug(img.label)
            name = f"{prefix}{i:02d}_{stem}_{img.digest}{img.suffix}"
            written.append(img.save(out / name))
        return written

    def as_dict(self) -> dict:
        """The report as plain data. Never contains image bytes."""
        return {
            "image": self.image,
            "aspect": self.aspect,
            "upload": self.upload,
            "queued": [q.as_dict() for q in self.queued],
            "refused": self.refused,
            "failed": [f.as_dict() for f in self.failed],
            "images": [i.as_dict() for i in self.images],
            "count": len(self.images),
            "expected": self.expected,
            "seconds": self.seconds,
            "stopped": self.stopped,
            "quota": self.quota,
        }

    def __str__(self) -> str:
        head = f"{len(self.images)} of {self.expected} renders in {self.seconds}s"
        if self.quota:
            return f"{head} (out of quota, batch stopped early)"
        if self.refused:
            return f"{head} ({len(self.refused)} picks refused)"
        return head


def _suffix_of(mime: str) -> str:
    return {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}.get(
        (mime or "").lower(), ""
    )


def _slug(text: str) -> str:
    keep = [c if c.isalnum() else "-" for c in (text or "pick").lower()]
    return "".join(keep).strip("-") or "pick"


__all__ = ["ItemState", "Plan", "RenderReport", "RenderedImage"]
