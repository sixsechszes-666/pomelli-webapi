"""An image that has been uploaded to the account."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Source:
    """A resource the API names by id: the uploaded image, or a style reference.

    Attributes
    ----------
    resid : `str`
        The resource id Google assigned on upload. Every later call refers to
        the image by this, never by its URL.
    mime : `str`
        The content type the server stored it as.
    name : `str`
        The local filename, when there was one. Carried for reporting only.

    """

    resid: str
    mime: str
    name: str = ""

    def as_pair(self) -> list[str]:
        """The ``[resid, mime]`` pair the item config is built from."""
        return [self.resid, self.mime]

    @property
    def suffix(self) -> str:
        return Path(self.name).suffix.lower() if self.name else ""

    def __str__(self) -> str:
        label = f" {self.name}" if self.name else ""
        return f"{self.resid} ({self.mime}){label}"


__all__ = ["Source"]
