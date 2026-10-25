"""Transporte Bluetooth LE: busca, conexão, reconexão e escrita."""
from __future__ import annotations

import asyncio

from bleak import BleakClient, BleakScanner
from bleak.exc import BleakError

from .device import Advert, Device
from .discovery import Discovery, register_discovery


@register_discovery
class BleDiscovery(Discovery):
    transport = "ble"
    label = "Bluetooth"

    async def scan(self, timeout: float) -> list[Advert]:
        found = await BleakScanner.discover(timeout=timeout, return_adv=True)
        adverts = []
        for dev, adv in found.values():
            name = adv.local_name or dev.name
            if name:
                adverts.append(Advert("ble", dev.address, name, adv.rssi, list(adv.service_uuids)))
        return adverts


class BleDevice(Device):
    transport = "ble"
    connect_timeout = 15.0
    # Muitos controladores baratos descartam um comando que chega logo depois do anterior.
    # Drivers definem aqui o intervalo mínimo entre escritas (segundos).
    write_interval = 0.0

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._client: BleakClient | None = None
        self._lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self._last_write = 0.0

    @property
    def connected(self) -> bool:
        return self._client is not None and self._client.is_connected

    @property
    def connecting(self) -> bool:
        return self._lock.locked() and not self.connected

    async def connect(self, attempts: int = 2) -> None:
        if self.connected:
            return
        async with self._lock:
            if self.on_change and not self.connected:
                self.on_change()  # o painel passa a mostrar "Conectando…"
            for attempt in range(1, attempts + 1):
                if self.connected:
                    return
                try:
                    await self._connect_once()
                    return
                except ConnectionError:
                    raise  # não encontrado: outra busca de 15 s não ajudaria
                except (BleakError, OSError, asyncio.TimeoutError):
                    # Logo após outra conexão (ou um app fechado à força) o aparelho às vezes
                    # cancela a primeira operação; uma nova tentativa costuma resolver.
                    if attempt == attempts:
                        raise
                    await asyncio.sleep(1.5)

    async def _connect_once(self) -> None:
        ble_device = await BleakScanner.find_device_by_address(self.address, timeout=self.connect_timeout)
        if ble_device is None:
            raise ConnectionError(
                f"{self.label} não respondeu. Verifique se está ligado e se o app do celular está fechado "
                "(a lâmpada aceita só uma conexão por vez)."
            )
        client = BleakClient(ble_device, disconnected_callback=self._on_disconnected, timeout=self.connect_timeout)
        await client.connect()
        self._client = client
        try:
            await self.on_connected(client)
        except Exception:
            await self.disconnect()
            raise

    async def wait_idle(self) -> None:
        async with self._lock:
            pass

    async def on_connected(self, client: BleakClient) -> None:
        """Gancho para handshakes ou descoberta de características."""

    def _on_disconnected(self, client: BleakClient) -> None:
        if client is self._client and self.on_change:
            self.on_change()

    async def disconnect(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            try:
                await client.disconnect()
            except BleakError:
                pass

    async def write(self, char_uuid: str, data: bytes, response: bool = False) -> None:
        """Escreve respeitando write_interval, reconectando uma vez se a conexão tiver caído."""
        async with self._write_lock:
            loop = asyncio.get_running_loop()
            wait = self._last_write + self.write_interval - loop.time()
            if wait > 0:
                await asyncio.sleep(wait)
            for attempt in range(2):
                if not self.connected:
                    await self.connect()
                try:
                    await self._client.write_gatt_char(char_uuid, data, response=response)
                    self._last_write = loop.time()
                    return
                except (BleakError, OSError):
                    await self.disconnect()
                    if attempt:
                        raise
