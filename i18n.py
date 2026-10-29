"""
i18n.py - Idiomas de PRTT.

Las traducciones viven en la carpeta  lang/  (junto a este archivo): un .json por idioma.
    lang/es.json   español (el idioma original del código: no necesita traducciones)
    lang/en.json   inglés: {"_meta": {...}, "strings": {"texto en español": "English text"}}

Cada texto del programa se escribe en español dentro del código y se pasa por  _t("...")  (o
_t("... {0}", valor) si lleva datos). Si el idioma elegido no tiene traducción para un texto, se
muestra el original en español. Para añadir un idioma nuevo basta con copiar lang/en.json a
lang/<código>.json, traducir los valores y poner el nombre en "_meta" -> "name".

El idioma sale, por orden, de: la variable de entorno PRTT_LANG -> «lang» en prtt_settings.json -> «es».
Cambiar de idioma se aplica al reiniciar PRTT (set_language() solo lo guarda).

No depende de Tkinter.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Dict, Optional

DEFAULT_LANG = "es"


def _tool_dir() -> str:
    if not getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(__file__))
    executable_dir = os.path.dirname(os.path.abspath(sys.executable))
    if sys.platform == "darwin":
        app_bundle = os.path.dirname(os.path.dirname(executable_dir))
        if app_bundle.endswith(".app"):
            return os.path.dirname(app_bundle)
    return executable_dir


TOOL_DIR = _tool_dir()
LANG_DIR = os.path.join(TOOL_DIR, "lang")
SETTINGS_FILE = os.path.join(TOOL_DIR, "prtt_settings.json")

_strings: Dict[str, str] = {}
_current = DEFAULT_LANG


def _read_json(path: str) -> dict:
    try:
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def available() -> Dict[str, str]:
    """{código: nombre} de los idiomas que hay en lang/ (siempre incluye el español)."""
    found: Dict[str, str] = {}
    try:
        names = sorted(os.listdir(LANG_DIR))
    except OSError:
        names = []
    for fname in names:
        if not fname.lower().endswith(".json"):
            continue
        code = fname[:-5].lower()
        meta = _read_json(os.path.join(LANG_DIR, fname)).get("_meta", {})
        found[code] = str(meta.get("name") or code) if isinstance(meta, dict) else code
    found.setdefault(DEFAULT_LANG, "Español")
    return found


def _wanted() -> str:
    env = os.environ.get("PRTT_LANG", "").strip().lower()
    if env:
        return env
    saved = _read_json(SETTINGS_FILE).get("lang", "")
    return saved.strip().lower() if isinstance(saved, str) and saved.strip() else DEFAULT_LANG


def load(code: Optional[str] = None) -> str:
    """Carga un idioma (o el guardado). Devuelve el código realmente cargado."""
    global _strings, _current
    code = (code or _wanted()).lower()
    table: Dict[str, str] = {}
    if code != DEFAULT_LANG:
        data = _read_json(os.path.join(LANG_DIR, code + ".json"))
        raw = data.get("strings", {})
        if isinstance(raw, dict) and raw:
            table = {str(k): str(v) for k, v in raw.items()}
        else:
            code = DEFAULT_LANG                       # sin archivo o vacío: español
    _strings, _current = table, code
    return code


def current() -> str:
    return _current


def tr(text: str, *args: object) -> str:
    """Traduce `text` (en español) y, si hay `args`, rellena {0}, {1}... con ellos."""
    out = _strings.get(text, text)
    if not args:
        return out
    try:
        return out.format(*args)
    except (IndexError, KeyError, ValueError):
        try:
            return text.format(*args)
        except (IndexError, KeyError, ValueError):
            return out


def set_language(code: str) -> bool:
    """Guarda el idioma elegido en prtt_settings.json (sin tocar el resto). Se aplica al reiniciar."""
    code = (code or "").lower()
    if code not in available():
        return False
    data = _read_json(SETTINGS_FILE)
    data["lang"] = code
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
    except OSError:
        return False
    return True


load()
_t = tr
