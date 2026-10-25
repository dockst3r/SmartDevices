"""SmartDevices no terminal: menu interativo sobre o Hub."""
from __future__ import annotations

import asyncio
import os
import sys
import threading

from core.device import Action, Device, Param
from core.hub import Hub

os.system("")  # ativa cores ANSI no console do Windows
B, G, Y, R, D, X = "\033[1m", "\033[92m", "\033[93m", "\033[91m", "\033[2m", "\033[0m"


async def ainput(prompt: str = "") -> str:
    """input() que não trava o loop (mantém as conexões vivas) e não impede o Ctrl+C."""
    loop = asyncio.get_running_loop()
    fut = loop.create_future()

    def deliver(setter, value):
        if not fut.done():
            setter(value)

    def worker():
        try:
            value = input(prompt)
            loop.call_soon_threadsafe(deliver, fut.set_result, value)
        except BaseException as e:  # EOFError, KeyboardInterrupt
            loop.call_soon_threadsafe(deliver, fut.set_exception, e)

    threading.Thread(target=worker, daemon=True).start()
    return (await fut).strip()


def header(title: str) -> None:
    print(f"\n{B}== {title} =={X}")


async def choose(title: str, options: list[str], back: str = "Voltar") -> int | None:
    """Mostra opções numeradas; devolve o índice escolhido ou None para voltar."""
    header(title)
    for i, opt in enumerate(options, 1):
        print(f"  {B}{i:>2}{X}. {opt}")
    print(f"  {B} 0{X}. {back}")
    while True:
        raw = await ainput("> ")
        if raw in ("", "0"):
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return int(raw) - 1
        print(f"{R}Opção inválida.{X}")


def parse_color(raw: str) -> tuple[int, int, int] | None:
    raw = raw.strip().lstrip("#")
    if "," in raw:
        parts = [p.strip() for p in raw.split(",")]
        if len(parts) == 3 and all(p.isdigit() and int(p) <= 255 for p in parts):
            return tuple(int(p) for p in parts)
        return None
    if len(raw) == 6:
        try:
            return tuple(int(raw[i:i + 2], 16) for i in (0, 2, 4))
        except ValueError:
            return None
    return None


async def ask_param(p: Param):
    if p.kind == "int":
        while True:
            raw = await ainput(f"{p.label} ({p.min}-{p.max}{p.unit}, vazio = cancelar): ")
            if raw == "":
                return None
            if raw.lstrip("-").isdigit() and p.min <= int(raw) <= p.max:
                return int(raw)
            print(f"{R}Digite um número entre {p.min} e {p.max}.{X}")
    if p.kind == "bool":
        idx = await choose(p.label, ["Sim", "Não"])
        return None if idx is None else idx == 0
    if p.kind == "choice":
        idx = await choose(p.label, [label for label, _ in p.choices])
        return None if idx is None else p.choices[idx][1]
    if p.kind == "color":
        header(p.label)
        for i, (label, rgb) in enumerate(p.choices, 1):
            print(f"  {B}{i:>2}{X}. \033[38;2;{rgb[0]};{rgb[1]};{rgb[2]}m██{X} {label}")
        print(f"  {D}ou digite #RRGGBB / R,G,B (ex: #ff8800 ou 255,136,0){X}")
        while True:
            raw = await ainput("> ")
            if raw in ("", "0"):
                return None
            if raw.isdigit() and 1 <= int(raw) <= len(p.choices):
                return p.choices[int(raw) - 1][1]
            if (rgb := parse_color(raw)) is not None:
                return rgb
            print(f"{R}Cor inválida.{X}")
    raise ValueError(f"Tipo de parâmetro sem suporte no terminal: {p.kind}")


def report(results: list[dict], verb: str) -> None:
    for r in results:
        if r["ok"]:
            print(f"  {G}✓ {r['label']}{X} {D}{verb}{X}")
        else:
            print(f"  {R}✗ {r['label']}: {r['error']}{X}")


def common_actions(devices: list[Device]) -> list[Action]:
    """Ações que todos os dispositivos do grupo entendem."""
    first = devices[0].actions()
    keys = set.intersection(*({a.key for a in d.actions()} for d in devices))
    return [a for a in first if a.key in keys]


