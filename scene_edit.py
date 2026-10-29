"""
scene_edit.py - Analiza, modifica y coordina las instrucciones `scene` en los .rpy.

    scene bg club_day with dissolve          (la transición puede ir en la misma línea...)
    scene bg club_day                        (...o en un `with dissolve` suelto justo debajo)
    with dissolve

QUÉ ESCENA MANDA EN UN DIÁLOGO: la última `scene` (sin onlayer) que aparece antes de él dentro de su label y con
la misma sangría o menos (una escena metida en otro `if` no cuenta). La anterior a esa es la que «se restaura» si
se elimina la actual.

OPERACIONES (cada una cambia solo las líneas necesarias; sangría, saltos de línea, tu comentario # y el resto de
cláusulas -onlayer, at, zorder- se conservan):
  · replace_scene : cambia el fondo y/o la transición de la instrucción actual, en su sitio.
  · add_scene     : inserta una `scene` nueva ANTES de los show/hide de ese diálogo (un `scene` borra a los
                    personajes, así que por defecto se vuelven a mostrar justo debajo).
  · delete_scene  : borra la instrucción y su transición; vuelve a mandar la escena anterior.
  · add_definition: escribe `image bg color_xxxxxx = "#xxxxxx"` tras tus otros fondos.

SceneWriter escribe de forma atómica, hace la copia `archivo.rpy.prtt-bak` la primera vez (nunca se pisa), conserva
UTF-8 con/sin BOM y CRLF/LF, no escribe si el archivo cambió en disco, y recuerda el texto anterior de cada cambio
para «Deshacer» mientras PRTT está abierto.

Incluye el catálogo y controlador que usa la ventana de escenarios. No depende de Tkinter.
"""
from __future__ import annotations

import codecs
import os
import re
import shutil
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple
from i18n import _t

from rpy_parser import SHOW_STOP, TOKEN_RE, WORD_RE, _scan
import scenes
import transitions

BACKUP_SUFFIX = ".prtt-bak"
KEEP = object()            # «conservar la transición que ya tiene»


class SceneError(Exception):
    pass


@dataclass
class SceneRef:
    file: str
    line: int                          # 1..
    indent: int
    raw: str
    name: str                          # "bg club_day"
    inline_with: Optional[str] = None
    next_with_line: int = 0            # `with X` suelto justo debajo (0 = no hay)
    next_with: Optional[str] = None

    @property
    def transition(self) -> Optional[str]:
        return self.next_with if self.next_with_line else self.inline_with

    @property
    def text(self) -> str:
        t = f"{self.file}:{self.line}  {self.raw}"
        return t + (f"   + with {self.next_with}" if self.next_with_line else "")


# --------------------------------------------------------------------------------------
# Qué escena manda
# --------------------------------------------------------------------------------------
_SKIP_TO_WITH = ("show", "hide", "play", "stop", "window")      # lo que puede haber entre un `scene` y su `with`


def _ref(db, j: int) -> SceneRef:
    """La instrucción `scene` y su transición: en la misma línea, en un `with` justo debajo o, como en los guiones
    de DDLC, en el `with` que cierra el bloque (`scene bg x` / `show sayori ...` / `with dissolve`): ese `with`
    aplica a toda la escena. Se busca hacia abajo solo entre show/hide/play/stop/window del mismo nivel."""
    ev = db.events[j]
    ref = SceneRef(ev.file, ev.line, ev.indent, ev.raw, ev.data.get("name", ""), ev.data.get("with"))
    nxt = db.events[j + 1] if j + 1 < len(db.events) else None
    if nxt is not None and nxt.kind == "with" and nxt.file == ev.file and nxt.indent == ev.indent \
            and nxt.line > ev.line:
        ref.next_with_line, ref.next_with = nxt.line, nxt.data.get("expr", "").strip()
    elif ref.inline_with is None:
        for k in range(j + 1, len(db.events)):
            e = db.events[k]
            if e.file != ev.file or e.indent != ev.indent:
                break
            if e.kind == "with":
                ref.next_with_line, ref.next_with = e.line, e.data.get("expr", "").strip()
                break
            if e.kind not in _SKIP_TO_WITH:
                break
    return ref


def find_scenes(db, frame) -> Tuple[Optional[SceneRef], Optional[SceneRef]]:
    """(escena que manda en ese diálogo, la anterior). None si no hay en este label."""
    ev = frame.event
    idx = next((i for i, e in enumerate(db.events) if e is ev), None)
    if idx is None:
        return None, None
    start = db.labels.get(ev.label, 0)
    ind, found = ev.indent, []
    for j in range(idx - 1, start - 1, -1):
        e = db.events[j]
        if e.kind == "scene" and not e.data.get("onlayer") and e.file == ev.file and e.indent <= ind:
            found.append(_ref(db, j))
            ind = e.indent
            if len(found) == 2:
                break
    return (found[0] if found else None), (found[1] if len(found) > 1 else None)


