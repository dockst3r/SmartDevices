"""Persistência: dispositivos salvos (devices.json) e preferências (settings.json)."""
from __future__ import annotations

import json
from pathlib import Path

from .device import DRIVERS, Device

ROOT = Path(__file__).resolve().parent.parent
DEVICES = ROOT / "devices.json"
SETTINGS = ROOT / "settings.json"


def _read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _write(path: Path, data) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)  # troca atômica: um travamento no meio não corrompe o arquivo


def load() -> list[Device]:
    devices = []
    for entry in _read(DEVICES, []):
        cls = DRIVERS.get(entry["driver"])
        if cls is None:
            continue  # driver removido; ignora até alguém salvar de novo
        devices.append(cls(entry["address"], entry["name"], entry.get("alias", ""), entry.get("model"),
                           entry.get("config"), entry.get("state")))
    return devices


def save(devices: list[Device]) -> None:
    _write(DEVICES, [
        {"address": d.address, "name": d.name, "alias": d.alias, "driver": d.driver_id,
         "model": d.model, "config": d.config, "state": d.state}
        for d in devices
    ])


def load_settings() -> dict:
    return _read(SETTINGS, {})


def save_settings(settings: dict) -> None:
    _write(SETTINGS, settings)
