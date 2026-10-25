"""Detalhes do Windows usados pela bandeja: posição, aparência, inicialização e instância única."""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import winreg
from ctypes import wintypes
from pathlib import Path

from PIL import Image, ImageDraw

user32 = ctypes.windll.user32
dwmapi = ctypes.windll.dwmapi
kernel32 = ctypes.windll.kernel32

# Tipos explícitos: sem eles, handles de 64 bits podem estourar o int padrão do ctypes.
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = wintypes.LONG
user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.LONG]
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowW.restype = wintypes.HWND
dwmapi.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]

ROOT = Path(__file__).resolve().parent.parent
STARTUP_LNK = Path(os.environ["APPDATA"]) / r"Microsoft\Windows\Start Menu\Programs\Startup\SmartDevices.lnk"
ICON_ICO = ROOT / "ui" / "icon.ico"


def work_area() -> tuple[int, int, int, int]:
    """Área útil da tela principal (sem a barra de tarefas), em pixels físicos."""
    rect = wintypes.RECT()
    user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)  # SPI_GETWORKAREA
    return rect.left, rect.top, rect.right, rect.bottom


def dpi_scale() -> float:
    try:
        return user32.GetDpiForSystem() / 96
    except AttributeError:
        return 1.0


def _personalize(name: str) -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            return bool(winreg.QueryValueEx(key, name)[0])
    except OSError:
        return True


def light_theme() -> bool:
    """Tema dos aplicativos (o painel)."""
    return _personalize("AppsUseLightTheme")


def light_taskbar() -> bool:
    """Tema da barra de tarefas, que pode ser diferente do dos aplicativos."""
    return _personalize("SystemUsesLightTheme")