def reshow_lines(frame) -> List[str]:
    """`show ...` de los personajes que hay ahora en escena y no se vuelven a mostrar en ese tramo del guion."""
    ev = frame.event
    shown = {(e.data.get("as") or e.data.get("tag")) for e in frame.actions
             if e.kind == "show" and e.file == ev.file and e.indent == ev.indent}
    st = frame.stage
    layers = [(a.zorder, a.seq, k, a, False) for k, a in st.actors.items()] + \
             [(o.zorder, o.seq, k, o, True) for k, o in st.overlays.items()]       # un `scene` borra también estas
    out = []
    for _z, _s, key, a, overlay in sorted(layers, key=lambda t: (t[0], t[1])):
        if key in shown or getattr(a, "hiding", False):
            continue
        names = list(a.at) if overlay else [a.place_name or a.pos_name] + list(a.extra_transforms)
        at = ", ".join(n for n in names if n)
        out.append(f"show {a.image}" + (f" zorder {a.zorder}" if a.zorder else "") + (f" at {at}" if at else ""))
    return out


def first_action_line(frame) -> Optional[int]:
    """Primera línea (1..) de show/hide del tramo anterior al diálogo, con su misma sangría."""
    ev = frame.event
    lines = [e.line for e in frame.actions
             if e.kind in ("show", "hide") and e.file == ev.file and e.indent == ev.indent]
    return min(lines) if lines else None


# --------------------------------------------------------------------------------------
# Piezas de texto
# --------------------------------------------------------------------------------------
def _norm(s: str) -> str:
    return " ".join(s.split())


def _indent_of(line: str) -> str:
    return re.match(r"[ \t]*", line).group()


def _eol_of(line: str) -> str:
    return line[len(line.rstrip("\r\n")):]


def single_line(phys: str) -> Optional[str]:
    code, depth, triple = _scan(phys.rstrip("\r\n"), 0, None)
    return None if (depth or triple or code.rstrip().endswith("\\")) else code


def with_code(phys: str, new_code: str) -> str:
    body = phys.rstrip("\r\n")
    code, _d, _tail = _scan(body, 0, None)
    comment = body[len(code):].strip()
    return _indent_of(phys) + new_code + (("  " + comment) if comment else "") + _eol_of(phys)


def parse_scene_line(code: str):
    """(nombre, cláusulas, resto_with) o None si no se puede reescribir con seguridad (ATL con ':', expression...)."""
    toks = [(m.group(), m.start()) for m in TOKEN_RE.finditer(code)]
    if not toks or toks[0][0] != "scene":
        return None
    i, name = 1, []
    while i < len(toks) and toks[i][0] not in SHOW_STOP:
        if not WORD_RE.match(toks[i][0]):
            return None
        name.append(toks[i][0])
        i += 1
    if not name or name[0] == "expression":
        return None
    clauses: List[list] = []
    tail = ""
    while i < len(toks):
        kw = toks[i][0]
        if kw == "with":
            tail = code[toks[i][1]:].strip()
            break
        if kw in ("at", "behind"):
            i += 1
            vals: List[str] = []
            while i < len(toks) and toks[i][0] not in SHOW_STOP:
                if toks[i][0] != ",":
                    if not WORD_RE.match(toks[i][0]):
                        return None
                    vals.append(toks[i][0])
                i += 1
            if not vals:
                return None
            clauses.append([kw, vals])
        elif kw in ("as", "onlayer", "zorder"):
            if i + 1 >= len(toks) or not WORD_RE.match(toks[i + 1][0]):
                return None
            clauses.append([kw, [toks[i + 1][0]]])
            i += 2
        else:
            return None
    return name, clauses, tail


def build_scene_code(name: str, clauses, tail: str) -> str:
    parts = ["scene", name] + [f"{kw} {', '.join(v)}" for kw, v in clauses]
    return " ".join(parts) + ((" " + tail) if tail else "")


