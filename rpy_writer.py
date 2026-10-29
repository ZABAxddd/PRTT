"""
rpy_writer.py - Escribe en los .rpy del mod lo que se edita en PRTT: posiciones de personajes
(show/hide) y texto de diálogos.

REEMPLAZO INTELIGENTE (posiciones)
  Para un diálogo editado se miran las instrucciones que el parser ve ENTRE el diálogo anterior y
  este (la "ventana": scene, show, hide, with...). Por cada personaje del layout:
      · si en esa ventana ya hay un `show <tag> ...` (en el mismo bloque/sangría) se REEMPLAZA esa
        línea, en su sitio; se conservan sus cláusulas `with`, `behind`, `onlayer` y el comentario
        propio del usuario si lo había;
      · si no, se inserta una línea nueva justo después de la última instrucción show/hide/scene de la
        ventana (o encima del diálogo si no hay ninguna).
  Los personajes que ya no están:
      · si ya estaban en escena y tienen un `show ... at [transform on hide]`, se conserva el show
        y su `hide` para reproducir la salida; si eran una adición nueva, se borra el par;
      · en otros casos se reutiliza/inserta el `hide` o se borra el show nuevo;
      · sin él: se inserta `hide <tag>` (salvo que ya haya uno).
  Volver a guardar el mismo diálogo reemplaza las mismas líneas: nunca se duplica nada.
  Un personaje cuyo estado (pose, zorder, transform) ya es EXACTAMENTE el que traía de un diálogo
  anterior no genera ninguna línea: ya existe una instrucción equivalente.

SINTAXIS DE POSE
  · Solo cambia la pose (no zorder ni at):
        quien HABLA  -> compacta, sobreescribiendo su línea de diálogo:   s 1b "texto"
        otro personaje -> `show monika 1b` (sin zorder ni at: conserva su posición)
  · Cambia zorder o at (o entra en escena): `show <tag> <pose> zorder N at T` completo, antes del diálogo.
  · Si la línea de diálogo del que habla ya llevaba pose, se mantiene sincronizada con el layout.
  Las instrucciones de la ventana que no sean de personajes (scene, with, play...) no se tocan.
  Una instrucción de otro diálogo anterior NO se modifica (cambiaría esos diálogos): ahí se inserta
  una línea nueva en la ventana de este diálogo.

COMENTARIOS
  No se escribe ningún comentario `#` salvo que se active `comments=True`; entonces cada línea que
  PRTT escribe termina en `  # PRTT`. Los bloques `# PRTT:begin/end` de versiones anteriores se
  retiran al volver a guardar ese diálogo.

DESHACER DESPUÉS DE GUARDAR
  Sin marcadores en el script, el escritor recuerda (en memoria, mientras PRTT está abierto) el tramo
  original de cada diálogo que tocó; `layout=None` lo restaura byte a byte. Si el script cambió por
  fuera entre medias, no restaura nada y lo dice (queda la copia `.prtt-bak`).

DIÁLOGOS (texto)
  `apply_dialogues` sustituye la línea física de un diálogo por el texto nuevo, conservando sangría,
  saltos de línea y su comentario. La línea nueva se valida con el propio rpy_parser (debe seguir
  siendo UN diálogo) y la antigua debe seguir siendo la que PRTT mostró.

SEGURIDAD (todo)
  Escritura atómica (archivo temporal + os.replace); copia `archivo.rpy.prtt-bak` la primera vez que
  se toca un archivo (nunca se pisa); conserva UTF-8 con/sin BOM y LF/CRLF; no escribe si el archivo
  cambió en disco desde que PRTT lo leyó ni si la línea ya no coincide.

No depende de Tkinter.
"""
from __future__ import annotations

import codecs
import os
import re
import shutil
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple
from i18n import _t

import cells
from atl import TransformLib
from cells import CellLayout
from rpy_parser import ScriptDB

BEGIN = "# PRTT:begin"       # formato antiguo: solo se reconoce para retirarlo
END = "# PRTT:end"
MARK = "# PRTT"              # comentario al final de línea (solo con comments=True)
BACKUP_SUFFIX = ".prtt-bak"
MAX_BLOCK_LINES = 64


