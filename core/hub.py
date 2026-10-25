"""Hub: o ponto único entre as interfaces (terminal, bandeja) e os drivers.

Toda interface fala só com o Hub; o Hub não sabe que tipo de aparelho está
controlando. Os métodos são assíncronos; HubThread permite chamá-los de
código síncrono (pystray, pywebview) rodando o loop numa thread própria.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from concurrent.futures import Future
from typing import Any, Callable

import drivers  # noqa: F401  (registra drivers e transportes)

from . import ble, storage  # noqa: F401  (ble registra a busca Bluetooth)
from .device import Advert, Device, best_driver
from .discovery import DISCOVERIES

log = logging.getLogger("smartdevices.hub")


def _error_text(exc: BaseException) -> str:
    text = str(exc).strip()
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)) and not text:
        return "não respondeu a tempo"
    return text or type(exc).__name__


class Hub:
    def __init__(self):
        self.devices: list[Device] = storage.load()
        self.settings: dict = storage.load_settings()
        self._found: dict[str, tuple[Advert, type[Device] | None]] = {}
        self.listeners: list[Callable[[], None]] = []  # avisados quando algo muda
        self._closing = False
        for d in self.devices:
            d.on_change = self._changed

    # ---------- consulta ----------
    def get(self, device_id: str) -> Device:
        for d in self.devices:
            if d.id == device_id:
                return d
        raise KeyError(f"Dispositivo {device_id} não está salvo.")

    def select(self, ids: list[str] | None) -> list[Device]:
        return list(self.devices) if not ids else [self.get(i) for i in ids]

    def snapshot(self) -> list[dict]:
        return [d.to_dict() for d in self.devices]

    def _changed(self) -> None:
        storage.save(self.devices)
        for fn in self.listeners:
            try:
                fn()
            except Exception:
                pass

    # ---------- busca e cadastro ----------
    async def scan(self, timeout: float = 8.0) -> list[dict]:
        results = await asyncio.gather(*(cls().scan(timeout) for cls in DISCOVERIES), return_exceptions=True)
        adverts = [a for r in results if not isinstance(r, BaseException) for a in r]
        errors = [r for r in results if isinstance(r, BaseException)]
        if errors and not adverts:
            raise RuntimeError(f"Falha na busca: {_error_text(errors[0])}")
        saved = {d.address for d in self.devices}
        self._found = {}
        rows = []
        for adv in adverts:
            drv = best_driver(adv)
            self._found[adv.address] = (adv, drv)
            rows.append({
                "address": adv.address, "name": adv.name, "transport": adv.transport, "signal": adv.signal,
                "supported": drv is not None, "driver_label": drv.driver_label if drv else "",
                "category": drv.category if drv else "other", "saved": adv.address in saved,
            })
        rows.sort(key=lambda r: (not r["supported"], r["saved"], -(r["signal"] or -999)))
        return rows

    def add(self, address: str, alias: str = "") -> Device:
        if any(d.address == address for d in self.devices):
            raise ValueError("Esse dispositivo já está salvo.")
        adv, drv = self._found.get(address, (None, None))
        if drv is None:
            raise ValueError("Não há driver para esse dispositivo. Procure de novo ou adicione um driver.")
        device = drv(adv.address, adv.name, alias.strip())
        device.on_change = self._changed
        self.devices.append(device)
        self._changed()
        return device

    async def remove(self, device_id: str) -> None:
        device = self.get(device_id)
        await device.disconnect()
        self.devices.remove(device)
        self._changed()

    def rename(self, device_id: str, alias: str) -> None:
        self.get(device_id).alias = alias.strip()
        self._changed()

    def set_option(self, device_id: str, key: str, value: Any) -> None:
        device = self.get(device_id)
        if key not in {p.key for p in device.options()}:
            raise ValueError(f"{device.label} não tem o ajuste '{key}'.")
        device.config[key] = value
        self._changed()

    async def set_model(self, device_id: str, model: str | None) -> None:
        device = self.get(device_id)
        await device.disconnect()
        device.model = model or None
        self._changed()

    # ---------- comandos ----------
    async def connect(self, ids: list[str] | None = None) -> list[dict]:
        results = await self._each(self.select(ids), lambda d: d.connect())
        self._changed()  # modelo detectado na conexão é salvo; interfaces atualizam o status
        return results

    async def keep_connected(self, first_wait: float = 30, max_wait: float = 300) -> None:
        """Reconecta quem caiu, esperando cada vez mais entre tentativas falhas
        (para não deixar o Bluetooth buscando sem parar um aparelho desligado na tomada)."""
        wait: dict[str, float] = {}
        next_try: dict[str, float] = {}
        loop = asyncio.get_running_loop()
        while not self._closing:
            now = loop.time()
            due = [d for d in self.devices if not d.connected and next_try.get(d.id, 0) <= now]
            if due:
                results = await self._each(due, lambda d: d.connect())
                for d, r in zip(due, results):
                    if r["ok"]:
                        wait.pop(d.id, None)
                        next_try.pop(d.id, None)
                    else:
                        wait[d.id] = min(max_wait, wait.get(d.id, first_wait / 2) * 2)
                        next_try[d.id] = loop.time() + wait[d.id]
                self._changed()
            await asyncio.sleep(5)

    async def execute(self, ids: list[str] | None, action_key: str, values: dict[str, Any] | None = None) -> list[dict]:
        values = values or {}
        targets = [d for d in self.select(ids) if action_key in {a.key for a in d.actions()}]

        async def one(d: Device):
            await d.run(action_key, **values)
            d.remember(action_key, values)

        results = await self._each(targets, one)
        self._changed()
        return results

    async def power_all(self, on: bool) -> list[dict]:
        ids = [d.id for d in self.devices if d.supports_power]
        return await self.execute(ids, "on" if on else "off") if ids else []

    async def _each(self, devices: list[Device], fn) -> list[dict]:
        results = await asyncio.gather(*(fn(d) for d in devices), return_exceptions=True)
        for d, r in zip(devices, results):
            if isinstance(r, BaseException):
                log.warning("%s (%s): %s", d.label, d.address, _error_text(r), exc_info=r)
        return [
            {"id": d.id, "label": d.label, "ok": not isinstance(r, BaseException),
             "error": _error_text(r) if isinstance(r, BaseException) else ""}
            for d, r in zip(devices, results)
        ]

    def set_setting(self, key: str, value: Any) -> None:
        self.settings[key] = value
        storage.save_settings(self.settings)

    async def shutdown(self) -> None:
        self._closing = True  # impede keep_connected de reconectar enquanto encerra
        # Espera conexões em andamento terminarem, para não deixar nenhuma aberta depois do disconnect.
        await asyncio.gather(*(asyncio.wait_for(d.wait_idle(), 20) for d in self.devices), return_exceptions=True)
        await asyncio.gather(*(d.disconnect() for d in self.devices), return_exceptions=True)
        storage.save(self.devices)


class HubThread:
    """Roda o Hub num loop asyncio em segundo plano, para interfaces síncronas."""

    def __init__(self, hub: Hub):
        self.hub = hub
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self.loop.run_forever, name="hub", daemon=True)
        self._thread.start()

    def submit(self, coro) -> Future:
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def call(self, coro, timeout: float | None = 60):
        return self.submit(coro).result(timeout)

    def sync(self, fn: Callable, *args, timeout: float | None = 10):
        """Executa uma função comum dentro do loop do Hub (evita corrida com comandos em andamento)."""
        async def wrapper():
            return fn(*args)
        return self.call(wrapper(), timeout)

    def stop(self) -> None:
        try:
            self.call(self.hub.shutdown(), timeout=10)
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)