# --------------------------------------------------------------------------------------
# Operaciones sobre las líneas de un archivo (`lines` con sus saltos de línea; se modifica en el sitio)
# --------------------------------------------------------------------------------------
def _check_ref(lines: List[str], ref: SceneRef) -> str:
    i = ref.line - 1
    if not 0 <= i < len(lines):
        raise SceneError(_t("{0}:{1}: esa línea ya no coincide con lo que PRTT leyó. Pulsa «Recargar .rpy» y vuelve a intentarlo.", ref.file, ref.line))
    code = single_line(lines[i])
    if code is None or _norm(code.strip()) != _norm(ref.raw):
        raise SceneError(_t("{0}:{1}: esa línea ya no coincide con lo que PRTT leyó. Pulsa «Recargar .rpy» y vuelve a intentarlo.", ref.file, ref.line))
    parsed = parse_scene_line(code.strip()) if code is not None else None
    if parsed is None:
        raise SceneError(_t("{0}:{1}: esta instrucción `scene` usa algo que no reescribo con seguridad (bloque ATL, expression, varias líneas...). Edítala a mano en el .rpy.", ref.file, ref.line))
    return code


def _check_next_with(lines: List[str], ref: SceneRef) -> int:
    """Índice del `with` suelto de debajo, o -1 si no hay (o ya no coincide)."""
    if not ref.next_with_line:
        return -1
    j = ref.next_with_line - 1
    if 0 <= j < len(lines):
        code = single_line(lines[j])
        expected = f"with {ref.next_with or ''}".strip()
        expanded = lines[j].expandtabs(4)
        indent = len(expanded) - len(expanded.lstrip(" "))
        if (code is not None and _norm(code.strip()) == _norm(expected)
                and indent == ref.indent):
            return j
    raise SceneError(_t("{0}:{1}: el `with` de la escena ya no está donde PRTT lo leyó. Pulsa «Recargar .rpy».", ref.file, ref.next_with_line))


def replace_scene(lines: List[str], ref: SceneRef, name: str, effect=KEEP) -> str:
    """Cambia el fondo (y la transición, si effect no es KEEP; None = sin transición)."""
    code = _check_ref(lines, ref)
    j = _check_next_with(lines, ref)
    names, clauses, tail = parse_scene_line(code.strip())
    if effect is not KEEP:
        tail = "" if (j >= 0 or effect is None) else f"with {effect}"       # con `with` suelto se maneja abajo
    i = ref.line - 1
    lines[i] = with_code(lines[i], build_scene_code(name, clauses, tail))
    if effect is not KEEP and j >= 0:
        if effect is None:
            del lines[j]
        else:
            lines[j] = with_code(lines[j], f"with {effect}")
    return f"scene {name}" + ("" if effect is KEEP else f" with {effect}" if effect else _t(" (sin transición)"))


def delete_scene(lines: List[str], ref: SceneRef) -> str:
    _check_ref(lines, ref)
    j = _check_next_with(lines, ref)
    if j >= 0:
        del lines[j]
    del lines[ref.line - 1]
    return _t("escena eliminada")


def add_scene(lines: List[str], where_line: int, name: str, effect: Optional[str], reshow: List[str],
              eol: str = "\n") -> str:
    """Inserta `scene name [with effect]` ANTES de la línea `where_line` (1..), con su sangría. Si hay `reshow`
    (personajes que el scene borraría) van debajo y la transición queda en un `with` suelto tras ellos."""
    i = where_line - 1
    if not 0 <= i < len(lines):
        raise SceneError(_t("la línea del diálogo ya no existe. Pulsa «Recargar .rpy»."))
    ind = _indent_of(lines[i])
    if reshow:
        body = [f"scene {name}"] + reshow + ([f"with {effect}"] if effect else [])
    else:
        body = [f"scene {name}" + (f" with {effect}" if effect else "")]
    lines[i:i] = [ind + b + eol for b in body]
    return f"scene {name}" + (f" with {effect}" if effect else "") + (_t(" + {0} personaje(s) vuelven a mostrarse", len(reshow)) if reshow else "")


def add_definition(lines: List[str], after_line: int, text: str, eol: str = "\n") -> str:
    """Escribe `text` (una línea) tras la línea `after_line` (1..); 0 = al final del archivo."""
    if after_line <= 0 or after_line > len(lines):
        if lines and not lines[-1].endswith(("\n", "\r")):
            lines[-1] += eol
        lines.append(text + eol)
    else:
        i = after_line - 1
        if not lines[i].endswith(("\n", "\r")):
            lines[i] += eol
        lines.insert(i + 1, _indent_of(lines[i]) + text + eol)
    return text


_BG_DEF = re.compile(r"^image\s+bg\s+[\w.\-]+\s*=")


def add_bg_definition(lines: List[str], text: str, eol: str = "\n") -> str:
    """Escribe `text` tras la última `image bg X = ...` del archivo (o tras la última `image`, o al final)."""
    last = last_img = 0
    for n, ln in enumerate(lines, 1):
        if _BG_DEF.match(ln) and single_line(ln) is not None:
            last = n
        if ln.startswith("image ") and single_line(ln) is not None and not ln.rstrip().endswith(":"):
            last_img = n
    return add_definition(lines, last or last_img, text, eol)


