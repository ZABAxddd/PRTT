"""
audio.py - Pistas de música de tus .rpy, su duración, su forma de onda y el reproductor de la vista previa.

QUÉ PISTAS SE DETECTAN (collect_tracks)
  · `define audio.t1 = "<loop 22.073>bgm/1.ogg"` (y cualquier `define x = "ruta.ogg"`), en definitions.rpy o en
    cualquier otro .rpy;
  · las que tus scripts reproducen con la ruta suelta: `play music "bgm/x.ogg"` (kind "file");
  · las que `play music nombre` usa pero nadie define (kind "missing", se marcan «sin definir»).

NOMBRE QUE SE MUESTRA
  1. El comentario de la definición:   define audio.t1 = "bgm/1.ogg"   # Doki Doki Literature Club!
     o la línea de comentario justo encima:   # Doki Doki Literature Club!
  2. Si no hay comentario, el nombre de la variable definida (`t1`).

DURACIÓN Y ONDA (sin instalar nada para lo básico)
  · duración: .ogg/.opus (se lee del propio archivo), .wav, .mp3 (aproximada); con `mutagen` instalado, cualquiera;
  · onda: .wav sin instalar nada; .ogg/.mp3 con `soundfile` (+numpy) o con `pygame`. Sin eso la gráfica se
    dibuja igual (línea de tiempo, bucle, fades), solo que sin la onda.
  · escuchar: necesita `pygame-ce` (py -3 -m pip install pygame-ce). Con él hay fade-in/fade-out reales en la vista previa.

No depende de Tkinter.
"""
from __future__ import annotations

import array
import os
import re
import struct
import time
import wave
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple
from i18n import _t
import rpa_reader

from rpy_parser import ScriptDB, unquote

AUDIO_EXT = (".ogg", ".opus", ".mp3", ".wav", ".flac", ".mid", ".midi", ".it", ".xm", ".s3m", ".mod")
SEARCH_DIRS = ("", "audio", "music", "bgm")          # relativos a /game

_DEFINE = re.compile(r"^\s*(?:define|default)\s+(?:-?\d+\s+)?([\w.]+)\s*=\s*(.+?)\s*$")
_STRING = re.compile(r"""^(?:"((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)')$""")
_CODE_LIKE = re.compile(r"^\s*(define|default|image|play|queue|stop|show|hide|scene|label|init|\$)\b")


# --------------------------------------------------------------------------------------
# Pistas
# --------------------------------------------------------------------------------------
@dataclass
class Track:
    ref: str                          # lo que va tras `play music`:  t1   o   "bgm/x.ogg"
    var: str = ""                     # nombre completo definido: audio.t1  ("" si es un archivo suelto)
    title: str = ""                   # comentario `# Título` de la definición (si lo hay)
    spec: str = ""                    # texto tal cual: <loop 22.073>bgm/1.ogg
    path: str = ""                    # ruta del archivo tal como la escribe el script
    kind: str = "defined"             # defined | file | missing
    source: str = ""                  # archivo .rpy donde se define o se usa
    line: int = 0
    loop: Optional[float] = None      # <loop N>: punto al que vuelve al repetirse
    start: Optional[float] = None     # <from N>
    end: Optional[float] = None       # <to N>
    file: Optional[str] = None        # ruta real en disco (None = no encontrada)
    duration: Optional[float] = None
    info_loaded: bool = False

    @property
    def short(self) -> str:
        return self.var[6:] if self.var.startswith("audio.") else (self.var or self.ref.strip("\"'"))

    @property
    def name(self) -> str:
        """Nombre para mostrar: el título del comentario o, si no hay, la variable."""
        return self.title or self.short

    @property
    def label(self) -> str:
        if self.title and self.short and self.title != self.short:
            return f"{self.title}  ({self.short})"
        return self.name

    @property
    def origin(self) -> str:
        if self.kind == "missing":
            return _t("sin definir (tus scripts la usan)")
        return f"{self.source}:{self.line}" if self.source else ""


