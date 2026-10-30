"""
rpyc_reader.py - Lee un .rpyc (script compilado de Ren'Py) y devuelve su AST como objetos inertes.

FORMATOS
  RPC2 (Ren'Py 6.1x en adelante, incluido DDLC 6.99, 7.x y 8.x):
        b"RENPY RPC2" + tabla de ranuras  (<III: ranura, inicio, longitud)*  terminada por una ranura 0.
        La ranura 1 es un pickle comprimido con zlib:  (datos, [nodos])  con  datos = {"version": ..., "key": ...}.
        (La ranura 2 guarda el bytecode de Python; PRTT no la necesita.)
  RPC1 (Ren'Py antiguo): todo el archivo es el pickle comprimido con zlib.
  El pickle es de Python 2 (Ren'Py <= 7) o de Python 3 (Ren'Py 8), siempre protocolo 2.

POR QUÉ NO HACE FALTA RENPY
  Un pickle nombra las clases que contiene (renpy.ast.Say, renpy.ast.Label...). Aquí nunca se importa Ren'Py: el
  Unpickler fabrica, para cada nombre `renpy.*`, una clase vacía que solo guarda atributos. Así el mismo lector sirve
  para cualquier versión (los campos que falten o sobren no importan: se leen con getattr).

SEGURIDAD
  Un .rpyc de un mod descargado es un pickle: abrirlo con pickle.load normal podría ejecutar código. Este Unpickler
  solo acepta (1) clases `renpy.*`, que se sustituyen por clases inertes, y (2) una lista corta de nombres inofensivos
  (set, frozenset, object, bytes vacío, OrderedDict, _codecs.encode). Cualquier otra cosa se rechaza.
  El tamaño descomprimido tiene tope.

No depende de Tkinter ni de Pillow.
"""
from __future__ import annotations

import io
import pickle
import struct
import zlib
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from i18n import _t

MAGIC = b"RENPY RPC2"
_MAX_RAW = 512 * 1024 * 1024


class RpycError(Exception):
    pass