def definition_site(db) -> Tuple[str, int]:
    """(archivo, línea) donde escribir la definición de un fondo nuevo: definitions.rpy si ya define fondos `bg`;
    si no, el archivo que más define; si no hay ninguno, el primero con `image`. ('', 0) si no hay dónde.
    (La línea es orientativa: add_bg_definition vuelve a buscar el sitio en el texto.)"""
    per: dict = {}
    for ev in db.events:
        if ev.kind == "image" and ev.data["name"].startswith("bg ") and ev.data["expr"] != "<ATL>":
            per.setdefault(ev.file, []).append(ev.line)
    if per:
        pick = next((f for f in per if f.replace("\\", "/").rsplit("/", 1)[-1] == "definitions.rpy"), None)
        pick = pick or max(per, key=lambda f: len(per[f]))
        return pick, per[pick][-1]
    first = next((ev for ev in db.events if ev.kind == "image"), None)
    return (first.file, 0) if first else ("", 0)


# --------------------------------------------------------------------------------------
# Escritura en disco
# --------------------------------------------------------------------------------------
Step = Tuple[str, Callable[[List[str], str], str], Optional[int]]      # (archivo, función, mtime esperado)


class SceneWriter:
    def __init__(self, game_dir: str, backup: bool = True) -> None:
        self.game_dir = os.path.abspath(game_dir)
        self.backup = backup
        self._backed: set = set()
        self._undo: List[List[Tuple[str, bytes, int]]] = []       # grupos de (archivo, bytes anteriores, mtime tras escribir)

    def path_of(self, rel: str) -> str:
        return os.path.join(self.game_dir, *rel.split("/"))

    def can_undo(self) -> bool:
        return bool(self._undo)

    # ------------------------------------------------------------------
    def _write_one(self, rel: str, fn, expected: Optional[int]) -> Tuple[Tuple[str, bytes, int], str]:
        path = self.path_of(rel)
        try:
            if expected is not None and os.stat(path).st_mtime_ns != expected:
                raise SceneError(_t("{0}: el archivo cambió en disco desde que PRTT lo leyó. Pulsa «Recargar .rpy» y vuelve a intentarlo.", rel))
            with open(path, "rb") as f:
                data = f.read()
        except OSError as exc:
            raise SceneError(f"{rel}: {exc}")
        bom = data.startswith(codecs.BOM_UTF8)
        try:
            text = (data[len(codecs.BOM_UTF8):] if bom else data).decode("utf-8")
        except UnicodeDecodeError:
            raise SceneError(_t("{0}: no está en UTF-8; no lo modifico.", rel))
        eol = "\r\n" if "\r\n" in text else "\n"
        lines = text.splitlines(keepends=True)
        msg = fn(lines, eol)
        out = (codecs.BOM_UTF8 if bom else b"") + "".join(lines).encode("utf-8")
        self._atomic(path, out)
        return (rel, data, os.stat(path).st_mtime_ns), msg

    def _atomic(self, path: str, data: bytes) -> None:
        tmp = path + ".prtt-tmp"
        try:
            if self.backup and path not in self._backed:
                bak = path + BACKUP_SUFFIX
                if not os.path.exists(bak):
                    shutil.copy2(path, bak)
                self._backed.add(path)
            with open(tmp, "wb") as f:
                f.write(data)
            shutil.copymode(path, tmp)
            os.replace(tmp, path)
        except OSError as exc:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise SceneError(f"{os.path.basename(path)}: {exc}")

    def apply(self, steps: List[Step]) -> Tuple[bool, str]:
        """Aplica los pasos en orden como una sola operación deshacible. Si uno falla, se revierten los anteriores."""
        done: List[Tuple[str, bytes, int]] = []
        msgs: List[str] = []
        try:
            for rel, fn, expected in steps:
                rec, msg = self._write_one(rel, fn, expected)
                done.append(rec)
                msgs.append(msg)
        except SceneError as exc:
            for rel, data, _mt in reversed(done):
                try:
                    self._atomic(self.path_of(rel), data)
                except SceneError:
                    pass
            return False, str(exc)
        self._undo.append(done)
        return True, "  ·  ".join(m for m in msgs if m)

    def undo(self) -> Tuple[bool, str]:
        if not self._undo:
            return False, _t("No hay nada que deshacer.")
        group = self._undo[-1]
        for rel, _data, mt in group:                       # solo si nadie tocó el archivo después
            try:
                if os.stat(self.path_of(rel)).st_mtime_ns != mt:
                    return False, _t("{0}: cambió después de escribirlo; no lo deshago para no pisar tus cambios.", rel)
            except OSError as exc:
                return False, str(exc)
        try:
            for rel, data, _mt in reversed(group):
                self._atomic(self.path_of(rel), data)
        except SceneError as exc:
            return False, str(exc)
        self._undo.pop()
        for rel, _data, _mt in group:                      # el archivo vuelve al estado de antes: los grupos anteriores
            try:                                           # siguen siendo válidos con la nueva fecha de modificación
                now = os.stat(self.path_of(rel)).st_mtime_ns
            except OSError:
                continue
            self._undo = [[(r, d, now if r == rel else m) for r, d, m in g] for g in self._undo]
        return True, _t("Cambio de escenario deshecho.")


