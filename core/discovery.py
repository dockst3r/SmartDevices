"""Busca de dispositivos, plugável por transporte.

Hoje existe a busca Bluetooth LE (core/ble.py). Um transporte novo (Wi-Fi,
infravermelho...) cria uma subclasse de Discovery com @register_discovery e
devolve Adverts; o Hub junta os resultados de todas.
"""
from __future__ import annotations

from .device import Advert

DISCOVERIES: list[type[Discovery]] = []


def register_discovery(cls: type[Discovery]) -> type[Discovery]:
    DISCOVERIES.append(cls)
    return cls


class Discovery:
    transport = ""
    label = ""

    async def scan(self, timeout: float) -> list[Advert]:
        raise NotImplementedError
