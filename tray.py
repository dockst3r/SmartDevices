"""SmartDevices na bandeja do Windows.

    pythonw tray.py          abre o painel ao iniciar
    pythonw tray.py --tray   começa só com o ícone (usado ao iniciar com o Windows)

Três threads: a da janela (pywebview, precisa ser a principal), a do ícone
(pystray) e a do Hub (asyncio + Bluetooth). As lâmpadas continuam conectadas
com o painel escondido; só "Sair" desconecta e encerra.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

if sys.stderr is None:  # pythonw não tem console
    sys.stdout = sys.stderr = open(os.devnull, "w", encoding="utf-8")
logging.basicConfig(
    level=logging.DEBUG if os.environ.get("SMART_DEBUG") else logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[RotatingFileHandler(ROOT / "smartdevices.log", maxBytes=500_000, backupCount=1, encoding="utf-8")],
)
log = logging.getLogger("smartdevices")

import pystray  # noqa: E402
import webview  # noqa: E402

from core.hub import Hub, HubThread  # noqa: E402
from ui import win  # noqa: E402
from ui.api import Api  # noqa: E402

TITLE = "SmartDevices"
WIDTH = 360
MARGIN = 12


class Panel:
    def __init__(self, start_hidden: bool):
        self.hub = Hub()
        self.ht = HubThread(self.hub)
        self.api = Api(self.ht, self)
        self.height = 560
        self.visible = False
        self._start_hidden = start_hidden
        self._hwnd: int | None = None
        self._hidden_at = 0.0
        self._had_focus = False
        self._quitting = False
        self._push_lock = threading.Lock()
        self._push_pending = False

        win.ensure_ico()
        self.window = webview.create_window(
            TITLE, str(ROOT / "ui" / "index.html"), js_api=self.api, width=WIDTH, height=self.height,
            frameless=True, easy_drag=False, resizable=False, on_top=True, hidden=True,
            background_color="#f3f3f3" if win.light_theme() else "#202020",
        )
        self.window.events.closing += self._on_closing
        self.hub.listeners.append(self._push)
        self._icon_key = ("off", win.light_taskbar())
        self.icon = pystray.Icon("SmartDevices", win.make_icon(*self._icon_key), TITLE, menu=self._menu())

    # ---------- bandeja ----------
    def _menu(self) -> pystray.Menu:
        item = pystray.MenuItem
        return pystray.Menu(
            item("Abrir painel", lambda: self.toggle(), default=True),
            item("Ligar tudo", lambda: self._power(True)),
            item("Desligar tudo", lambda: self._power(False)),
            pystray.Menu.SEPARATOR,
            item("Procurar dispositivos", lambda: self.show("scan")),
            item("Iniciar com o Windows", lambda: self._toggle_startup(), checked=lambda _: win.startup_enabled()),
            pystray.Menu.SEPARATOR,
            item("Sair", lambda: self.quit()),
        )

    def _power(self, on: bool) -> None:
        def done(fut):
            try:
                failed = [r for r in fut.result() if not r["ok"]]
            except Exception as e:
                failed = [{"label": "SmartDevices", "error": str(e)}]
            if failed:
                self.icon.notify("\n".join(f"{r['label']}: {r['error']}" for r in failed), "Não deu certo")
        self.ht.submit(self.hub.power_all(on)).add_done_callback(done)

    def _toggle_startup(self) -> None:
        try:
            win.set_startup(not win.startup_enabled())
        except Exception as e:
            log.exception("startup")
            self.icon.notify(str(e), "Não foi possível alterar a inicialização")
        self._push()

    def _update_tooltip(self) -> None:
        devices = self.hub.devices
        connected = [d for d in devices if d.connected]
        lit = sum(1 for d in connected if d.state.get("on"))
        if devices:
            self.icon.title = f"{TITLE} · {len(connected)} de {len(devices)} conectados · {lit} ligados"
        self._refresh_icon("off" if not connected else "on" if lit else "idle")

    def _refresh_icon(self, state: str | None = None) -> None:
        """Troca a imagem só quando o estado ou o tema da barra de tarefas mudar."""
        state = state or self._icon_key[0]
        key = (state, win.light_taskbar())
        if key != self._icon_key:
            self._icon_key = key
            self.icon.icon = win.make_icon(state, key[1])

    # ---------- janela ----------
    def _started(self) -> None:
        for _ in range(50):
            self._hwnd = win.find_window(TITLE)
            if self._hwnd:
                break
            time.sleep(0.1)
        if self._hwnd:
            win.style_as_flyout(self._hwnd)
        threading.Thread(target=self._watch_focus, name="focus", daemon=True).start()
        threading.Thread(target=self._promote_icon, name="promote", daemon=True).start()
        self.ht.submit(self.hub.keep_connected())
        if not self._start_hidden:
            time.sleep(0.5)  # dá tempo do painel carregar antes de pedir uma tela específica
            self.show(os.environ.get("SMART_OPEN_VIEW"))

    def _promote_icon(self) -> None:
        if self.hub.settings.get("tray_promoted"):
            return
        for _ in range(20):  # o Windows cria a entrada do ícone alguns segundos depois
            time.sleep(1)
            if win.promote_tray_icon(TITLE):
                log.info("ícone da bandeja tornado visível")
                break
        self.hub.set_setting("tray_promoted", True)

    def _place(self) -> None:
        left, top, right, bottom = win.work_area()
        scale = win.dpi_scale()
        x = int(right / scale) - WIDTH - MARGIN
        y = int(bottom / scale) - self.height - MARGIN
        self.window.move(max(int(left / scale), x), max(int(top / scale), y))

    def show(self, view: str | None = None) -> None:
        log.debug("mostrando painel (hwnd=%s)", self._hwnd)
        self._place()
        self.window.show()
        if self._hwnd:
            win.bring_to_front(self._hwnd)
        self._had_focus = False
        self.visible = True
        self._js(f"window.app && app.onShow({json.dumps(view)})")

    def hide(self) -> None:
        if self.visible:
            self.visible = False
            self._hidden_at = time.monotonic()
            self.window.hide()

    def toggle(self) -> None:
        if self.visible:
            self.hide()
        elif time.monotonic() - self._hidden_at > 0.5:  # o clique no ícone já escondeu o painel
            self.show()

    def fit(self, height: int) -> None:
        log.debug("fit pedido: %s px (atual %s)", height, self.height)
        _, top, _, bottom = win.work_area()
        height = max(240, min(height, int((bottom - top) / win.dpi_scale()) - 2 * MARGIN))
        if height != self.height:
            self.height = height
            self.window.resize(WIDTH, height)
            self._place()

    def _watch_focus(self) -> None:
        """Esconde o painel quando outra janela ganha o foco, como os painéis do Windows 11.
        Também confere a cada ~3 s se o tema da barra mudou, para trocar a cor do ícone."""
        ticks = 0
        while not self._quitting:
            time.sleep(0.15)
            ticks += 1
            if ticks % 20 == 0:
                self._refresh_icon()
            if not (self.visible and self._hwnd):
                continue
            if win.foreground_is(self._hwnd):
                self._had_focus = True
            elif self._had_focus:
                log.debug("painel perdeu o foco")
                self.hide()

    def _on_closing(self):
        if self._quitting:
            return True
        self.hide()  # Alt+F4 só esconde
        return False

    def _push(self) -> None:
        """Avisa o painel que algo mudou. Agrupa rajadas (sliders) em uma atualização."""
        self._update_tooltip()
        with self._push_lock:
            if self._push_pending:
                return
            self._push_pending = True

        def later():
            time.sleep(0.2)
            with self._push_lock:
                self._push_pending = False
            if self.visible:
                self._js("window.app && app.refresh()")
        threading.Thread(target=later, daemon=True).start()

    def _js(self, code: str) -> None:
        # Fora da thread chamadora: evaluate_js espera a janela, e a janela pode estar esperando o Hub.
        threading.Thread(target=lambda: self._safe_js(code), daemon=True).start()

    def _safe_js(self, code: str) -> None:
        try:
            self.window.evaluate_js(code)
        except Exception:
            log.debug("evaluate_js falhou", exc_info=True)

    # ---------- ciclo de vida ----------
    def run(self) -> None:
        self.icon.run_detached()
        self._update_tooltip()
        webview.start(self._started)

    def quit(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        log.info("encerrando")
        try:
            self.ht.stop()
        except Exception:
            log.exception("ao desconectar")
        self.icon.stop()
        self.window.destroy()


def main() -> None:
    if not win.single_instance():
        win.message_box("O SmartDevices já está aberto. Procure o ícone da torre de luz perto do relógio.")
        return
    log.info("iniciando")
    try:
        Panel(start_hidden="--tray" in sys.argv).run()
    except Exception:
        log.exception("erro fatal")
        win.message_box(f"O SmartDevices encontrou um erro. Detalhes em:\n{ROOT / 'smartdevices.log'}")
    os._exit(0)  # garante que threads do Bluetooth/ícone não segurem o processo


if __name__ == "__main__":
    main()