@dataclass
class Stmt:
    """Una instrucción del guion entre el diálogo anterior y este."""
    line: int          # nº de línea (1..)
    raw: str           # texto lógico leído por el parser
    kind: str          # scene, show, hide, with, play...
    tag: str = ""      # personaje (solo show/hide; "" si lleva `as`)
    zorder: int = 0
    at: Tuple[str, ...] = ()


@dataclass
class Change:
    """Un diálogo cuyas posiciones hay que escribir (layout) o devolver a su estado original (None)."""
    key: object
    file: str                         # ruta relativa a /game, con "/"
    line: int                         # nº de línea (1..) del diálogo
    raw: str                          # texto lógico de esa línea (para comprobar que no se movió)
    layout: Optional[CellLayout]
    hide: List[str] = field(default_factory=list)       # personajes que estaban en escena y ya no
    window: List[Stmt] = field(default_factory=list)    # instrucciones desde el diálogo anterior
    before: Set[str] = field(default_factory=set)       # personajes en escena ANTES de la ventana
    speaker: str = ""                                    # tag del personaje que habla en este diálogo
    before_state: Dict[str, tuple] = field(default_factory=dict)   # tag -> (poses, zorder, at) antes de la ventana
    hide_effects: List[str] = field(default_factory=list)  # personajes cuyo transform requiere `hide`
    script_hide_effects: List[str] = field(default_factory=list)  # transforms de salida ya escritos en esta ventana


@dataclass
class DialogueEdit:
    """Sustituir la línea de un diálogo: `expect` es el código que PRTT mostró, `new` el nuevo."""
    key: object
    file: str
    line: int
    expect: str
    new: str


@dataclass
class Result:
    key: object
    ok: bool
    msg: str = ""


def make_change(key, frame, layout: Optional[CellLayout], prev_frame=None, db=None) -> Change:
    """Construye el Change de un diálogo (Frame de stage.build_timeline) con su ventana de instrucciones."""
    ev = frame.event
    window: List[Stmt] = []
    for a in frame.actions:
        if a.file != ev.file:
            continue
        tag = ""
        if a.kind == "show":
            tag = a.data.get("as") or a.data.get("tag", "")
        elif a.kind == "hide":
            tag = a.data.get("tag", "")
        window.append(Stmt(a.line, a.raw, a.kind, tag, a.data.get("zorder") or 0,
                           tuple(a.data.get("at", ()))))
    new_scene = any(a.kind == "scene" and not a.data.get("onlayer") for a in frame.actions)
    before = set() if (new_scene or prev_frame is None) else set(prev_frame.stage.actors)
    hide = []
    if layout is not None:
        wanted = {p.tag for p in layout.slots.values()}
        hide = list(dict.fromkeys(
            cells.hide_list(frame.stage, layout) +
            [tag for tag in before if tag not in wanted]
        ))
    hide_effects = []
    script_hide_effects = []
    if layout is not None and db is not None:
        lib = TransformLib(db.transform_defs)
        hide_effects = [p.tag for slot, p in layout.slots.items()
                        if lib.has_event(layout.at_of(slot), "hide")]
    if db is not None:
        lib = TransformLib(db.transform_defs)
        for action in frame.actions:
            if action.kind == "show" and any(
                    lib.has_event(transform, "hide") for transform in action.data.get("at", ())):
                tag = action.data.get("as") or action.data.get("tag", "")
                if tag and tag not in script_hide_effects:
                    script_hide_effects.append(tag)
    state: Dict[str, tuple] = {}
    if prev_frame is not None and not new_scene:
        pl = cells.layout_from_stage(prev_frame.stage)
        state = {p.tag: (tuple(p.attrs), p.zorder, pl.at_of(k)) for k, p in pl.slots.items()}
    who = ev.data.get("who", "")
    speaker = (db.image_tag_of(who) if db is not None and who else "") or ""
    return Change(key, ev.file, ev.line, ev.raw, layout, hide, window, before, speaker, state,
                  hide_effects, script_hide_effects)


# --------------------------------------------------------------------------------------
# Piezas puras (fáciles de probar)
# --------------------------------------------------------------------------------------
def split_comment(physical: str) -> Tuple[str, str]:
    """(código, comentario) de una línea física, sin salto de línea. Un # dentro de comillas no cuenta."""
    s = physical.rstrip("\r\n")
    quote = None
    i = 0
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
            return s[:i].rstrip(), s[i:]
        i += 1
    return s.rstrip(), ""


