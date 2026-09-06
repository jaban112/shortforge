from .apod import Apod
from .base import Image, Item, license_allowed
from .onthisday import OnThisDay

PACKS = {
    OnThisDay.name: OnThisDay,
    Apod.name: Apod,
}


def get_pack(name: str):
    if name not in PACKS:
        raise KeyError(f"unknown pack {name!r}; available: {sorted(PACKS)}")
    return PACKS[name]()


__all__ = ["Apod", "OnThisDay", "Image", "Item", "PACKS", "get_pack", "license_allowed"]
