"""
rpa_reader.py - Lectura de archivos .rpa (archivos empaquetados de Ren'Py) como respaldo de /game.

IDEA
  PRTT sigue buscando primero los recursos sueltos en disco. Solo si un recurso NO está suelto, se busca en los
  .rpa que haya en la carpeta game/. Lo que se encuentra allí se representa con una «referencia virtual»
  (una cadena que empieza por «rpa://»), que cabe donde antes iba una ruta de archivo:

      find_in_archives(game_dir, "sayori/1l.png", ("images", ""))   ->  "rpa://images/sayori/1l.png"

  Quien necesite el contenido usa una de estas funciones (todas aceptan también rutas normales de disco):
      asset_source(ref)  -> ruta o io.BytesIO listo para  PIL.Image.open(...)
      read_asset(ref)    -> bytes
      real_path(ref)     -> ruta real en disco (extrae a una carpeta temporal solo si hace falta; pensado para
                            audio, donde mutagen / soundfile / pygame esperan una ruta)

FORMATOS: RPA-2.0, RPA-3.0 (el de DDLC y casi todos los mods) y RPA-3.2.
SEGURIDAD: el índice es un `pickle`. Se lee con un Unpickler restringido (solo se permite `_codecs.encode`, que
  Ren'Py 8 usa para los `bytes`), así que un .rpa descargado no puede ejecutar código al abrirse. La
  descompresión del índice tiene tope de tamaño y las extracciones no pueden salirse de la carpeta de caché.
Los .rpyc que traen los .rpa NO se leen: PRTT necesita los .rpy sueltos para poder editarlos y guardarlos.

No depende de Tkinter ni de Pillow.
"""
from __future__ import annotations

import hashlib
import io
import os
import pickle
import tempfile
import threading
import zlib
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple, Union
from i18n import _t

RPA_PREFIX = "rpa://"
_MAX_INDEX_BYTES = 256 * 1024 * 1024          # tope del índice ya descomprimido
_CACHE_DIR = os.path.join(tempfile.gettempdir(), "prtt_rpa_cache")


class RpaError(Exception):
    pass


# --------------------------------------------------------------------------------------
# Un archivo .rpa
# --------------------------------------------------------------------------------------
Segment = Tuple[int, int, bytes]              # (offset, longitud total, prefijo)


def _empty_bytes(*args):
    if args:
        raise pickle.UnpicklingError(_t("bytes() con argumentos"))
    return b""


class _IndexUnpickler(pickle.Unpickler):
    """Unpickler sin clases: solo deja pasar lo que Ren'Py 8 usa para guardar `bytes` con pickle protocolo 2
    (`_codecs.encode(texto, 'latin1')` para los no vacíos y `bytes()` para el vacío)."""

    def find_class(self, module: str, name: str):
        if (module, name) == ("_codecs", "encode"):
            return __import__("_codecs").encode
        if module in ("builtins", "__builtin__") and name == "bytes":
            return _empty_bytes
        raise pickle.UnpicklingError(_t("el índice contiene un objeto no permitido: {0}.{1}", module, name))


def norm_name(name: str) -> str:
    """Nombre tal como lo guarda Ren'Py: barras normales, sin './' ni '/' al principio."""
    name = (name or "").replace("\\", "/").strip()
    while name.startswith("./"):
        name = name[2:]
    return name.lstrip("/")


def _fix_name(name) -> str:
    """Los .rpa de Python 2 guardan los nombres como bytes UTF-8 (leídos como latin-1): se recomponen."""
    if isinstance(name, bytes):
        return name.decode("utf-8", "replace")
    try:
        return name.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return name


def _as_bytes(prefix) -> bytes:
    if isinstance(prefix, (bytes, bytearray)):
        return bytes(prefix)
    return str(prefix).encode("latin-1", "replace") if prefix else b""


def _decompress_limited(data: bytes, limit: int = _MAX_INDEX_BYTES) -> bytes:
    d = zlib.decompressobj()
    out = d.decompress(data, limit + 1)
    if len(out) > limit or d.unconsumed_tail:
        raise RpaError(_t("el índice del .rpa es demasiado grande"))
    return out