def accent_colors() -> dict | None:
    """Cor de destaque escolhida no Windows, nas variações que o Windows 11 usa em cada tema
    (AccentPalette: 8 cores RGBA, do mais claro ao mais escuro)."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\Accent") as key:
            pal = winreg.QueryValueEx(key, "AccentPalette")[0]
    except OSError:
        return None
    if len(pal) < 32:
        return None
    color = lambda i: "#%02x%02x%02x" % tuple(pal[i * 4:i * 4 + 3])
    return {"light": color(4), "dark": color(1)}  # AccentDark1 no tema claro, AccentLight2 no escuro


def style_as_flyout(hwnd: int) -> None:
    """Cantos arredondados do Windows 11 e fora da barra de tarefas / Alt+Tab."""
    corner = ctypes.c_int(2)  # DWMWCP_ROUND
    dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(corner), ctypes.sizeof(corner))
    dark = ctypes.c_int(0 if light_theme() else 1)
    dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(dark), ctypes.sizeof(dark))  # borda escura
    GWL_EXSTYLE, WS_EX_TOOLWINDOW, WS_EX_APPWINDOW = -20, 0x80, 0x40000
    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, (style | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW)


def find_window(title: str) -> int | None:
    return user32.FindWindowW(None, title)


def foreground_is(hwnd: int) -> bool:
    fg = user32.GetForegroundWindow()
    return fg == hwnd or user32.GetAncestor(fg, 2) == hwnd  # GA_ROOT


def bring_to_front(hwnd: int) -> None:
    user32.SetForegroundWindow(hwnd)


def single_instance(name: str = "Local\\SmartDevicesTray") -> bool:
    """True se esta é a única cópia rodando. Mantém o mutex vivo até o processo sair."""
    global _mutex
    _mutex = kernel32.CreateMutexW(None, False, name)
    return kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


def message_box(text: str, title: str = "SmartDevices") -> None:
    user32.MessageBoxW(None, text, title, 0x40)


def promote_tray_icon(tooltip_prefix: str) -> bool:
    """O Windows 11 esconde ícones novos na seta "^". Mostra o nosso na barra, mas só se o
    usuário ainda não escolheu nada para ele (sem valor IsPromoted gravado)."""
    changed = False
    base = r"Control Panel\NotifyIconSettings"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, base) as root:
            for i in range(winreg.QueryInfoKey(root)[0]):
                name = winreg.EnumKey(root, i)
                with winreg.OpenKey(root, name, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE) as key:
                    try:
                        tip = winreg.QueryValueEx(key, "InitialTooltip")[0]
                    except OSError:
                        continue
                    if not str(tip).startswith(tooltip_prefix):
                        continue
                    try:
                        winreg.QueryValueEx(key, "IsPromoted")
                    except FileNotFoundError:
                        winreg.SetValueEx(key, "IsPromoted", 0, winreg.REG_DWORD, 1)
                        changed = True
    except OSError:
        pass
    return changed


# ---------- iniciar com o Windows ----------
def startup_enabled() -> bool:
    return STARTUP_LNK.exists()


def set_startup(enabled: bool) -> None:
    if not enabled:
        STARTUP_LNK.unlink(missing_ok=True)
        return
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    ps = (
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:LNK);"
        "$s.TargetPath=$env:TARGET;$s.Arguments=$env:ARGS;$s.WorkingDirectory=$env:WD;"
        "$s.IconLocation=$env:ICON;$s.Description='SmartDevices';$s.Save()"
    )
    env = dict(os.environ, LNK=str(STARTUP_LNK), TARGET=str(pythonw), ARGS=f'"{ROOT / "tray.py"}" --tray',
               WD=str(ROOT), ICON=str(ICON_ICO))
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], env=env, check=True,
                   creationflags=subprocess.CREATE_NO_WINDOW, timeout=30)


# ---------- ícone ----------
LIGHT_ON = (255, 176, 46)


def make_icon(state: str = "on", light_bar: bool | None = None, size: int = 64,
              stroke: tuple | None = None) -> Image.Image:
    """Torre de luz de uma cor só, como os ícones do sistema na bandeja.

    state: "on" (ponto laranja: há luz acesa), "idle" (tudo desligado) ou "off" (sem conexão, apagado).
    """
    if light_bar is None:
        light_bar = light_taskbar()
    if stroke is None:
        stroke = (27, 27, 27) if light_bar else (255, 255, 255)
    k = 256 / 16  # desenha numa grade de 16 px ampliada e reduz no fim (bordas suaves)
    big = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    d = ImageDraw.Draw(big)
    w = round(1.5 * k)
    d.rounded_rectangle([5.4 * k, 1.2 * k, 10.6 * k, 12.4 * k], radius=2.6 * k, outline=stroke, width=w)
    def rounded_line(x1, y1, x2, y2, fill):
        d.line([x1, y1, x2, y2], fill=fill, width=w)
        for x, y in ((x1, y1), (x2, y2)):
            d.ellipse([x - w / 2, y - w / 2, x + w / 2, y + w / 2], fill=fill)

    rounded_line(8 * k, 4.6 * k, 8 * k, 9 * k, LIGHT_ON if state == "on" else stroke)  # a luz da torre
    rounded_line(4.4 * k, 14.6 * k, 11.6 * k, 14.6 * k, stroke)  # base
    if state == "on":
        d.ellipse([10.4 * k, 0.2 * k, 15.8 * k, 5.6 * k], fill=(0, 0, 0, 0))  # recorte em volta do ponto
        d.ellipse([11.3 * k, 1.1 * k, 14.9 * k, 4.7 * k], fill=LIGHT_ON)
    if state == "off":
        alpha = big.getchannel("A").point(lambda a: a * 45 // 100)
        big.putalpha(alpha)
    return big.resize((size, size), Image.LANCZOS)


def ensure_ico() -> None:
    """Ícone do atalho (aparece fora da bandeja, então usa a torre acesa em laranja)."""
    make_icon("on", size=256, stroke=LIGHT_ON).save(
        ICON_ICO, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (256, 256)])
