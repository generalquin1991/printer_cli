"""Compose an immutable request into a previewable raster and TSPL artifact."""

from dataclasses import dataclass

from layout import render_row
from jobs import PrintRequest
from media import MediaProfile
from tspl_backend import TSPLBackend


@dataclass(frozen=True)
class RenderedJob:
    raster: object
    payload: bytes


def render_request(request, media, density=12):
    if not isinstance(request, PrintRequest):
        raise TypeError("request must be PrintRequest")
    if not isinstance(media, MediaProfile):
        raise TypeError("media must be MediaProfile")
    if not request.placements:
        raise ValueError("print request has no content")
    slots = [placement.slot for placement in request.placements]
    if len(slots) != len(set(slots)):
        raise ValueError("print request contains duplicate slots")
    raster = render_row({item.slot: item.content for item in request.placements},
                        media, request.fit)
    payload = TSPLBackend(media, density).encode_print(raster, request.speed)
    return RenderedJob(raster, payload)