def split_audio_spec(spec: str) -> Tuple[Dict[str, float], str]:
    """'<from 3 to 40 loop 5>bgm/x.ogg' -> ({'from': 3, 'to': 40, 'loop': 5}, 'bgm/x.ogg')."""
    m = re.match(r"^((?:<[^>]*>)*)(.*)$", spec.strip(), re.S)
    info: Dict[str, float] = {}
    for body in re.findall(r"<([^>]*)>", m.group(1)):
        toks = body.split()
        i = 0
        while i < len(toks):
            if toks[i] in ("from", "to", "loop", "silence") and i + 1 < len(toks):
                try:
                    info[toks[i]] = float(toks[i + 1])
                except ValueError:
                    pass
                i += 2
            else:
                i += 1
    return info, m.group(2).strip()


def _split_comment(physical: str) -> Tuple[str, str]:
    """(código, comentario sin '#') de una línea; un # dentro de comillas no cuenta."""
    s = physical.rstrip("\r\n")
    quote, i = None, 0
    while i < len(s):
        c = s[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in "\"'":
            quote = c
        elif c == "#":
            return s[:i].rstrip(), s[i:].lstrip("#").strip()
        i += 1
    return s.rstrip(), ""


def _clean_title(text: str) -> str:
    text = text.strip().strip("-=*#_~ \t")
    if not text or not re.search(r"\w", text) or _CODE_LIKE.match(text) or text.upper().startswith("PRTT"):
        return ""
    return text


def resolve_file(game_dir: str, path: str) -> Optional[str]:
    path = path.replace("\\", "/").strip()
    if not path:
        return None
    for sub in SEARCH_DIRS:
        cand = os.path.join(game_dir, *([sub] if sub else []), *path.split("/"))
        if os.path.isfile(cand):
            return cand
    return rpa_reader.find_in_archives(game_dir, path, SEARCH_DIRS)     # «rpa://...» si solo está en un .rpa


class TrackBook:
    """Búsqueda de pistas por la referencia con que un script las nombra."""

    def __init__(self, tracks: List[Track]) -> None:
        self.tracks = tracks
        self._by: Dict[str, Track] = {}
        for t in tracks:
            for key in {t.ref, t.var, t.short, t.path, t.path.replace("\\", "/")}:
                if key:
                    self._by.setdefault(key.strip("\"'"), t)

    def find(self, ref: str) -> Optional[Track]:
        ref = (ref or "").strip().strip("\"'")
        _info, path = split_audio_spec(ref) if ref.startswith("<") else ({}, ref)
        return self._by.get(ref) or self._by.get(path) or self._by.get("audio." + ref)


def collect_tracks(db: ScriptDB, game_dir: str) -> List[Track]:
    """Todas las pistas de música de los .rpy (ver el encabezado del módulo)."""
    game_dir = os.path.abspath(game_dir)
    tracks: List[Track] = []
    seen: set = set()
    for rel in db.files:
        try:
            with open(os.path.join(game_dir, *rel.split("/")), "r", encoding="utf-8-sig", errors="replace") as f:
                lines = f.read().splitlines()
        except OSError:
            continue
        for i, phys in enumerate(lines):
            code, comment = _split_comment(phys)
            m = _DEFINE.match(code)
            if not m:
                continue
            var, expr = m.group(1), m.group(2)
            sm = _STRING.match(expr)
            if not sm:
                continue
            spec = unquote(expr)
            info, path = split_audio_spec(spec)
            if not (var.startswith("audio.") or path.lower().endswith(AUDIO_EXT)):
                continue
            if var in seen:
                continue
            seen.add(var)
            title = _clean_title(comment)
            if not title and i > 0:                                  # comentario en la línea de arriba
                above = lines[i - 1].strip()
                if above.startswith("#"):
                    title = _clean_title(above.lstrip("#"))
            short = var[6:] if var.startswith("audio.") else var
            tracks.append(Track(ref=short, var=var, title=title, spec=spec, path=path, source=rel, line=i + 1,
                                loop=info.get("loop"), start=info.get("from"), end=info.get("to"),
                                file=resolve_file(game_dir, path)))
    book = TrackBook(tracks)
    use = re.compile(r"""^(?:play|queue)\s+music\s+("[^"]+"|'[^']+'|[\w.]+)""")
    for ev in db.events:                                             # lo que los scripts usan y no está definido
        if ev.kind != "play" or ev.data.get("channel") != "music":
            continue
        m = use.match(ev.raw)
        if not m:
            continue
        token = m.group(1)
        if token == "expression" or book.find(token) is not None:
            continue
        if token[0] in "\"'":
            info, path = split_audio_spec(token[1:-1])
            t = Track(ref=token, path=path, kind="file", source=ev.file, line=ev.line, loop=info.get("loop"),
                      start=info.get("from"), end=info.get("to"), file=resolve_file(game_dir, path),
                      title=os.path.splitext(os.path.basename(path))[0])
        else:
            t = Track(ref=token, var=token, kind="missing", source=ev.file, line=ev.line)
        tracks.append(t)
        book = TrackBook(tracks)
    return tracks


# --------------------------------------------------------------------------------------
# Duración
# --------------------------------------------------------------------------------------
def _ogg_duration(path: str) -> Optional[float]:
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        head = f.read(1024)
        if head[:4] != b"OggS":
            return None
        rate, pre = None, 0
        i = head.find(b"\x01vorbis")
        if i >= 0 and len(head) >= i + 16:
            rate = struct.unpack("<I", head[i + 12:i + 16])[0]
        else:
            j = head.find(b"OpusHead")
            if j >= 0 and len(head) >= j + 12:
                pre, rate = struct.unpack("<H", head[j + 10:j + 12])[0], 48000
        if not rate:
            return None
        f.seek(max(0, size - 65536))
        tail = f.read()
    k = tail.rfind(b"OggS")
    if k < 0 or len(tail) < k + 14:
        return None
    granule = struct.unpack("<q", tail[k + 6:k + 14])[0]
    return max(granule - pre, 0) / rate if granule > 0 else None


def _wav_duration(path: str) -> Optional[float]:
    with wave.open(path, "rb") as w:
        return w.getnframes() / float(w.getframerate()) if w.getframerate() else None


_MP3_BR = {1: [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0],
           2: [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0]}


def _mp3_duration(path: str) -> Optional[float]:
    """Aproximada (como si fuera de tasa constante). Con `mutagen` es exacta."""
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        head = f.read(10)
        skip = 0
        if head[:3] == b"ID3" and len(head) == 10:
            skip = 10 + ((head[6] & 0x7F) << 21 | (head[7] & 0x7F) << 14 | (head[8] & 0x7F) << 7 | (head[9] & 0x7F))
        f.seek(skip)
        buf = f.read(4096)
    for i in range(len(buf) - 4):
        if buf[i] == 0xFF and (buf[i + 1] & 0xE0) == 0xE0:
            version = (buf[i + 1] >> 3) & 3          # 3 = MPEG1, 2 = MPEG2
            layer = (buf[i + 1] >> 1) & 3            # 1 = Layer III
            idx = buf[i + 2] >> 4
            if version in (2, 3) and layer == 1 and 0 < idx < 15:
                kbps = _MP3_BR[1 if version == 3 else 2][idx]
                return (size - skip) * 8 / (kbps * 1000) if kbps else None
    return None


def probe_duration(path: str) -> Optional[float]:
    try:
        path = rpa_reader.real_path(path)               # si viene de un .rpa se extrae a la caché temporal
    except rpa_reader.RpaError:
        return None
    ext = os.path.splitext(path)[1].lower()
    try:
        from mutagen import File as _MFile            # opcional: exacto para cualquier formato
        mf = _MFile(path)
        if mf is not None and getattr(mf, "info", None) is not None and mf.info.length:
            return float(mf.info.length)
    except Exception:                                  # ImportError o archivo ilegible
        pass
    try:
        if ext in (".ogg", ".opus"):
            return _ogg_duration(path)
        if ext == ".wav":
            return _wav_duration(path)
        if ext == ".mp3":
            return _mp3_duration(path)
    except (OSError, struct.error, wave.Error, EOFError):
        return None
    return None


def load_info(track: Track) -> Track:
    """Rellena la duración (una sola vez por pista)."""
    if not track.info_loaded:
        track.info_loaded = True
        if track.file:
            track.duration = probe_duration(track.file)
    return track


# --------------------------------------------------------------------------------------
# Forma de onda (opcional)
# --------------------------------------------------------------------------------------
def _norm_peaks(vals: List[float]) -> List[float]:
    top = max(vals) if vals else 0
    return [v / top for v in vals] if top > 0 else []


def _peaks_from_samples(samples, channels: int, buckets: int) -> List[float]:
    n = len(samples) // max(channels, 1)
    if n <= 0:
        return []
    step = max(n // buckets, 1)
    out: List[float] = []
    for b in range(0, n, step):
        seg = samples[b * channels:(b + step) * channels:channels]
        out.append(float(max(abs(min(seg)), abs(max(seg)))) if len(seg) else 0.0)
    return _norm_peaks(out[:buckets])


def waveform(path: str, buckets: int = 1200) -> Optional[List[float]]:
    """Picos normalizados (0..1), `buckets` de ellos a lo largo de la pista; None si no hay forma de leerla."""
    try:
        path = rpa_reader.real_path(path)
    except rpa_reader.RpaError:
        return None
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".wav":
            with wave.open(path, "rb") as w:
                ch, width, frames = w.getnchannels(), w.getsampwidth(), w.getnframes()
                if width not in (1, 2) or not frames:
                    return None
                step = max(frames // buckets, 1)
                out: List[float] = []
                for _ in range(0, frames, step):
                    raw = w.readframes(step)
                    if not raw:
                        break
                    if width == 2:
                        a = array.array("h")
                        a.frombytes(raw[:len(raw) - len(raw) % 2])
                    else:
                        a = array.array("h", [b - 128 for b in raw])
                    seg = a[0::ch]
                    out.append(float(max(abs(min(seg)), abs(max(seg)))) if len(seg) else 0.0)
                return _norm_peaks(out[:buckets])
    except (OSError, wave.Error, EOFError, ValueError):
        return None
    try:                                                # .ogg / .mp3 / .flac con soundfile + numpy
        import numpy as np
        import soundfile as sf
        data, _rate = sf.read(path, always_2d=True)
        mono = np.abs(data).max(axis=1)
        step = max(len(mono) // buckets, 1)
        peaks = [float(mono[i:i + step].max()) for i in range(0, len(mono), step)][:buckets]
        return _norm_peaks(peaks)
    except Exception:
        pass
    try:                                                # o con pygame (decodifica ogg/mp3)
        import pygame
        if not pygame.mixer.get_init():
            pygame.mixer.init()
        freq, fmt, channels = pygame.mixer.get_init()
        if fmt != -16:
            return None
        a = array.array("h")
        a.frombytes(pygame.mixer.Sound(path).get_raw())
        return _peaks_from_samples(a, channels, buckets)
    except Exception:
        return None


# --------------------------------------------------------------------------------------
# Números y curvas (las comparten la gráfica y el reproductor)
# --------------------------------------------------------------------------------------
def fmt_num(v: float) -> str:
    """1 -> '1.0', 0.75 -> '0.75', 2.5 -> '2.5' (como se escribe en un `fadein`)."""
    s = f"{float(v):.2f}".rstrip("0")
    return s + "0" if s.endswith(".") else s


def fmt_time(sec: float) -> str:
    sec = max(float(sec), 0.0)
    m, s = divmod(sec, 60)
    return f"{int(m)}:{s:04.1f}"


def fade_in_gain(t: float, fade: float) -> float:
    """Volumen (0..1) a los t segundos de empezar con `fadein fade` (rampa lineal, como en Ren'Py)."""
    return 1.0 if fade <= 0 else max(0.0, min(1.0, t / fade))


def fade_out_gain(t: float, stop_at: float, fade: float) -> float:
    """Volumen (0..1) a los t segundos si el `stop music fadeout fade` ocurre en `stop_at`."""
    if t < stop_at:
        return 1.0
    return 0.0 if fade <= 0 else max(0.0, 1.0 - (t - stop_at) / fade)


def nice_step(span: float, target: int = 8) -> float:
    """Separación «redonda» (1, 2, 5, 10, 15, 30, 60...) para las marcas de la regla de tiempo."""
    raw = max(span / max(target, 1), 0.001)
    for s in (0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600):
        if s >= raw:
            return s
    return 600


# --------------------------------------------------------------------------------------
# Reproductor de la vista previa
# --------------------------------------------------------------------------------------
class Player:
    """Reproduce una pista con fades. Los fades se hacen subiendo/bajando el volumen en `tick()` (que la
    ventana llama unas 25 veces por segundo): así no se bloquea la ventana ni depende de la versión de pygame."""

    def __init__(self) -> None:
        self.ok = False
        self.why = ""
        self._pg = None
        self._env: Optional[Callable[[float], Optional[float]]] = None
        self._file = ""
        self._start = 0.0
        self._t0 = 0.0
        self._loop = False
        self._loop_start = 0.0
        self._duration: Optional[float] = None
        self._active = False
        try:
            import pygame
            pygame.mixer.init()
            self._pg = pygame
            self.ok = True
        except Exception as exc:                        # sin pygame o sin dispositivo de audio
            self.why = (_t("Para escuchar la vista previa instala pygame-ce:  python -m pip install pygame-ce")
                        if isinstance(exc, ImportError) else _t("No se pudo iniciar el audio: {0}", exc))

    # ------------------------------------------------------------------
    @property
    def active(self) -> bool:
        return self._active

    def play(self, file: str, start: float = 0.0, loop: bool = False, loop_start: Optional[float] = None,
             duration: Optional[float] = None, env: Optional[Callable[[float], Optional[float]]] = None) -> str:
        """Empieza a sonar `file` desde `start` s. env(t) -> volumen 0..1 (None = parar). '' si fue bien."""
        if not self.ok:
            return self.why
        self.stop()
        pg = self._pg
        try:
            file = rpa_reader.real_path(file)
        except rpa_reader.RpaError as exc:
            return _t("No se pudo reproducir: {0}", exc)
        try:
            pg.mixer.music.load(file)
            pg.mixer.music.set_volume(env(0.0) if env else 1.0)
            try:
                pg.mixer.music.play(0, max(start, 0.0))
            except pg.error:                            # formatos que no admiten empezar a mitad (p. ej. .wav)
                start = 0.0
                pg.mixer.music.play(0)
        except pg.error as exc:
            return _t("No se pudo reproducir: {0}", exc)
        self._file, self._start, self._t0 = file, max(start, 0.0), time.monotonic()
        self._loop, self._loop_start, self._duration, self._env = loop, loop_start or 0.0, duration, env
        self._active = True
        return ""

    def position(self) -> float:
        """Segundo de la pista que está sonando (con el bucle aplicado)."""
        pos = self._start + (time.monotonic() - self._t0)
        if self._loop and self._duration and pos >= self._duration > self._loop_start:
            pos = self._loop_start + (pos - self._duration) % (self._duration - self._loop_start)
        return pos

    def tick(self) -> None:
        """Llamar con frecuencia: aplica el fade y reinicia el bucle."""
        if not self._active:
            return
        pg = self._pg
        elapsed = time.monotonic() - self._t0
        if self._env is not None:
            gain = self._env(elapsed)
            if gain is None:
                self.stop()
                return
            pg.mixer.music.set_volume(max(0.0, min(1.0, gain)))
        if not pg.mixer.music.get_busy():
            if self._loop:
                try:
                    pg.mixer.music.play(0, self._loop_start if self._duration else 0.0)
                except pg.error:
                    pg.mixer.music.play(0)
                self._start, self._t0 = self._loop_start, time.monotonic()
                self._env = None                                          # el fade solo afecta a la primera vuelta
                pg.mixer.music.set_volume(1.0)
            else:
                self.stop()

    def fade_out(self, duration: float) -> None:
        """Aplica un fade-out sobre la reproducción actual y la detiene al terminar."""
        if not self._active:
            return
        if duration <= 0:
            self.stop()
            return
        pg = self._pg
        self._loop = False
        elapsed = time.monotonic() - self._t0
        fade_start = elapsed
        start_volume = pg.mixer.music.get_volume()

        def envelope(now: float) -> Optional[float]:
            progress = (now - fade_start) / duration
            if progress >= 1.0:
                return None
            return start_volume * max(0.0, 1.0 - progress)

        self._env = envelope

    def stop(self) -> None:
        self._active = False
        self._env = None
        if self._pg is not None:
            try:
                self._pg.mixer.music.stop()
            except Exception:
                pass

    def close(self) -> None:
        self.stop()
