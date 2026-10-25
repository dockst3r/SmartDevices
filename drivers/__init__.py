"""Importa todos os drivers desta pasta para que se registrem sozinhos."""
import importlib
import pkgutil

for _mod in pkgutil.iter_modules(__path__):
    importlib.import_module(f"{__name__}.{_mod.name}")