# --------------------------------------------------------------------------------------
# Clases inertes que sustituyen a las de Ren'Py
# --------------------------------------------------------------------------------------
class AstNode:
    """Nodo del AST: solo guarda los atributos que traía el pickle."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def __setstate__(self, state: Any) -> None:
        if isinstance(state, dict):
            self.__dict__.update(state)
        elif isinstance(state, tuple) and len(state) == 2 and all(s is None or isinstance(s, dict) for s in state):
            for part in state:                      # (estado, estado_de___slots__): Ren'Py usa __slots__
                if part:
                    self.__dict__.update(part)
        else:
            self.__dict__["_state"] = state

    def __repr__(self) -> str:
        return f"<{type(self).__name__} línea {getattr(self, 'linenumber', '?')}>"


class PyExpr(str):
    """Expresión de Python tal como se escribió en el script (Ren'Py la guarda como str con ubicación)."""

    def __new__(cls, s: Any = "", *rest: Any, **kwargs: Any) -> "PyExpr":
        # Ren'Py cambia los argumentos de PyExpr entre versiones (8.5: 7): solo el texto importa, el resto se tolera.
        obj = super().__new__(cls, s if isinstance(s, str) else str(s))
        obj.filename = rest[0] if len(rest) > 0 else kwargs.get("filename")
        obj.linenumber = rest[1] if len(rest) > 1 else kwargs.get("linenumber")
        obj.py = rest[2] if len(rest) > 2 else kwargs.get("py")
        return obj

    def __setstate__(self, state: Any) -> None:       # no aporta nada
        pass


class PyCode(AstNode):
    """Código Python de `$`, `python:`, `define`...  Estado: (1, fuente, ubicación, modo[, py])."""
    source: Any = ""
    location: Any = None
    mode: str = "exec"

    def __setstate__(self, state: Any) -> None:
        if isinstance(state, tuple) and len(state) >= 4 and isinstance(state[0], int):
            self.source, self.location, self.mode = state[1], state[2], state[3]
        else:
            super().__setstate__(state)


class RevertableList(list):
    def __setstate__(self, state: Any) -> None:
        pass


class RevertableDict(dict):
    def __setstate__(self, state: Any) -> None:
        pass


class RevertableSet(set):
    def __setstate__(self, state: Any) -> None:
        pass


_SPECIAL = {
    "PyExpr": PyExpr, "PyCode": PyCode,
    "RevertableList": RevertableList, "RevertableDict": RevertableDict, "RevertableSet": RevertableSet,
}
_stubs: Dict[tuple, type] = {}


def _empty_bytes(*args: Any) -> bytes:
    if args:
        raise pickle.UnpicklingError(_t("bytes() con argumentos"))
    return b""


def _newobj(cls: Any, *args: Any) -> Any:
    return cls.__new__(cls, *args)


_SAFE = {
    ("__builtin__", "set"): set, ("builtins", "set"): set,
    ("__builtin__", "frozenset"): frozenset, ("builtins", "frozenset"): frozenset,
    ("__builtin__", "object"): object, ("builtins", "object"): object,
    ("__builtin__", "bytes"): _empty_bytes, ("builtins", "bytes"): _empty_bytes,
    ("collections", "OrderedDict"): OrderedDict,
    ("copy_reg", "__newobj__"): _newobj, ("copyreg", "__newobj__"): _newobj,
}


_DANGEROUS_MODULES = {"os", "posix", "nt", "subprocess", "sys", "shutil", "socket", "builtins", "__builtin__",
                      "importlib", "ctypes", "pty", "commands", "popen2", "webbrowser", "runpy", "code", "codeop",
                      "marshal", "types", "operator", "functools", "itertools", "pickle", "cPickle"}
_DANGEROUS_NAMES = {"eval", "exec", "compile", "open", "__import__", "getattr", "setattr", "system", "popen"}


_HARMLESS_BUILTINS = {m: {"list": list, "dict": dict, "tuple": tuple, "str": str, "unicode": str, "int": int,
                          "long": int, "float": float, "bool": bool, "slice": slice, "xrange": range, "range": range}
                      for m in ("__builtin__", "builtins")}


class _AstUnpickler(pickle.Unpickler):
    def find_class(self, module: str, name: str) -> Any:
        if module == "renpy" or module.startswith("renpy."):
            if name in _SPECIAL:
                return _SPECIAL[name]
            key = (module, name)
            cls = _stubs.get(key)
            if cls is None:
                cls = _stubs[key] = type(name, (AstNode,), {"__module__": module})
            return cls
        if (module, name) == ("_codecs", "encode"):
            return __import__("_codecs").encode
        if (module, name) in _SAFE:
            return _SAFE[(module, name)]
        if module in _HARMLESS_BUILTINS and name in _HARMLESS_BUILTINS[module]:
            return _HARMLESS_BUILTINS[module][name]
        if module in _DANGEROUS_MODULES or name in _DANGEROUS_NAMES:
            raise pickle.UnpicklingError(_t("el .rpyc contiene un objeto no permitido: {0}.{1}", module, name))
        # Cualquier otra clase (de Ren'Py nuevo, de Python 2/3, de un mod): se sustituye por una clase inerte que solo
        # guarda atributos. Nunca se ejecuta nada, y así un .rpyc de una versión futura sigue leyéndose.
        key = (module, name)
        cls = _stubs.get(key)
        if cls is None:
            cls = _stubs[key] = type(name, (AstNode,), {"__module__": module})
        return cls


def fix_text(s: str) -> str:
    """Los `str` de Python 2 llegan decodificados como latin-1: si en realidad eran UTF-8, se recomponen."""
    if s.isascii():
        return s
    try:
        return s.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s


# --------------------------------------------------------------------------------------
# Lectura
# --------------------------------------------------------------------------------------
@dataclass
class RpycFile:
    format: str                                   # "RPC2" | "RPC1"
    info: Dict[str, Any] = field(default_factory=dict)
    stmts: List[Any] = field(default_factory=list)


def _inflate(blob: bytes) -> bytes:
    d = zlib.decompressobj()
    try:
        out = d.decompress(blob, _MAX_RAW + 1)
    except zlib.error as exc:
        raise RpycError(_t("no se pudo descomprimir el .rpyc: {0}", exc))
    if len(out) > _MAX_RAW or d.unconsumed_tail:
        raise RpycError(_t("el .rpyc es demasiado grande"))
    return out


def _slot1(data: bytes) -> bytes:
    pos, size = len(MAGIC), len(data)
    while True:
        if pos + 12 > size:
            raise RpycError(_t("tabla de ranuras del .rpyc incompleta"))
        slot, start, length = struct.unpack("<III", data[pos:pos + 12])
        pos += 12
        if slot == 0:
            raise RpycError(_t("el .rpyc no tiene la ranura 1"))
        if slot == 1:
            if start + length > size:
                raise RpycError(_t("el .rpyc está truncado"))
            return data[start:start + length]


def load_rpyc(data: bytes) -> RpycFile:
    """Bytes de un .rpyc -> RpycFile. Lanza RpycError si no se entiende."""
    if data.startswith(MAGIC):
        fmt, blob = "RPC2", _slot1(data)
    else:
        fmt, blob = "RPC1", data
    raw = _inflate(blob)
    try:
        obj = _AstUnpickler(io.BytesIO(raw), encoding="latin-1").load()
    except (pickle.UnpicklingError, EOFError, AttributeError, ImportError, IndexError, KeyError,
            TypeError, ValueError, MemoryError, RecursionError, OverflowError, UnicodeError) as exc:
        raise RpycError(_t("no se pudo leer el contenido del .rpyc: {0}", exc))
    if isinstance(obj, tuple) and len(obj) == 2 and isinstance(obj[1], list):
        info, stmts = obj
    elif isinstance(obj, list):
        info, stmts = {}, obj
    else:
        raise RpycError(_t("el .rpyc no tiene la estructura esperada"))
    return RpycFile(fmt, info if isinstance(info, dict) else {}, stmts)


def read_rpyc(path: str) -> RpycFile:
    try:
        with open(path, "rb") as f:
            return load_rpyc(f.read())
    except OSError as exc:
        raise RpycError(str(exc))