@dataclass
class RpaArchive:
    path: str
    version: str
    index: Dict[str, List[Segment]] = field(default_factory=dict)

    @classmethod
    def open(cls, path: str) -> "RpaArchive":
        size = os.path.getsize(path)
        try:
            with open(path, "rb") as f:
                header = f.readline(256).strip()
                parts = header.split()
                tag = parts[0].decode("ascii", "replace") if parts else ""
                try:
                    if tag == "RPA-3.0":
                        offset, key = int(parts[1], 16), int(parts[2], 16)
                    elif tag == "RPA-3.2":
                        offset, key = int(parts[1], 16), 0
                        for sub in parts[3:]:
                            key ^= int(sub, 16)
                    elif tag == "RPA-2.0":
                        offset, key = int(parts[1], 16), 0
                    else:
                        raise RpaError(_t("formato de .rpa no soportado: {0!r}", header[:20]))
                except (IndexError, ValueError):
                    raise RpaError(_t("cabecera de .rpa ilegible"))
                if not 0 < offset < size:
                    raise RpaError(_t("el índice del .rpa apunta fuera del archivo"))
                f.seek(offset)
                try:
                    raw = _IndexUnpickler(io.BytesIO(_decompress_limited(f.read())), encoding="latin-1").load()
                except (zlib.error, pickle.UnpicklingError, EOFError, AttributeError, ImportError, IndexError,
                        KeyError, TypeError, ValueError) as exc:
                    raise RpaError(_t("no se pudo leer el índice del .rpa: {0}", exc))
        except OSError as exc:
            raise RpaError(str(exc))
        if not isinstance(raw, dict):
            raise RpaError(_t("el índice del .rpa no es válido"))
        index: Dict[str, List[Segment]] = {}
        for name, segs in raw.items():
            try:
                items: List[Segment] = []
                for seg in segs:
                    off, length = int(seg[0]) ^ key, int(seg[1]) ^ key
                    prefix = _as_bytes(seg[2]) if len(seg) > 2 else b""
                    if off < 0 or length < len(prefix):
                        raise ValueError
                    items.append((off, length, prefix))
            except (TypeError, ValueError, IndexError):
                continue                                  # entrada rara: se ignora, el resto sigue valiendo
            if items:
                index[norm_name(_fix_name(name))] = items
        return cls(path, tag, index)

    def read(self, name: str) -> bytes:
        segs = self.index.get(name)
        if segs is None:
            raise RpaError(_t("no está en {0}: {1}", os.path.basename(self.path), name))
        chunks: List[bytes] = []
        try:
            with open(self.path, "rb") as f:             # se abre en cada lectura: nada queda bloqueado en Windows
                for off, length, prefix in segs:
                    f.seek(off)
                    body = f.read(length - len(prefix))
                    if len(body) != length - len(prefix):
                        raise RpaError(_t("{0}: {1} está truncado", os.path.basename(self.path), name))
                    chunks.append(prefix + body)
        except OSError as exc:
            raise RpaError(str(exc))
        return b"".join(chunks)


