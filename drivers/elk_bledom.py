"""Lâmpadas/fitas do app Lotus Lamp / Lotus Lamp X / Lotus Lantern (protocolo ELK-BLEDOM).

A tabela de modelos e efeitos em elk_data/ vem do projeto MIT
https://github.com/dave-code-ruiz/elkbledom (ver elk_data/LICENSE).
Nos templates de comando: i = intensidade %, r/g/b = cor, w/c = % quente/frio, v = valor.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from bleak import BleakClient
from bleak.exc import BleakError

from core.ble import BleDevice
from core.device import Action, Advert, Param, register

DATA = Path(__file__).resolve().parent / "elk_data"
_models_raw = json.loads((DATA / "models.json").read_text(encoding="utf-8"))
_definitions = json.loads((DATA / "definitions.json").read_text(encoding="utf-8"))["effects_definitions"]


def _model_key(m: dict) -> str:
    # Há nomes repetidos (ELK-BLEDOM) que só se distinguem pelo handle.
    return f"{m['name']}#h{m['handle']}" if "handle" in m else m["name"]


MODELS: dict[str, dict] = {_model_key(m): m for m in _models_raw}
FALLBACK_MODEL = "ELK-BLEDOM"
FFF0_SERVICE = "0000fff0-0000-1000-8000-00805f9b34fb"
LOGIN_PREFIXES = ("melk", "modelx")

_PT = {
    "jump": "pular", "crossfade": "transição", "blink": "piscar", "chase": "perseguir",
    "red": "vermelho", "green": "verde", "blue": "azul", "yellow": "amarelo", "cyan": "ciano",
    "magenta": "magenta", "white": "branco", "turquoise": "turquesa", "multicolor": "multicolor",
    "reverse": "reverso",
}


def _effect_label(name: str) -> str:
    words = name.split("_")
    if all(w in _PT for w in words):
        head, *colors = [_PT[w] for w in words]
        return f"{head.capitalize()} " + ", ".join(colors) if colors else head.capitalize()
    return name.replace("_", " ")


# Os brancos são misturas RGB: o comando nativo de temperatura (color_temp) só age em
# lâmpadas com LEDs brancos quente/frio separados, o que as torres RGB não têm.
COLOR_PRESETS = [
    ("branco quente", (255, 147, 41)), ("branco neutro", (255, 214, 170)), ("branco frio", (201, 226, 255)),
    ("vermelho", (255, 0, 0)), ("laranja", (255, 90, 0)), ("amarelo", (255, 200, 0)),
    ("verde", (0, 255, 0)), ("ciano", (0, 255, 255)), ("azul", (0, 0, 255)),
    ("roxo", (140, 0, 255)), ("rosa", (255, 40, 120)),
]


def _models_for_name(name: str) -> list[str]:
    """Chaves de modelo cujo prefixo casa com o nome anunciado, mais específico primeiro."""
    lower = name.lower()
    keys = [k for k, m in MODELS.items() if lower.startswith(m["name"].lower())]
    return sorted(keys, key=lambda k: len(MODELS[k]["name"]), reverse=True)


@register
class ElkBledom(BleDevice):
    driver_id = "elk_bledom"
    driver_label = "Lotus Lamp (ELK-BLEDOM)"
    category = "light"
    # Comandos com ~1 ms de intervalo são descartados pelo controlador; 300 ms é o que funciona
    # (github.com/dave-code-ruiz/elkbledom/issues/157).
    write_interval = 0.3

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._write_uuid: str | None = None

    @classmethod
    def match(cls, adv: Advert) -> int:
        candidates = _models_for_name(adv.name)
        if candidates:
            return 10 + len(MODELS[candidates[0]]["name"])
        return 1 if FFF0_SERVICE in adv.service_uuids else 0

    def summary(self) -> str:
        on = self.state.get("on")
        if on is None:
            return "Estado desconhecido"
        if not on:
            return "Desligada"
        return f"Ligada · {self.state['brightness']}%" if "brightness" in self.state else "Ligada"

    @classmethod
    def models(cls) -> list[str]:
        return list(MODELS)

    @property
    def spec(self) -> dict:
        return MODELS.get(self.model) or MODELS[FALLBACK_MODEL]

    def _detect_model(self, client: BleakClient) -> str:
        candidates = _models_for_name(self.name)
        if not candidates:
            return FALLBACK_MODEL
        longest = MODELS[candidates[0]]["name"]
        same_name = [k for k in candidates if MODELS[k]["name"] == longest]
        char = client.services.get_characteristic(MODELS[same_name[0]]["write_uuid"])
        handle = char.handle if char else None
        for k in same_name:
            if MODELS[k].get("handle") == handle:
                return k
        return next((k for k in same_name if "handle" not in MODELS[k]), same_name[0])

    async def on_connected(self, client: BleakClient) -> None:
        if self.model not in MODELS:
            self.model = self._detect_model(client)
        uuid = self.spec["write_uuid"]
        if client.services.get_characteristic(uuid) is None:
            # BleakError (e não ConnectionError) para ganhar nova tentativa: logo após uma conexão
            # interrompida o Windows às vezes devolve a lista de serviços vazia.
            raise BleakError(f"{self.label}: característica de escrita {uuid} não encontrada.")
        self._write_uuid = uuid
        if self.name.lower().startswith(LOGIN_PREFIXES):
            await client.write_gatt_char(uuid, bytes([0x7E, 0x07, 0x83]), response=False)
            await asyncio.sleep(1)
            await client.write_gatt_char(uuid, bytes([0x7E, 0x04, 0x04]), response=False)
            await asyncio.sleep(1)
        self._last_write = asyncio.get_running_loop().time()

    def options(self) -> list[Param]:
        return [Param("brightness_mode", "Como ajustar o brilho", "choice", choices=[
            ("Comando de brilho da lâmpada", "native"),
            ("Escurecendo a cor (para modelos que ignoram o comando)", "rgb"),
        ])]

    @property
    def brightness_by_color(self) -> bool:
        return self.config.get("brightness_mode") == "rgb"

    def _scaled_color(self, rgb, pct: int) -> dict:
        r, g, b = (round(c * pct / 100) for c in rgb)
        return {"r": r, "g": g, "b": b}

    def _effects(self) -> list[tuple[str, int]]:
        table = _definitions.get(self.spec.get("effects_class", "EFFECTS"), {})
        return sorted(((_effect_label(n), v) for n, v in table.items()), key=lambda e: e[1])

    def actions(self) -> list[Action]:
        cmds = self.spec["commands"]
        actions = [Action("on", "Ligar"), Action("off", "Desligar")]
        if "color" in cmds:
            actions.append(Action("color", "Cor", [Param("rgb", "Cor", "color", choices=COLOR_PRESETS)]))
        if "brightness" in cmds:
            actions.append(Action("brightness", "Brilho", [Param("pct", "Brilho", "int", 0, 100, "%")]))
        if "effect" in cmds and self._effects():
            actions.append(Action("effect", "Efeito", [Param("code", "Efeito", "choice", choices=self._effects())]))
        if "effect_speed" in cmds:
            actions.append(Action("speed", "Velocidade do efeito", [Param("pct", "Velocidade", "int", 0, 100, "%")]))
        return actions

    def _build(self, command: str, **vals: int) -> bytes:
        template = self.spec["commands"][command]
        return bytes(vals[x] if isinstance(x, str) else x for x in template)

    async def run(self, action_key: str, **values) -> None:
        if action_key == "on":
            data = self._build("turn_on")
        elif action_key == "off":
            data = self._build("turn_off")
        elif action_key == "color":
            pct = self.state.get("brightness", 100) if self.brightness_by_color else 100
            data = self._build("color", **self._scaled_color(values["rgb"], pct))
        elif action_key == "brightness" and self.brightness_by_color:
            data = self._build("color", **self._scaled_color(self.state.get("color") or (255, 255, 255), values["pct"]))
        elif action_key == "brightness":
            data = self._build("brightness", i=values["pct"])
        elif action_key == "effect":
            data = self._build("effect", v=values["code"])
        elif action_key == "speed":
            rng = self.spec.get("effect_speed_range", {})
            lo, hi = rng.get("min", 0), rng.get("max", 100)
            data = self._build("effect_speed", v=round(lo + values["pct"] * (hi - lo) / 100))
        else:
            raise ValueError(f"Ação desconhecida: {action_key}")
        if self._write_uuid is None or not self.connected:
            await self.connect()
        await self.write(self._write_uuid, data)

    def swatch(self) -> str | None:
        rgb = self.state.get("color")
        return "#%02x%02x%02x" % tuple(rgb) if rgb and self.state.get("on") else None