def indent_of(line: str) -> str:
    return re.match(r"[ \t]*", line).group()


def _norm(s: str) -> str:
    return " ".join(s.split())


def line_matches(physical: str, raw: str) -> bool:
    """¿La línea física sigue siendo la que el parser leyó? (compara el principio)."""
    p, r = _norm(physical), _norm(raw)
    return bool(r) and p.startswith(r[:min(len(r), 12)])


def _clauses(code: str) -> str:
    """Cláusulas de un `show` que se conservan al reemplazarlo: behind, onlayer, with."""
    out = ""
    m = re.search(r"\sbehind\s+([\w.,\s]+?)(?=\s+(?:at|zorder|with|onlayer|as)\b|$)", code)
    if m:
        out += f" behind {m.group(1).strip()}"
    m = re.search(r"\sonlayer\s+(\w+)", code)
    if m:
        out += f" onlayer {m.group(1)}"
    m = re.search(r"\swith\s+(\S.*)$", code)
    if m:
        out += f" with {m.group(1).strip()}"
    return out


def _tail(comment: str, comments: bool) -> str:
    """Comentario final de una línea que PRTT escribe: el del usuario se respeta; el nuestro, solo si se pidió."""
    c = comment.strip()
    if c == MARK:
        c = ""
    if c:
        return "  " + c
    return "  " + MARK if comments else ""


def show_text(p: cells.Placement, at: str) -> str:
    return f"show {p.image} zorder {p.zorder} at {at}"


_SHOW_RE = re.compile(r"^(\s*show\s+)(.*?)(\s+(?:at|as|behind|onlayer|zorder|with)\b.*)?$")


def _retarget(code: str, image: str) -> str:
    """Mismo `show`, otra pose: se cambia solo el nombre de la imagen y se respeta el resto de la línea."""
    m = _SHOW_RE.match(code)
    return f"{m.group(1)}{image}{m.group(3) or ''}" if m else code


def _say_head(code: str):
    """(sangría, [hablante, pose...], resto desde la primera comilla) de una línea de diálogo, o None."""
    q = next((k for k, c in enumerate(code) if c in "\"'"), None)
    if q is None:
        return None
    toks = code[:q].split()
    if not toks:
        return None                           # narrador (o "Nombre" "texto"): no admite pose
    return indent_of(code), toks, code[q:]


def say_attrs(line: str) -> Optional[Tuple[str, ...]]:
    """Poses de una línea de diálogo (`s 1a "x"` -> ("1a",)); None si no lleva hablante."""
    h = _say_head(split_comment(line)[0])
    return tuple(h[1][1:]) if h else None


def set_say_attrs(line: str, attrs: Sequence[str]) -> Optional[str]:
    """La línea de diálogo con esas poses (conserva texto, cláusulas, comentario y salto de línea)."""
    code, comment = split_comment(line)
    h = _say_head(code)
    if h is None:
        return None
    ind, toks, rest = h
    eol = line[len(line.rstrip("\r\n")):]
    return ind + " ".join([toks[0]] + list(attrs)) + " " + rest + (("  " + comment) if comment else "") + eol