@dataclass
class Context:
    where: str
    current: Optional[SceneRef]
    previous: Optional[SceneRef]
    backgrounds: List[scenes.Background]
    transitions: List[transitions.Spec]
    notes: List[str] = field(default_factory=list)

    @property
    def current_bg(self) -> str:
        return self.current.name if self.current else ""

    @property
    def previous_bg(self) -> str:
        return self.previous.name if self.previous else ""


def build_context(db, frame, find_image: Optional[Callable[[str], object]] = None,
                  include_all: bool = False) -> Context:
    current, previous = find_scenes(db, frame)
    notes = []
    if current is None:
        notes.append(_t("Este diálogo no tiene ninguna instrucción `scene` antes dentro de su label: solo se puede AGREGAR una."))
    return Context(f"{frame.event.label} · {frame.event.where}", current, previous,
                   scenes.collect_backgrounds(db, include_all),
                   transitions.collect_transitions(db, find_image), notes)


class SceneController:
    def __init__(self, game_dir: str) -> None:
        self.writer = SceneWriter(game_dir)

    def can_undo(self) -> bool:
        return self.writer.can_undo()

    def undo(self) -> Tuple[bool, str]:
        return self.writer.undo()

    def apply(self, db, frame, op: str, bg: Optional[scenes.Background], effect=KEEP, reshow: bool = True,
              mtimes: Optional[Dict[str, int]] = None) -> Tuple[bool, str, bool]:
        mtimes = dict(mtimes or {})
        current, previous = find_scenes(db, frame)
        steps: List[Step] = []
        touched: set = set()

        def step(rel: str, fn) -> None:
            steps.append((rel, fn, None if rel in touched else mtimes.get(rel)))
            touched.add(rel)

        try:
            if effect is not KEEP and effect is not None and not isinstance(effect, str):
                raise SceneError(_t("transición no válida"))
            if op in ("replace", "delete") and current is None:
                raise SceneError(_t("No hay ninguna instrucción `scene` que mande en este diálogo."))
            if op in ("replace", "add") and bg is None:
                raise SceneError(_t("Elige un fondo."))
            if op == "replace":
                step(current.file, lambda lines, eol: replace_scene(lines, current, bg.name, effect))
            elif op == "add":
                transition = (current.transition if current else None) if effect is KEEP else effect
                where = first_action_line(frame) or frame.event.line
                lines = reshow_lines(frame) if reshow else []
                step(frame.event.file, lambda source, eol: add_scene(
                    source, where, bg.name, transition, lines, eol))
            elif op == "delete":
                step(current.file, lambda lines, eol: delete_scene(lines, current))
            else:
                raise SceneError(_t("operación desconocida: {0}", op))
            full = False
            if op != "delete" and bg.new:
                site, _line = definition_site(db)
                if not site:
                    raise SceneError(_t("No encuentro ningún `image` en tus .rpy donde escribir la definición del color."))
                text = scenes.definition_line(bg)
                step(site, lambda lines, eol: add_bg_definition(lines, text, eol))
                full = True
        except SceneError as exc:
            return False, str(exc), False
        ok, msg = self.writer.apply(steps)
        if not ok:
            return False, msg, False
        if op == "add" and any(e.kind == "scene" and not e.data.get("onlayer") for e in frame.actions):
            msg += (_t(" Ojo: este diálogo ya tenía un `scene` justo antes; el anterior solo se verá un instante. Si lo que querías es cambiar su fondo, usa «Reemplazar»."))
        if op == "delete":
            back = _t(" Vuelve a mandar «{0}» ({1}:{2}).", previous.name, previous.file, previous.line) if previous else \
                _t(" No queda ninguna escena anterior en este label.")
            msg += back
        return True, msg, full
