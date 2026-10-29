"""
audio_edit.py - Lee y escribe en los .rpy las instrucciones de música:

    play music t1 fadein 1.0
    stop music fadeout 1.0

QUÉ INSTRUCCIÓN MANDA EN UN DIÁLOGO: hacia atrás desde el diálogo, dentro de su label, mismo archivo y con la misma
sangría o menos (una instrucción metida en otro `if` no cuenta):
  · la `play music` más cercana es la música «actual»;
  · si antes de llegar a ella hay un `stop music`, ese es el «stop actual» (y la música ya no suena).

OPERACIONES (cada una toca solo una línea; sangría, comentario # y el resto de cláusulas -volume, fadeout, if_changed-
se conservan):
  · replace_play : reescribe el `play music` actual con la pista, el fadein y el loop elegidos.
  · add_play     : inserta un `play music` NUEVO encima del diálogo, sin tocar el anterior.
  · replace_stop : reescribe el `stop music` actual con el fadeout elegido.
  · add_stop     : inserta un `stop music fadeout N` encima del diálogo.
  Al agregar o reemplazar un stop se averigua qué pista estaba sonando justo antes (Link): ese es el `play music`
  que se está deteniendo. Con la opción «anotar» se escribe `# detiene: Título` al final de la línea (apagada por
  defecto: PRTT no escribe comentarios si no se lo pides).

LOOP: la música de Ren'Py ya se repite por sí sola, así que con «loop» marcado no se escribe nada (si la línea ya
traía un `loop` explícito se conserva); al desmarcarlo se escribe `noloop`.

Usa el mismo escritor seguro que los escenarios (scene_edit.SceneWriter): escritura atómica, copia `.prtt-bak` la
primera vez, UTF-8 con/sin BOM, CRLF/LF, no escribe si el archivo cambió en disco y recuerda lo anterior para
«Deshacer». No depende de Tkinter.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import audio
from rpy_parser import TOKEN_RE, _scan
from scene_edit import SceneError, SceneWriter, Step, single_line, with_code

CHANNEL = "music"
_KW = ("fadein", "fadeout", "volume", "loop", "noloop", "if_changed")
_FLAGS = ("loop", "noloop", "if_changed")
_VALUED = ("fadein", "fadeout", "volume")
NOTE_PREFIX = "# detiene:"


def _norm(s: str) -> str:
    return " ".join(s.split())


def _indent_of(line: str) -> str:
    return re.match(r"[ \t]*", line).group()


def _eol_of(line: str) -> str:
    return line[len(line.rstrip("\r\n")):]


# --------------------------------------------------------------------------------------
# Leer / construir el texto de una instrucción
# --------------------------------------------------------------------------------------
@dataclass
class Parsed:
    cmd: str                                   # play | queue | stop
    channel: str
    target: str = ""                           # texto tras el canal:  t1  /  "bgm/x.ogg"  /  [a, b]
    clauses: List[Tuple[str, Optional[str]]] = field(default_factory=list)   # [("fadein", "1.0"), ("loop", None)]

    def value(self, kw: str) -> Optional[str]:
        return next((v for k, v in self.clauses if k == kw), None)

    def has(self, kw: str) -> bool:
        return any(k == kw for k, _v in self.clauses)

    def number(self, kw: str) -> Optional[float]:
        try:
            return float(self.value(kw))
        except (TypeError, ValueError):
            return None


def parse_audio_code(code: str) -> Optional[Parsed]:
    """Descompone `play music X [fadein N] [loop]...` o `stop music [fadeout N]`; None si lleva algo raro."""
    code = code.strip()
    if not code or code.endswith(":"):
        return None
    ms = list(TOKEN_RE.finditer(code))
    toks = [m.group() for m in ms]
    if len(toks) < 2 or toks[0] not in ("play", "queue", "stop"):
        return None
    cmd, channel = toks[0], toks[1]
    if cmd == "stop":
        k = 2
        target = ""
    else:
        k = next((i for i in range(3, len(toks)) if toks[i] in _KW), len(toks))
        if len(toks) < 3:
            return None
        target = code[ms[2].start():ms[k - 1].end()].strip()
    clauses: List[Tuple[str, Optional[str]]] = []
    i = k
    while i < len(toks):
        kw = toks[i]
        if kw in _FLAGS:
            clauses.append((kw, None))
            i += 1
        elif kw in _VALUED and i + 1 < len(toks):
            clauses.append((kw, toks[i + 1]))
            i += 2
        else:
            return None                         # `with`, `if`, `channel`...: no se reescribe con seguridad
    return Parsed(cmd, channel, target, clauses)


def build_play(target: str, fadein: float, loop: bool, old: Optional[Parsed] = None) -> str:
    """`play music t1 fadein 1.0` (conserva fadeout, volume e if_changed de la línea anterior)."""
    kept = [(k, v) for k, v in (old.clauses if old else []) if k not in ("fadein", "loop", "noloop")]
    explicit_loop = bool(old and old.has("loop"))
    parts = ["play music", target]
    for k, v in kept:
        if k == "fadeout":
            parts += [k, v or ""]
    if fadein and fadein > 0:
        parts += ["fadein", audio.fmt_num(fadein)]
    if not loop:
        parts.append("noloop")
    elif explicit_loop:
        parts.append("loop")
    for k, v in kept:
        if k != "fadeout":
            parts += [k] + ([v] if v is not None else [])
    return " ".join(p for p in parts if p)


def build_stop(fadeout: float) -> str:
    return "stop music" + (f" fadeout {audio.fmt_num(fadeout)}" if fadeout and fadeout > 0 else "")


# --------------------------------------------------------------------------------------
# Qué instrucciones mandan en un diálogo
# --------------------------------------------------------------------------------------
@dataclass
class AudioRef:
    idx: int                                   # posición en db.events
    file: str
    line: int                                  # 1..
    indent: int
    raw: str
    kind: str                                  # play | stop
    parsed: Optional[Parsed] = None
    track: Optional[audio.Track] = None

    @property
    def target(self) -> str:
        return self.parsed.target if self.parsed else ""

    @property
    def fadein(self) -> Optional[float]:
        return self.parsed.number("fadein") if self.parsed else None

    @property
    def fadeout(self) -> Optional[float]:
        return self.parsed.number("fadeout") if self.parsed else None

    @property
    def loop(self) -> bool:
        return not (self.parsed and self.parsed.has("noloop"))

    @property
    def text(self) -> str:
        return f"{self.file}:{self.line}  {self.raw}"


@dataclass
class Link:
    """Qué pista está sonando justo antes de un punto del guion (la que un `stop music` detendría)."""
    state: str                                 # playing | stopped | none
    track: Optional[audio.Track] = None
    play: Optional[AudioRef] = None
    note: str = ""

    @property
    def title(self) -> str:
        if self.track is not None:
            return self.track.name
        return self.play.target if self.play else ""


def _make_ref(db, j: int, book: audio.TrackBook) -> AudioRef:
    ev = db.events[j]
    parsed = parse_audio_code(ev.raw)
    ref = AudioRef(j, ev.file, ev.line, ev.indent, ev.raw, ev.kind, parsed)
    if parsed is not None and ev.kind == "play" and parsed.target and not parsed.target.startswith("["):
        ref.track = book.find(parsed.target)
    return ref


def _is_music(ev, kinds=("play", "stop")) -> bool:
    return ev.kind in kinds and ev.data.get("channel") == CHANNEL


def active_before(db, idx: int, indent: int, book: audio.TrackBook) -> Link:
    """La pista que suena justo antes del evento `idx`: la `play music` más cercana hacia atrás, salvo que antes de
    ella haya un `stop music` (entonces ya estaba detenida)."""
    ev = db.events[idx]
    start = db.labels.get(ev.label, 0)
    for j in range(idx - 1, start - 1, -1):
        e = db.events[j]
        if e.file != ev.file or e.indent > indent or not _is_music(e):
            continue
        if e.kind == "stop":
            return Link("stopped", note=f"ya la detuvo un `stop music` en {e.file}:{e.line}; "
                                        "este stop no detendría nada")
        if e.data.get("queued"):
            continue
        ref = _make_ref(db, j, book)
        if ref.track is not None:
            return Link("playing", ref.track, ref, f"{ref.track.name} ({ref.track.short}), empezó en {e.file}:{e.line}")
        return Link("playing", None, ref, f"lo que empezó en {e.file}:{e.line} ({ref.target or e.raw})")
    return Link("none", note="no hay ningún `play music` antes de este punto en este label "
                             "(la música puede venir de otro label)")


def find_refs(db, frame, book: audio.TrackBook) -> Tuple[Optional[AudioRef], Optional[AudioRef], Optional[int]]:
    """(play actual, stop actual, índice del diálogo). Ver el encabezado del módulo."""
    ev = frame.event
    idx = next((i for i, e in enumerate(db.events) if e is ev), None)
    if idx is None:
        return None, None, None
    start = db.labels.get(ev.label, 0)
    play = stop = None
    for j in range(idx - 1, start - 1, -1):
        e = db.events[j]
        if e.file != ev.file or e.indent > ev.indent or not _is_music(e):
            continue
        if e.kind == "play":
            if e.data.get("queued"):
                continue
            play = _make_ref(db, j, book)
            break
        if stop is None:
            stop = _make_ref(db, j, book)
    return play, stop, idx


def window_music(db, idx: int) -> List[AudioRef]:
    """Instrucciones de música escritas entre el diálogo anterior y este (en orden de aparición)."""
    ev = db.events[idx]
    start = db.labels.get(ev.label, 0)
    out: List[AudioRef] = []
    book = audio.TrackBook([])
    for j in range(idx - 1, start - 1, -1):
        e = db.events[j]
        if e.kind == "say" and e.file == ev.file and e.indent <= ev.indent:
            break
        if e.file == ev.file and e.indent <= ev.indent and _is_music(e) and not e.data.get("queued"):
            out.append(_make_ref(db, j, book))
    return out[::-1]


@dataclass
class Options:
    fadein: float = 1.0
    fadeout: float = 1.0
    loop: bool = True
    note: bool = False                         # `# detiene: Título` al final del stop


@dataclass
class Context:
    where: str
    play: Optional[AudioRef]
    stop: Optional[AudioRef]
    playing: Optional[audio.Track]             # lo que suena en este diálogo según el escenario
    tracks: List[audio.Track]
    link_add: Link                             # pista que detendría un stop NUEVO encima del diálogo
    link_replace: Optional[Link]               # pista que detiene el stop actual (None si no hay stop)
    notes: List[str] = field(default_factory=list)
    window: List[AudioRef] = field(default_factory=list)   # música ya escrita justo encima de este diálogo

    def link_for(self, replace: bool) -> Link:
        return self.link_replace if (replace and self.link_replace is not None) else self.link_add


def build_context(db, frame, game_dir: str) -> Context:
    tracks = audio.collect_tracks(db, game_dir)
    book = audio.TrackBook(tracks)
    play, stop, idx = find_refs(db, frame, book)
    notes: List[str] = []
    if idx is None:
        return Context(frame.event.where, None, None, None, tracks, Link("none", note="diálogo no encontrado"), None,
                       ["No encuentro el diálogo en los scripts: pulsa «Recargar .rpy»."])
    ev = frame.event
    link_add = active_before(db, idx, ev.indent, book)
    link_rep = active_before(db, stop.idx, stop.indent, book) if stop is not None else None
    music = frame.stage.music
    playing = book.find(music) if music and not music.startswith("[") else None
    if play is None and stop is None:
        notes.append("Este diálogo no tiene ningún `play music` ni `stop music` antes dentro de su label: "
                     "solo se puede AGREGAR uno.")
    if not any(t.kind != "missing" for t in tracks):
        notes.append("No encontré ninguna pista definida (`define audio.x = \"bgm/x.ogg\"`) en tus .rpy.")
    return Context(f"{ev.label} · {ev.where}", play, stop, playing, tracks, link_add, link_rep, notes,
                   window_music(db, idx))


# --------------------------------------------------------------------------------------
# Operaciones sobre las líneas de un archivo (se modifica `lines` en el sitio)
# --------------------------------------------------------------------------------------
def _check_ref(lines: List[str], ref: AudioRef) -> Parsed:
    i = ref.line - 1
    code = single_line(lines[i]) if 0 <= i < len(lines) else None
    if code is None or _norm(code.strip()) != _norm(ref.raw):
        raise SceneError(f"{ref.file}:{ref.line}: esa línea ya no coincide con lo que PRTT leyó. "
                         "Pulsa «Recargar .rpy» y vuelve a intentarlo.")
    parsed = parse_audio_code(code.strip())
    if parsed is None:
        raise SceneError(f"{ref.file}:{ref.line}: esta instrucción usa algo que no reescribo con seguridad "
                         "(`with`, `if`, varias líneas...). Edítala a mano en el .rpy.")
    return parsed


def _with_note(phys: str, new_code: str, note: str) -> str:
    """Como scene_edit.with_code, pero puede escribir `# detiene: ...` si la línea no tenía un comentario tuyo."""
    body = phys.rstrip("\r\n")
    code, _depth, _triple = _scan(body, 0, None)
    old = body[len(code):].strip()
    comment = old
    if note and (not old or old.startswith(NOTE_PREFIX)):
        comment = f"{NOTE_PREFIX} {note}"
    return _indent_of(phys) + new_code + (("  " + comment) if comment else "") + _eol_of(phys)


def replace_play(lines: List[str], ref: AudioRef, track_ref: str, opts: Options) -> str:
    parsed = _check_ref(lines, ref)
    if parsed.cmd != "play" or parsed.channel != CHANNEL:
        raise SceneError(f"{ref.file}:{ref.line}: no es un `play music`. Pulsa «Recargar .rpy».")
    code = build_play(track_ref, opts.fadein, opts.loop, parsed)
    i = ref.line - 1
    lines[i] = with_code(lines[i], code)
    return code


def replace_stop(lines: List[str], ref: AudioRef, opts: Options, note: str = "") -> str:
    parsed = _check_ref(lines, ref)
    if parsed.cmd != "stop" or parsed.channel != CHANNEL:
        raise SceneError(f"{ref.file}:{ref.line}: no es un `stop music`. Pulsa «Recargar .rpy».")
    code = build_stop(opts.fadeout)
    i = ref.line - 1
    lines[i] = _with_note(lines[i], code, note if opts.note else "")
    return code


def add_line(lines: List[str], where_line: int, expect_raw: str, code: str, eol: str = "\n",
             note: str = "") -> str:
    """Inserta `code` ANTES de la línea `where_line` (1..) con su sangría, sin tocar nada más."""
    i = where_line - 1
    if not 0 <= i < len(lines) or not _norm(lines[i]).startswith(_norm(expect_raw)[:12]):
        raise SceneError("la línea del diálogo ya no está donde PRTT la leyó. Pulsa «Recargar .rpy».")
    j = i - 1
    while j >= 0 and not lines[j].strip():
        j -= 1
    prev = single_line(lines[j]) if j >= 0 else None
    if prev is not None and _norm(prev.strip()) == _norm(code):          # doble clic: no se repite la misma línea
        return code + "  (ya estaba escrita justo encima)"
    text = code + (f"  {NOTE_PREFIX} {note}" if note else "")
    lines.insert(i, _indent_of(lines[i]) + text + eol)
    return code


# --------------------------------------------------------------------------------------
# Controlador (lo que usa la ventana)
# --------------------------------------------------------------------------------------
class AudioController:
    def __init__(self, game_dir: str) -> None:
        self.game_dir = game_dir
        self.writer = SceneWriter(game_dir)

    def can_undo(self) -> bool:
        return self.writer.can_undo()

    def undo(self) -> Tuple[bool, str]:
        ok, msg = self.writer.undo()
        return ok, ("Cambio de música deshecho." if ok else msg)

    def apply(self, db, frame, op: str, track: Optional[audio.Track], opts: Options,
              mtimes: Optional[Dict[str, int]] = None) -> Tuple[bool, str]:
        """op: replace_play | add_play | replace_stop | add_stop  ->  (ok, mensaje)."""
        mtimes = dict(mtimes or {})
        ctx = build_context(db, frame, self.game_dir)
        ev = frame.event
        steps: List[Step] = []
        info = ""
        try:
            if ctx.notes and ctx.notes[0].startswith("No encuentro el diálogo"):
                raise SceneError(ctx.notes[0])
            if op in ("replace_play", "add_play"):
                if track is None:
                    raise SceneError("Elige una pista.")
                if track.kind == "missing":
                    raise SceneError(f"«{track.short}» no está definida en tus .rpy "
                                     "(`define audio.%s = \"...\"`): defínela antes." % track.short)
            if op == "replace_play":
                if ctx.play is None:
                    raise SceneError("No hay ningún `play music` que mande en este diálogo: usa «Agregar».")
                ref = ctx.play
                steps.append((ref.file, lambda lines, eol: replace_play(lines, ref, track.ref, opts),
                              mtimes.get(ref.file)))
            elif op == "add_play":
                steps.append((ev.file, lambda lines, eol: add_line(lines, ev.line, ev.raw,
                                                                   build_play(track.ref, opts.fadein, opts.loop), eol),
                              mtimes.get(ev.file)))
            elif op == "replace_stop":
                if ctx.stop is None:
                    raise SceneError("No hay ningún `stop music` que mande en este diálogo: usa «Agregar».")
                ref, link = ctx.stop, ctx.link_for(True)
                note = link.title
                steps.append((ref.file, lambda lines, eol: replace_stop(lines, ref, opts, note),
                              mtimes.get(ref.file)))
                info = _link_text(link)
            elif op == "add_stop":
                link = ctx.link_for(False)
                note = link.title
                steps.append((ev.file, lambda lines, eol: add_line(
                    lines, ev.line, ev.raw, build_stop(opts.fadeout), eol, note if opts.note else ""),
                    mtimes.get(ev.file)))
                info = _link_text(link)
            else:
                raise SceneError(f"operación desconocida: {op}")
        except SceneError as exc:
            return False, str(exc)
        ok, msg = self.writer.apply(steps)
        if not ok:
            return False, msg
        if op in ("add_play", "add_stop") and ctx.window:
            last = ctx.window[-1]
            what = "`play music`" if last.kind == "play" else "`stop music`"
            effect = "lo sustituye" if op == "add_play" else "lo corta"
            info = (info + "  ·  " if info else "") + (
                f"Ojo: justo encima de este diálogo ya hay un {what} ({last.file}:{last.line}); lo nuevo "
                f"{effect} al instante. Si querías cambiar esa instrucción, usa «Reemplazar».")
        return True, (msg + ("  ·  " + info if info else "")).strip()


def _link_text(link: Link) -> str:
    if link.state == "playing":
        return f"Detiene {link.note}."
    return f"Ojo: {link.note}." if link.note else ""