async def control(hub: Hub, devices: list[Device]) -> None:
    title = devices[0].label if len(devices) == 1 else f"Grupo: {', '.join(d.label for d in devices)}"
    print(f"{D}Conectando...{X}")
    report(await hub.connect([d.id for d in devices]), "conectado")
    devices = [d for d in devices if d.connected]
    if not devices:
        return
    ids = [d.id for d in devices]
    while True:
        actions = common_actions(devices)
        idx = await choose(title, [a.label for a in actions])
        if idx is None:
            return
        action = actions[idx]
        values = {}
        for p in action.params:
            v = await ask_param(p)
            if v is None:
                break
            values[p.key] = v
        else:
            report(await hub.execute(ids, action.key, values), action.label.lower())


async def scan(hub: Hub) -> None:
    print(f"{D}Procurando dispositivos por 8 segundos... (feche o app no celular){X}")
    try:
        rows = await hub.scan(8)
    except Exception as e:
        print(f"{R}{e}{X}")
        return
    if not rows:
        print(f"{Y}Nada encontrado. O Bluetooth do PC está ligado?{X}")
        return
    while True:
        labels = []
        for r in rows:
            tag = f"{G}{r['driver_label']}{X}" if r["supported"] else f"{D}não suportado{X}"
            mark = f" {Y}[salvo]{X}" if r["saved"] else ""
            signal = f"{r['signal']} dBm" if r["signal"] is not None else r["transport"]
            labels.append(f"{r['name']:<22} {D}{r['address']}  {signal}{X}  {tag}{mark}")
        idx = await choose("Encontrados (escolha para salvar)", labels)
        if idx is None:
            return
        row = rows[idx]
        if not row["supported"] or row["saved"]:
            print(f"{Y}{'Já está salvo.' if row['saved'] else 'Ainda não há driver para esse dispositivo.'}{X}")
            continue
        alias = await ainput(f"Apelido (ex: Lâmpada esquerda) [{row['name']}]: ")
        hub.add(row["address"], alias)
        row["saved"] = True
        print(f"{G}Salvo!{X}")


async def manage(hub: Hub) -> None:
    while hub.devices:
        idx = await choose("Gerenciar dispositivos",
                           [f"{d.label} {D}({d.name}, modelo {d.model or 'auto'}){X}" for d in hub.devices])
        if idx is None:
            return
        dev = hub.devices[idx]
        ops = ["Renomear", "Trocar modelo (se os comandos não funcionarem)", "Remover"]
        options = dev.options()
        ops += [f"Ajuste: {p.label}" for p in options]
        op = await choose(dev.label, ops)
        if op is not None and op >= 3:
            p = options[op - 3]
            value = await ask_param(p)
            if value is not None:
                hub.set_option(dev.id, p.key, value)
                print(f"{G}Ajuste salvo.{X}")
        elif op == 0:
            if alias := await ainput("Novo apelido: "):
                hub.rename(dev.id, alias)
        elif op == 1:
            models = ["auto"] + type(dev).models()
            m = await choose("Modelo", models)
            if m is not None:
                await hub.set_model(dev.id, None if m == 0 else models[m])
        elif op == 2:
            if (await ainput(f"Remover {dev.label}? (s/N): ")).lower() == "s":
                await hub.remove(dev.id)


async def pick_devices(hub: Hub) -> list[Device] | None:
    options = [f"{d.label} {D}({d.driver_label}){X}" for d in hub.devices]
    if len(hub.devices) > 1:
        options.append(f"{B}Todos juntos{X}")
    idx = await choose("Qual dispositivo?", options)
    if idx is None:
        return None
    return list(hub.devices) if idx == len(hub.devices) else [hub.devices[idx]]


async def main() -> None:
    hub = Hub()
    print(f"{B}SmartDevices{X} {D}— controle pelo PC{X}")
    try:
        while True:
            options = ["Procurar e adicionar dispositivos"]
            if hub.devices:
                options += ["Controlar", "Ligar tudo", "Desligar tudo", "Gerenciar (renomear / modelo / remover)"]
            idx = await choose("Menu principal", options, back="Sair")
            if idx is None:
                break
            if idx == 0:
                await scan(hub)
            elif idx == 1:
                if devs := await pick_devices(hub):
                    await control(hub, devs)
            elif idx in (2, 3):
                report(await hub.power_all(idx == 2), "ligado" if idx == 2 else "desligado")
            elif idx == 4:
                await manage(hub)
    finally:
        await hub.shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, EOFError):
        pass
    print("Até mais!")
    sys.exit(0)
