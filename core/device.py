"""Contrato que todo driver de dispositivo segue.

Para suportar um dispositivo novo (lâmpada, tomada, ar-condicionado...), crie
um arquivo em drivers/ com uma subclasse de Device decorada com @register.
O terminal e o painel da bandeja montam os controles a partir de actions(),
então nenhuma interface precisa ser alterada.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# Categorias conhecidas pela interface (definem o ícone). Use "other" se nenhuma servir.
CATEGORIES = ("light", "plug", "climate", "sensor", "other")


@dataclass
class Param:
    key: str
    label: str
    kind: str  # "int" | "choice" | "color" | "bool"
    min: int = 0
    max: int = 100
    unit: str = ""
    step: int = 1
    choices: list[tuple[str, Any]] = field(default_factory=list)


@dataclass
class Action:
    """Algo que o usuário pode fazer. As chaves "on" e "off" são especiais:
    viram o botão de liga/desliga do bloco e entram em "Ligar/Desligar tudo"."""
    key: str
    label: str
    params: list[Param] = field(default_factory=list)


@dataclass
class Advert:
    """Um dispositivo encontrado por uma Discovery, ainda não salvo."""
    transport: str
    address: str
    name: str
    signal: int | None = None  # dBm no BLE; None quando não se aplica
    service_uuids: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


DRIVERS: dict[str, type[Device]] = {}


def register(cls: type[Device]) -> type[Device]:
    DRIVERS[cls.driver_id] = cls
    return cls


def best_driver(adv: Advert) -> type[Device] | None:
    """Escolhe o driver do mesmo transporte com maior pontuação."""
    best, best_score = None, 0
    for cls in DRIVERS.values():
        if cls.transport != adv.transport:
            continue
        score = cls.match(adv)
        if score > best_score:
            best, best_score = cls, score
    return best


class Device:
    driver_id = ""
    driver_label = ""
    transport = ""  # "ble", "wifi", "ir"... precisa casar com uma Discovery
    category = "other"

    def __init__(self, address: str, name: str, alias: str = "", model: str | None = None,
                 config: dict | None = None, state: dict | None = None):
        self.address = address
        self.name = name
        self.alias = alias
        self.model = model
        self.config = config or {}  # dados do driver que precisam persistir (IP, token...)
        self.state = state or {}  # último estado conhecido, para a interface
        self.on_change = None  # o Hub coloca aqui um aviso; chame quando algo mudar sozinho (ex.: queda)

    @property
    def id(self) -> str:
        return self.address

    @property
    def label(self) -> str:
        return self.alias or self.name

    @classmethod
    def match(cls, adv: Advert) -> int:
        """Pontuação > 0 se este driver sabe falar com o dispositivo anunciado."""
        return 0

    @classmethod
    def models(cls) -> list[str]:
        """Variações de protocolo que o usuário pode escolher manualmente."""
        return []

    @property
    def connected(self) -> bool:
        raise NotImplementedError

    async def connect(self) -> None:
        raise NotImplementedError

    async def disconnect(self) -> None:
        raise NotImplementedError

    async def wait_idle(self) -> None:
        """Espera uma conexão em andamento terminar (usado ao encerrar o app)."""

    def actions(self) -> list[Action]:
        raise NotImplementedError

    async def run(self, action_key: str, **values: Any) -> None:
        raise NotImplementedError

    @property
    def supports_power(self) -> bool:
        keys = {a.key for a in self.actions()}
        return {"on", "off"} <= keys

    def remember(self, action_key: str, values: dict[str, Any]) -> None:
        """Atualiza self.state depois de um comando bem-sucedido. Sobrescreva se precisar."""
        if action_key in ("on", "off"):
            self.state["on"] = action_key == "on"
        elif len(values) == 1:
            self.state[action_key] = next(iter(values.values()))
        elif values:
            self.state[action_key] = values

    def options(self) -> list[Param]:
        """Ajustes do aparelho mostrados nas Configurações. Os valores ficam em self.config[param.key]."""
        return []

    @property
    def connecting(self) -> bool:
        """True enquanto uma conexão está em andamento (o painel mostra um indicador)."""
        return False

    def swatch(self) -> str | None:
        """Cor (#rrggbb) para a bolinha do bloco, ou None. Útil para luzes."""
        return None

    def summary(self) -> str:
        """Linha curta mostrada no bloco do dispositivo."""
        on = self.state.get("on")
        if on is None:
            return "Estado desconhecido"
        return "Ligado" if on else "Desligado"

    def to_dict(self) -> dict[str, Any]:
        """Formato enviado para a interface."""
        actions = self.actions()
        keys = {a.key for a in actions}
        return {
            "id": self.id, "label": self.label, "alias": self.alias, "name": self.name, "address": self.address,
            "driver": self.driver_id, "driver_label": self.driver_label, "category": self.category,
            "model": self.model, "models": self.models(), "connected": self.connected,
            "connecting": self.connecting, "swatch": self.swatch(),
            "state": self.state, "summary": self.summary(), "supports_power": {"on", "off"} <= keys,
            "actions": [asdict(a) for a in actions],
            "options": [asdict(p) for p in self.options()], "config": self.config,
        }