# --------------------------------------------------------------------------------------
# Todos los .rpa de game/
# --------------------------------------------------------------------------------------
class RpaVault:
    """Los .rpa de una carpeta game/ vistos como un solo sistema de archivos (gana el primero, por orden alfabético)."""

    def __init__(self, game_dir: str) -> None:
        self.game_dir = os.path.abspath(game_dir)
        self.archives: List[RpaArchive] = []
        self.errors: List[str] = []                      # .rpa que no se pudieron abrir (para avisar al usuario)
        self._owner: Dict[str, RpaArchive] = {}
        self._lower: Dict[str, str] = {}
        self._sig: Tuple = ()
        self.scan()

    def _signature(self) -> Tuple:
        """(nombre, tamaño, fecha) de cada .rpa: sirve para saber si hay que volver a leer los índices."""
        sig = []
        try:
            for fn in sorted(os.listdir(self.game_dir)):
                if fn.lower().endswith(".rpa"):
                    st = os.stat(os.path.join(self.game_dir, fn))
                    sig.append((fn, st.st_size, st.st_mtime_ns))
        except OSError:
            pass
        return tuple(sig)

    def refresh_if_changed(self) -> None:
        if self._signature() != self._sig:
            self.scan()

    def scan(self) -> None:
        self.archives, self.errors, self._owner, self._lower = [], [], {}, {}
        self._sig = self._signature()
        for fn, _size, _mtime in self._sig:
            p = os.path.join(self.game_dir, fn)
            if not os.path.isfile(p):
                continue
            try:
                arc = RpaArchive.open(p)
            except RpaError as exc:
                self.errors.append(f"{fn}: {exc}")
                continue
            self.archives.append(arc)
            for name in arc.index:
                self._owner.setdefault(name, arc)
                self._lower.setdefault(name.lower(), name)

    def __bool__(self) -> bool:
        return bool(self._owner)

    def resolve(self, name: str) -> Optional[str]:
        """Nombre exacto guardado en el índice (acepta mayúsculas distintas), o None."""
        name = norm_name(name)
        if name in self._owner:
            return name
        return self._lower.get(name.lower())

    def find(self, path: str, prefixes: Sequence[str] = ("",)) -> Optional[str]:
        for pre in prefixes:
            hit = self.resolve("/".join(x for x in (norm_name(pre).rstrip("/"), norm_name(path)) if x))
            if hit:
                return hit
        return None

    def read(self, name: str) -> bytes:
        hit = self.resolve(name)
        if hit is None:
            raise RpaError(_t("no está en ningún .rpa: {0}", name))
        return self._owner[hit].read(hit)

    def names(self) -> List[str]:
        return sorted(self._owner)

    def extract(self, name: str) -> str:
        """Extrae a la caché temporal (si aún no está) y devuelve la ruta real."""
        hit = self.resolve(name)
        if hit is None:
            raise RpaError(_t("no está en ningún .rpa: {0}", name))
        arc = self._owner[hit]
        st = os.stat(arc.path)
        tag = hashlib.sha1(f"{arc.path}|{st.st_size}|{st.st_mtime_ns}".encode("utf-8", "replace")).hexdigest()[:12]
        root = os.path.abspath(os.path.join(_CACHE_DIR, tag))
        dest = os.path.abspath(os.path.join(root, *hit.split("/")))
        if os.path.commonpath([root, dest]) != root:      # nombre con «..»: no se escribe fuera de la caché
            raise RpaError(_t("nombre no permitido dentro del .rpa: {0}", hit))
        if os.path.isfile(dest):
            return dest
        data = arc.read(hit)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        tmp = f"{dest}.{os.getpid()}.{threading.get_ident()}.tmp"      # único por hilo (la onda se carga en otro hilo)
        try:
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, dest)
        except OSError as exc:
            raise RpaError(str(exc))
        return dest


# --------------------------------------------------------------------------------------
# API de módulo: la que usan sprites.py, audio.py y la ventana
# --------------------------------------------------------------------------------------
_vaults: Dict[str, RpaVault] = {}
_active: Optional[RpaVault] = None


def get_vault(game_dir: str, refresh: bool = False) -> RpaVault:
    """El RpaVault de esa carpeta game/. Se crea una vez y solo relee los índices si algún .rpa apareció, desapareció o
    cambió (refresh=True fuerza la relectura). Queda como vault activo."""
    global _active
    key = os.path.normcase(os.path.abspath(game_dir))
    v = _vaults.get(key)
    if v is None:
        v = _vaults[key] = RpaVault(game_dir)
    elif refresh:
        v.scan()
    else:
        v.refresh_if_changed()
    _active = v
    return v


def is_virtual(ref: Optional[str]) -> bool:
    return bool(ref) and ref.startswith(RPA_PREFIX)


def find_in_archives(game_dir: str, path: str, prefixes: Sequence[str] = ("",)) -> Optional[str]:
    """Referencia virtual del recurso `path` (probando cada prefijo de carpeta), o None si ningún .rpa lo tiene."""
    hit = get_vault(game_dir).find(path, prefixes)
    return RPA_PREFIX + hit if hit else None


def _vault_for(ref: str) -> RpaVault:
    if _active is None:
        raise RpaError(_t("no hay ningún .rpa abierto"))
    return _active


def read_asset(ref: str) -> bytes:
    if is_virtual(ref):
        return _vault_for(ref).read(ref[len(RPA_PREFIX):])
    with open(ref, "rb") as f:
        return f.read()


def asset_source(ref: str) -> Union[str, io.BytesIO]:
    """Para PIL:  Image.open(asset_source(ref)).  Ruta normal -> la misma ruta; virtual -> io.BytesIO(datos)."""
    if is_virtual(ref):
        return io.BytesIO(read_asset(ref))
    return ref


def real_path(ref: str) -> str:
    """Ruta real en disco: la misma si ya lo era; si es virtual, se extrae a la caché temporal."""
    if is_virtual(ref):
        return _vault_for(ref).extract(ref[len(RPA_PREFIX):])
    return ref


def exists(ref: Optional[str]) -> bool:
    if not ref:
        return False
    if is_virtual(ref):
        return _active is not None and _active.resolve(ref[len(RPA_PREFIX):]) is not None
    return os.path.isfile(ref)
