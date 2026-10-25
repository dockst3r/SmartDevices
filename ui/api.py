"""Funções que o painel HTML chama via pywebview (window.pywebview.api.*).

Só atributos com _ ficam escondidos do JavaScript; todo método público vira
uma função disponível no painel. Erros voltam como {"error": "..."} para o
painel mostrar, em vez de exceções que o JavaScript não entende.
"""
from __future__ import annotations

import functools
import logging

from core.hub import HubThread

from . import win

log = logging.getLogger("smartdevices.api")


def _safe(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            result = fn(*args, **kwargs)
            return {"ok": True} if result is None else result
        except Exception as e:
            log.exception("api.%s falhou", fn.__name__)
            return {"error": str(e) or type(e).__name__}
    return wrapper


class Api:
    def __init__(self, hub_thread: HubThread, panel):
        self._ht = hub_thread
        self._hub = hub_thread.hub
        self._panel = panel  # tray.Panel: hide(), quit(), fit()

    @_safe
    def snapshot(self):
        return {
            "devices": self._ht.sync(self._hub.snapshot),
            "settings": {"startup": win.startup_enabled(), "accent": win.accent_colors()},
        }

    @_safe
    def execute(self, ids, action_key, values=None):
        return {"results": self._ht.call(self._hub.execute(ids or None, action_key, values or {}))}

    @_safe
    def power_all(self, on):
        return {"results": self._ht.call(self._hub.power_all(bool(on)))}

    @_safe
    def connect(self, ids=None):
        return {"results": self._ht.call(self._hub.connect(ids or None))}

    @_safe
    def scan(self):
        return {"rows": self._ht.call(self._hub.scan(8.0))}

    @_safe
    def add(self, address, alias=""):
        device = self._ht.sync(self._hub.add, address, alias)
        self._ht.submit(self._hub.connect([device.id]))  # conecta em segundo plano
        return {"id": device.id}

    @_safe
    def remove(self, device_id):
        self._ht.call(self._hub.remove(device_id))

    @_safe
    def rename(self, device_id, alias):
        self._ht.sync(self._hub.rename, device_id, alias)

    @_safe
    def set_model(self, device_id, model):
        self._ht.call(self._hub.set_model(device_id, model or None))

    @_safe
    def set_option(self, device_id, key, value):
        self._ht.sync(self._hub.set_option, device_id, key, value)

    @_safe
    def set_startup(self, enabled):
        win.set_startup(bool(enabled))
        return {"startup": win.startup_enabled()}

    def hide(self):
        self._panel.hide()

    def quit(self):
        self._panel.quit()

    def fit(self, height):
        self._panel.fit(int(height))