def validate_say(code: str) -> Optional[str]:
    """None si `code` es UN diálogo válido para el parser de PRTT; si no, el motivo."""
    code = code.strip()
    if not code:
        return _t("la línea está vacía")
    if "\n" in code or "\r" in code:
        return _t("debe ser una sola línea (usa \\n dentro del texto para saltos)")
    if code.endswith(":"):
        return _t("no puede terminar en «:» (sería un menú o un bloque)")
    quote, i = None, 0
    while i < len(code):                       # el parser perdona comillas sin cerrar; aquí no
        c = code[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in "\"'":
            quote = c
        i += 1
    if quote:
        return _t("hay unas comillas sin cerrar")
    db = ScriptDB()
    db.parse_text("label _prtt_check:\n    " + code + "\n", "_check")
    evs = [e for e in db.events if e.kind != "label"]
    if len(evs) != 1 or evs[0].kind != "say":
        return _t("no se lee como un diálogo: personaje [pose] \"texto\" (revisa las comillas)")
    return None


class _Refuse(Exception):
    pass


# --------------------------------------------------------------------------------------
# Análisis de la ventana de un diálogo
# --------------------------------------------------------------------------------------
@dataclass
class _Window:
    start: int
    show_idx: Dict[str, int]
    show_state: Dict[str, Tuple[int, str]]
    hide_idx: Dict[str, int]
    blocked: Set[str]
    anchor: Optional[int]


def _analyze(lines: List[str], i: int, ch: Change) -> _Window:
    indent = indent_of(lines[i])
    w = _Window(i, {}, {}, {}, set(), None)
    for st in sorted(ch.window, key=lambda s: s.line):
        idx = st.line - 1
        if not 0 <= idx < i:
            continue
        w.start = min(w.start, idx)
        code, _c = split_comment(lines[idx])
        if indent_of(lines[idx]) != indent or st.kind not in ("show", "hide", "scene"):
            continue
        if _norm(code) != _norm(st.raw) or code.endswith(":"):
            if st.tag:
                w.blocked.add(st.tag)           # varias líneas / con bloque ATL: no se reescribe
            continue
        w.anchor = idx if w.anchor is None else max(w.anchor, idx)
        if st.kind == "show" and st.tag:
            w.show_idx[st.tag] = idx
            w.show_state[st.tag] = (st.zorder, ", ".join(st.at))
        elif st.kind == "hide" and st.tag:
            w.hide_idx[st.tag] = idx
    if w.start > 0 and lines[w.start - 1].strip() == BEGIN:
        w.start -= 1                            # formato antiguo: el bloque entero se retira
    return w


def _rewrite_region(lines: List[str], i: int, ch: Change, w: _Window, start: int,
                    comments: bool, eol: str) -> Tuple[List[str], str]:
    """(nuevo contenido de lines[start:i], nueva línea de diálogo) para el layout de `ch`."""
    layout = ch.layout
    indent = indent_of(lines[i])
    repl: Dict[int, str] = {}
    dele: Set[int] = set()
    ins_hide: List[str] = []
    ins_show: List[str] = []
    ins_exit_hide: List[str] = []
    say = lines[i]
    say_now = say_attrs(say)

    def refuse(tag: str) -> None:
        raise _Refuse(_t("{0}: la instrucción de «{1}» ocupa varias líneas o abre un bloque; edítala a mano (PRTT no la toca).", ch.file, tag))

    for k in sorted(layout.slots):
        p = layout.slots[k]
        if p.tag in w.blocked:
            refuse(p.tag)
        at = layout.at_of(k)
        prior = ch.before_state.get(p.tag)                 # cómo estaba antes de este diálogo
        full = prior is None or prior[1] != p.zorder or prior[2] != at
        pose = prior is None or prior[0] != tuple(p.attrs)
        speaker = bool(ch.speaker) and p.tag == ch.speaker
        s_idx, h_idx = w.show_idx.get(p.tag), w.hide_idx.get(p.tag)
        exits = p.tag in ch.hide_effects
        script_state = w.show_state.get(p.tag)
        if s_idx is not None and script_state is not None:
            full = full or script_state != (p.zorder, at)
        if h_idx is not None and (s_idx is None or h_idx > s_idx) and not exits:
            dele.add(h_idx)                                # un hide posterior anularía el show que queremos
        if s_idx is not None:                              # ya hay un show aquí: se REEMPLAZA esa línea
            code, comment = split_comment(lines[s_idx])
            if full:
                text = indent_of(lines[s_idx]) + show_text(p, at) + _clauses(code)
            else:
                text = _retarget(code, p.image)            # solo cambia la pose: respeta at/zorder que ya traía
            repl[s_idx] = text + _tail(comment, comments) + eol
        elif full:
            ins_show.append(indent + show_text(p, at) + _tail("", comments) + eol)
        elif pose:
            fixed = set_say_attrs(say, p.attrs) if (speaker and p.attrs) else None
            if fixed is not None:                          # el que habla: sintaxis compacta  s 1b "texto"
                say = fixed
            else:
                ins_show.append(indent + f"show {p.image}" + _tail("", comments) + eol)
        # (si no hay nada distinto: ya existe una instrucción equivalente; no se escribe nada)
        if speaker and p.attrs and say_now and tuple(say_now) != tuple(p.attrs):
            fixed = set_say_attrs(say, p.attrs)            # su línea de diálogo ya llevaba pose: se sincroniza
            if fixed is not None:
                say = fixed
        if exits and (h_idx is None or s_idx is None or h_idx <= s_idx):
            if h_idx is not None:
                dele.add(h_idx)
            ins_exit_hide.append(indent + f"hide {p.tag}" + _tail("", comments) + eol)
    for tag in ch.hide:
        if tag in w.blocked:
            refuse(tag)
        s_idx = w.show_idx.get(tag)
        h_idx = w.hide_idx.get(tag)
        if s_idx is not None and tag in ch.script_hide_effects:
            if tag in ch.before:
                if h_idx is not None and h_idx > s_idx:
                    continue                                # mantiene el show de salida y su hide
                if h_idx is not None:
                    dele.add(h_idx)                        # un hide anterior no sirve para esta salida
                ins_exit_hide.append(indent + f"hide {tag}" + _tail("", comments) + eol)
            else:
                dele.add(s_idx)                            # se añadía y quitaba en el mismo bloque
                if h_idx is not None and h_idx > s_idx:
                    dele.add(h_idx)
            continue
        if s_idx is not None:
            if tag in ch.before and h_idx is not None and h_idx > s_idx:
                dele.add(s_idx)
            elif tag in ch.before:
                code, comment = split_comment(lines[s_idx])
                wm = re.search(r"\swith\s+(\S.*)$", code)
                repl[s_idx] = (indent_of(lines[s_idx]) + f"hide {tag}" +
                               (f" with {wm.group(1).strip()}" if wm else "") + _tail(comment, comments) + eol)
            else:
                dele.add(s_idx)                            # el show era quien lo metía: sobra
                if h_idx is not None:
                    dele.add(h_idx)
        elif tag in ch.before and h_idx is None:
            ins_hide.append(indent + f"hide {tag}" + _tail("", comments) + eol)
        elif tag not in ch.before and h_idx is not None:
            dele.add(h_idx)
    ins = ins_hide + ins_show + ins_exit_hide

    out: List[str] = []
    for idx in range(start, i):
        line = lines[idx]
        if line.strip() in (BEGIN, END):
            pass                                           # restos del formato antiguo
        elif idx not in dele:
            out.append(repl.get(idx, line))
        if idx == w.anchor:
            out.extend(ins)
    if w.anchor is None:
        out.extend(ins)
    return out, say


# --------------------------------------------------------------------------------------
# Escritura
# --------------------------------------------------------------------------------------
class ScriptWriter:
    def __init__(self, game_dir: str, backup: bool = True, comments: bool = False) -> None:
        self.game_dir = os.path.abspath(game_dir)
        self.backup = backup
        self.comments = comments                # True: las líneas que PRTT escribe acaban en "  # PRTT"
        self._backed: set = set()
        self._journal: Dict[object, Tuple[List[str], List[str]]] = {}   # key -> (original, escrito)

    def path_of(self, rel: str) -> str:
        return os.path.join(self.game_dir, *rel.split("/"))

    # ------------------------------------------------------------------ E/S común
    def _load(self, rel: str, expected_mtime: Optional[int]):
        """(bom, eol, líneas) o str con el motivo del fallo."""
        path = self.path_of(rel)
        try:
            if expected_mtime is not None and os.stat(path).st_mtime_ns != expected_mtime:
                return (_t("el archivo cambió en disco desde que PRTT lo leyó. Pulsa «Recargar .rpy» y vuelve a guardar."))
            with open(path, "rb") as f:
                data = f.read()
        except OSError as exc:
            return str(exc)
        bom = data.startswith(codecs.BOM_UTF8)
        try:
            text = (data[len(codecs.BOM_UTF8):] if bom else data).decode("utf-8")
        except UnicodeDecodeError:
            return _t("no está en UTF-8; no lo modifico.")
        eol = "\r\n" if "\r\n" in text else "\n"
        return bom, eol, text.splitlines(keepends=True)      # mismo criterio de líneas que rpy_parser

    def _store(self, rel: str, bom: bool, lines: List[str]) -> Optional[str]:
        path = self.path_of(rel)
        out = (codecs.BOM_UTF8 if bom else b"") + "".join(lines).encode("utf-8")
        tmp = path + ".prtt-tmp"
        try:
            self._backup(path)
            with open(tmp, "wb") as f:
                f.write(out)
            shutil.copymode(path, tmp)
            os.replace(tmp, path)
        except OSError as exc:
            try:
                os.remove(tmp)
            except OSError:
                pass
            return str(exc)
        return None

    def _backup(self, path: str) -> None:
        if not self.backup or path in self._backed:
            return
        bak = path + BACKUP_SUFFIX
        if not os.path.exists(bak):          # la primera copia (el original) no se pisa nunca
            shutil.copy2(path, bak)
        self._backed.add(path)

    # ------------------------------------------------------------------ posiciones
    def apply(self, changes: Sequence[Change],
              mtimes: Optional[Dict[str, int]] = None) -> List[Result]:
        """Aplica los cambios, agrupados por archivo (un solo reemplazo atómico por archivo).

        mtimes: {archivo: st_mtime_ns cuando PRTT lo leyó}; si no coincide, no se escribe."""
        by_file: Dict[str, List[Change]] = {}
        for ch in changes:
            by_file.setdefault(ch.file, []).append(ch)
        results: List[Result] = []
        for rel, group in by_file.items():
            results += self._apply_file(rel, group, (mtimes or {}).get(rel))
        return results

    def _apply_file(self, rel: str, group: List[Change], expected_mtime: Optional[int]) -> List[Result]:
        loaded = self._load(rel, expected_mtime)
        if isinstance(loaded, str):
            return [Result(c.key, False, f"{rel}: {loaded}") for c in group]
        bom, eol, lines = loaded
        results: List[Result] = []
        changed = False
        journal: Dict[object, Optional[Tuple[List[str], List[str]]]] = {}     # se confirma al escribir
        for ch in sorted(group, key=lambda c: -c.line):      # de abajo hacia arriba: no mueve los demás
            i = ch.line - 1
            if not 0 <= i < len(lines) or not line_matches(lines[i], ch.raw):
                results.append(Result(
                    ch.key, False,
                    _t("{0}:{1}: esa línea ya no coincide con lo que PRTT leyó. Pulsa «Recargar .rpy» y vuelve a guardar.", rel, ch.line)))
                continue
            try:
                did = (self._restore(lines, i, ch, rel, journal) if ch.layout is None
                       else self._write_layout(lines, i, ch, eol, journal))
            except _Refuse as exc:
                results.append(Result(ch.key, False, str(exc)))
                continue
            changed = changed or did
            results.append(Result(ch.key, True))
        if not changed:
            self._commit(journal)
            return results
        err = self._store(rel, bom, lines)
        if err:
            return [Result(r.key, False, f"{rel}: {err}") if r.ok else r for r in results]
        self._commit(journal)
        return results

    def _commit(self, journal) -> None:
        for key, val in journal.items():
            if val is None:
                self._journal.pop(key, None)
            else:
                self._journal[key] = val

    def _write_layout(self, lines: List[str], i: int, ch: Change, eol: str, journal) -> bool:
        w = _analyze(lines, i, ch)
        start = w.start
        prev = self._journal.get(ch.key)
        orig_head: List[str] = []
        prev_orig: Optional[List[str]] = None
        if prev is not None:
            p_orig, p_new = prev
            s_prev = i - (len(p_new) - 1)                   # p_new acaba con la línea del diálogo
            if s_prev >= 0 and lines[s_prev:i] == p_new[:-1]:
                if start < s_prev:
                    orig_head = lines[start:s_prev]
                start = min(start, s_prev)
                prev_orig = p_orig[:-1] + [_resync_say(lines[i], p_orig[-1])]   # por si se editó el texto
        original = (orig_head + prev_orig) if prev_orig is not None else list(lines[start:i + 1])
        region, say = _rewrite_region(lines, i, ch, w, start, self.comments, eol)
        new = region + [say]
        if new == lines[start:i + 1]:
            return False
        lines[start:i + 1] = new
        journal[ch.key] = None if new == original else (original, list(new))
        return True

    def _restore(self, lines: List[str], i: int, ch: Change, rel: str, journal) -> bool:
        """layout=None: devuelve el tramo del diálogo (y su pose) a como estaba antes de que PRTT lo tocara."""
        prev = self._journal.get(ch.key)
        if prev is not None:
            orig, new = prev
            s = i - (len(new) - 1)
            if s < 0 or lines[s:i] != new[:-1]:
                raise _Refuse(_t("{0}:{1}: el script cambió por fuera desde que PRTT lo escribió; no puedo restaurarlo solo (copia original: {2}{3}).", rel, ch.line, os.path.basename(rel), BACKUP_SUFFIX))
            say = orig[-1] if lines[i] == new[-1] else _resync_say(lines[i], orig[-1])
            lines[s:i + 1] = orig[:-1] + [say]
            journal[ch.key] = None
            return True
        span = _legacy_block(lines, i)          # bloque `# PRTT:begin/end` de versiones anteriores
        if span:
            del lines[span[0]:span[1]]
            return True
        return False

    # ------------------------------------------------------------------ diálogos
    def read_statements(self, rel: str, line_numbers: Sequence[int]) -> List[Tuple[str, str]]:
        """[(código, comentario)] de esas líneas del archivo. ValueError si no se puede leer."""
        loaded = self._load(rel, None)
        if isinstance(loaded, str):
            raise ValueError(f"{rel}: {loaded}")
        out = []
        for n in line_numbers:
            if not 1 <= n <= len(loaded[2]):
                raise ValueError(_t("{0}:{1}: la línea ya no existe", rel, n))
            code, comment = split_comment(loaded[2][n - 1])
            out.append((code.strip(), comment))
        return out

    def apply_dialogues(self, edits: Sequence[DialogueEdit],
                        mtimes: Optional[Dict[str, int]] = None) -> List[Result]:
        by_file: Dict[str, List[DialogueEdit]] = {}
        for e in edits:
            by_file.setdefault(e.file, []).append(e)
        results: List[Result] = []
        for rel, group in by_file.items():
            results += self._dialogues_file(rel, group, (mtimes or {}).get(rel))
        return results

    def _dialogues_file(self, rel: str, group: List[DialogueEdit], expected_mtime: Optional[int]) -> List[Result]:
        loaded = self._load(rel, expected_mtime)
        if isinstance(loaded, str):
            return [Result(e.key, False, f"{rel}: {loaded}") for e in group]
        bom, eol, lines = loaded
        results: List[Result] = []
        changed = False
        for e in sorted(group, key=lambda x: -x.line):
            i = e.line - 1
            where = f"{rel}:{e.line}"
            if not 0 <= i < len(lines):
                results.append(Result(e.key, False, _t("{0}: la línea ya no existe.", where)))
                continue
            code, comment = split_comment(lines[i])
            if code.strip() != e.expect.strip():
                results.append(Result(e.key, False, _t("{0}: esa línea ya no es la que PRTT mostró. Pulsa «Recargar .rpy» y vuelve a intentarlo.", where)))
                continue
            problem = validate_say(e.expect) or validate_say(e.new)
            if problem:
                results.append(Result(e.key, False, f"{where}: {problem}"))
                continue
            new_code, new_comment = split_comment(e.new.strip())
            line = indent_of(lines[i]) + new_code.strip() + (
                "  " + (new_comment or comment.strip()) if (new_comment or comment.strip()) else "") + (
                eol if lines[i].endswith(("\n", "\r")) else "")
            if line != lines[i]:
                lines[i] = line
                changed = True
            results.append(Result(e.key, True))
        if not changed:
            return results
        err = self._store(rel, bom, lines)
        if err:
            return [Result(r.key, False, f"{rel}: {err}") if r.ok else r for r in results]
        return results


def _resync_say(current: str, original: str) -> str:
    """`current` (texto vigente) con las poses que tenía `original`."""
    attrs = say_attrs(original)
    if attrs is None or say_attrs(current) == attrs:
        return current
    return set_say_attrs(current, attrs) or current


def _legacy_block(lines: List[str], say_idx: int) -> Optional[Tuple[int, int]]:
    """(inicio, fin) de un bloque `# PRTT:begin ... # PRTT:end` de versiones anteriores encima del diálogo."""
    j = say_idx - 1
    while j >= 0 and not lines[j].strip():
        j -= 1
    if j < 0 or lines[j].strip() != END:
        return None
    k = j - 1
    while k >= 0 and j - k <= MAX_BLOCK_LINES:
        s = lines[k].strip()
        if s == BEGIN:
            return k, say_idx
        if s == END:
            return None
        k -= 1
    return None
