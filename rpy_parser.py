"""
rpy_parser.py - Lector de scripts .rpy (Ren'Py) para el previsualizador de mods de DDLC.

Paso 1 del proyecto: recorrer /game y subcarpetas, leer cada .rpy y convertirlo en una
lista ordenada de eventos (labels, escenas, show/hide, posiciones txx/hxx/fxx..., diálogos,
menús, saltos, música...). No ejecuta nada de Ren'Py: solo interpreta el texto.

Requiere Python 3.8+ (se ejecuta FUERA de Ren'Py, con un Python 3 normal).
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterator, List, Optional, Tuple

# --------------------------------------------------------------------------------------
# Posiciones: txx, hxx, fxx, hfxx, ixx, rxx, lxx, sxx  (+ thide)
#   primer dígito  = cuántos personajes hay en escena (1-9)
#   segundo dígito = posición de este personaje (1..N, de izquierda a derecha)
# --------------------------------------------------------------------------------------
MOTIONS = {
    "t": "appear",       # aparición (transición normal)
    "h": "hop",          # saltito
    "f": "focus",        # foco al personaje
    "hf": "hop_focus",   # salto con foco
    "i": "instant",      # aparición instantánea
    "r": "slide_right",  # entra desde la derecha hasta la posición
    "l": "slide_left",   # entra desde la izquierda hasta la posición
    "s": "sink",         # agachada
    "d": "dip",          # baja y vuelve a subir
}
HIDE_TRANSFORMS = {"thide", "lhide", "rhide"}  # el personaje se va; suele seguir "hide X"

POS_RE = re.compile(r"^(hf|t|h|f|i|r|l|s|d)(\d)(\d)$")

TOKEN_RE = re.compile(
    r'''"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|[\w@.\-]+|[,:=()\[\]]|\S'''
)
WORD_RE = re.compile(r"^[\w@.\-]+$")
SHOW_STOP = {"at", "as", "behind", "onlayer", "zorder", "with"}
TAG_RE = re.compile(r"\{[^{}]*\}")


@dataclass(frozen=True)
class Position:
    name: str     # "t32", "hf21", "thide"...
    motion: str   # "appear", "hop", "focus", ..., "hide"
    count: int    # nº de personajes en escena según la posición (0 para hide)
    slot: int     # celda (1..count) (0 para hide)


def parse_position(name: str) -> Optional[Position]:
    if name in HIDE_TRANSFORMS:
        return Position(name, "hide", 0, 0)
    m = POS_RE.match(name)
    if not m:
        return None
    prefix, count, slot = m.group(1), int(m.group(2)), int(m.group(3))
    if count < 1 or slot < 1 or slot > count:
        return None
    return Position(name, MOTIONS[prefix], count, slot)


# --------------------------------------------------------------------------------------
# Utilidades de texto
# --------------------------------------------------------------------------------------
def unquote(tok: str) -> str:
    """Quita comillas y resuelve escapes de un literal de Ren'Py."""
    if len(tok) >= 2 and tok[0] in "\"'" and tok[-1] == tok[0]:
        tok = tok[1:-1]
    mapping = {"n": "\n", "t": "\t"}
    return re.sub(r"\\(.)", lambda m: mapping.get(m.group(1), m.group(1)), tok, flags=re.S)


def plain_text(text: str) -> str:
    """Texto sin etiquetas de estilo ({i}, {w}, {nw}, {fast}...)."""
    return TAG_RE.sub("", text)


def _scan(line: str, depth: int, triple: Optional[str]) -> Tuple[str, int, Optional[str]]:
    """Quita el comentario # (fuera de strings) y sigue el balance de () [] {} y triple comillas."""
    out: List[str] = []
    i, n = 0, len(line)
    quote: Optional[str] = None
    while i < n:
        c = line[i]
        if triple:
            if line.startswith(triple, i):
                out.append(triple)
                i += 3
                triple = None
            elif c == "\\" and i + 1 < n:
                out.append(line[i:i + 2])
                i += 2
            else:
                out.append(c)
                i += 1
            continue
        if quote:
            if c == "\\" and i + 1 < n:
                out.append(line[i:i + 2])
                i += 2
                continue
            if c == quote:
                quote = None
            out.append(c)
            i += 1
            continue
        if line.startswith('"""', i) or line.startswith("'''", i):
            triple = line[i:i + 3]
            out.append(triple)
            i += 3
            continue
        if c in "\"'":
            quote = c
            out.append(c)
            i += 1
            continue
        if c == "#":
            break
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth = max(0, depth - 1)
        out.append(c)
        i += 1
    return "".join(out), depth, triple


def logical_lines(text: str) -> Iterator[Tuple[int, int, str]]:
    """Genera (nº_línea, indentación, texto) de cada línea lógica, sin comentarios."""
    lines = text.splitlines()
    i, n = 0, len(lines)
    while i < n:
        raw = lines[i].expandtabs(4)
        if not raw.strip():
            i += 1
            continue
        lineno = i + 1
        indent = len(raw) - len(raw.lstrip(" "))
        code, depth, triple = _scan(raw, 0, None)
        parts = [code.strip() if not triple else code.lstrip()]
        i += 1
        while i < n and (depth > 0 or triple or parts[-1].rstrip().endswith("\\")):
            if parts[-1].rstrip().endswith("\\") and not triple:
                parts[-1] = parts[-1].rstrip()[:-1]
            sep = "\n" if triple else " "
            cont, depth, triple = _scan(lines[i].expandtabs(4), depth, triple)
            parts.append(sep)
            parts.append(cont if sep == "\n" else cont.strip())
            i += 1
        text_line = "".join(parts).strip()
        if text_line:
            yield lineno, indent, text_line


# --------------------------------------------------------------------------------------
# Modelo de datos
# --------------------------------------------------------------------------------------
@dataclass
class Event:
    kind: str       # label, scene, show, hide, say, with, play, stop, pause, window, menu,
                    # choice, flow, jump, call, return, python, define, image, transform, unknown
    file: str       # ruta relativa a /game
    line: int
    label: str      # label en el que está
    indent: int
    raw: str
    data: Dict[str, Any] = field(default_factory=dict)

    @property
    def where(self) -> str:
        return f"{self.file}:{self.line}"


@dataclass
class CharacterDef:
    var: str                 # variable usada en el script: s, m, y, n, mc...
    name_expr: str           # "Sayori"  o  s_name (variable)
    name_is_var: bool
    dynamic: bool
    image_tag: str = ""      # image='sayori' -> lo que usa "s 1a" para cambiar la pose
    what_prefix: str = ""    # what_prefix='"' -> comillas alrededor del diálogo
    what_suffix: str = ""


@dataclass
class TransformDef:
    name: str
    params: str                                   # "x=640, z=0.80"
    body: List[Tuple[int, str]] = field(default_factory=list)   # (sangría relativa, línea)


class ScriptDB:
    """Todo lo leído de la carpeta /game."""

    def __init__(self) -> None:
        self.events: List[Event] = []
        self.labels: Dict[str, int] = {}
        self.characters: Dict[str, CharacterDef] = {}
        self.images: Dict[str, str] = {}      # "sayori 1a" -> expresión (ruta o Composite...)
        self.image_atl: Dict[str, List[Tuple[int, str]]] = {}   # image x:  (cuerpo ATL)
        self.transforms: set = set()
        self.transform_defs: Dict[str, TransformDef] = {}
        self.defines: Dict[str, str] = {}     # define/default: nombre -> expresión cruda (gui.*)
        self.variables: Dict[str, str] = {}   # $ s_name = "Sayori"
        self.styles: Dict[str, dict] = {}     # style say_dialogue: -> {"parent": .., "bodies": [..]}
        self.files: List[str] = []
        self.errors: List[str] = []
        self._capture: Optional[list] = None  # donde guardar el cuerpo del bloque en curso

    # ------------------------------------------------------------------ carga
    def load_game_folder(self, game_dir: str, exclude_dirs: Tuple[str, ...] = ()) -> "ScriptDB":
        """Lee todos los .rpy de game_dir y subcarpetas.

        exclude_dirs: rutas de carpetas a ignorar (la herramienta se excluye a sí misma
        para no leer sus propios archivos de ejemplo).
        """
        game_dir = os.path.abspath(game_dir)
        if not os.path.isdir(game_dir):
            raise FileNotFoundError(f"No existe la carpeta: {game_dir}")
        skip = {os.path.normcase(os.path.abspath(p)) for p in exclude_dirs}
        paths: List[str] = []
        for root, dirs, files in os.walk(game_dir):
            dirs[:] = sorted(
                d for d in dirs
                if os.path.normcase(os.path.abspath(os.path.join(root, d))) not in skip
                and not d.startswith(".") and d != "__pycache__"
            )
            for fn in sorted(files):
                if fn.lower().endswith(".rpy"):
                    paths.append(os.path.join(root, fn))
        # definiciones (define/image) pueden estar en cualquier archivo: se leen todos igual
        for p in paths:
            rel = os.path.relpath(p, game_dir).replace(os.sep, "/")
            try:
                with open(p, "r", encoding="utf-8-sig", errors="replace") as f:
                    text = f.read()
                self.parse_text(text, rel)
                self.files.append(rel)
            except OSError as exc:
                self.errors.append(f"{rel}: {exc}")
        self._index_labels()
        return self

    def _index_labels(self) -> None:
        self.labels = {}
        for idx, ev in enumerate(self.events):
            if ev.kind == "label":
                self.labels.setdefault(ev.data["name"], idx)

    # ------------------------------------------------------------------ consultas
    def label_range(self, label: str) -> Tuple[int, int]:
        """Índices [inicio, fin) de los eventos de un label (hasta el siguiente label)."""
        start = self.labels[label]
        end = len(self.events)
        for j in range(start + 1, len(self.events)):
            if self.events[j].kind == "label":
                end = j
                break
        return start, end

    def style_props(self, name: str, _seen: Optional[set] = None) -> Dict[str, str]:
        """Propiedades de un style (con herencia): {"xalign": "0.5", "outlines": "[(2, ...)]"}."""
        _seen = _seen or set()
        if name in _seen:
            return {}
        _seen.add(name)
        st = self.styles.get(name)
        props: Dict[str, str] = {}
        if name != "default":
            parent = (st or {}).get("parent") or "default"
            props.update(self.style_props(parent, _seen))
        if st:
            for body in st["bodies"]:
                for _indent, text in body:
                    bits = text.split(None, 1)
                    if len(bits) == 2:
                        props[bits[0]] = bits[1].strip()
        return props

    def display_name(self, var: str) -> str:
        ch = self.characters.get(var)
        if ch is None:
            return var
        if not ch.name_is_var:
            return ch.name_expr
        return self.variables.get(ch.name_expr, ch.name_expr)

    def image_tag_of(self, var: str) -> str:
        ch = self.characters.get(var)
        return ch.image_tag if ch else ""

    def summary(self) -> dict:
        kinds: Dict[str, int] = {}
        for ev in self.events:
            kinds[ev.kind] = kinds.get(ev.kind, 0) + 1
        return {
            "archivos": len(self.files),
            "labels": len(self.labels),
            "personajes": sorted(self.characters),
            "eventos_por_tipo": dict(sorted(kinds.items())),
            "errores": list(self.errors),
        }

    def to_json(self) -> str:
        return json.dumps(
            {"events": [asdict(e) for e in self.events],
             "characters": {k: asdict(v) for k, v in self.characters.items()},
             "images": self.images, "labels": self.labels},
            ensure_ascii=False, indent=1,
        )

    # ------------------------------------------------------------------ parseo
    def parse_text(self, text: str, rel: str) -> None:
        label = ""
        skip_indent: Optional[int] = None
        capture: Optional[list] = None
        for lineno, indent, line in logical_lines(text):
            if skip_indent is not None:
                if indent > skip_indent:
                    if capture is not None:          # cuerpo de un transform / image ATL
                        capture.append((indent - skip_indent, line))
                    continue
                skip_indent = None
                capture = None
            self._capture = None
            try:
                kind, data, skip = self._statement(line)
            except Exception as exc:  # un error en una línea no debe tumbar todo el archivo
                self.errors.append(f"{rel}:{lineno}: {exc}")
                kind, data, skip = "unknown", {}, False
            if skip:
                capture = self._capture
            if kind == "label":
                label = data["name"]
            if kind is None:
                if skip:
                    skip_indent = indent
                continue
            ev = Event(kind, rel, lineno, label, indent, line, data)
            self.events.append(ev)
            if skip:
                skip_indent = indent

    # Devuelve (kind, data, saltar_bloque_indentado). kind=None => no genera evento.
    def _statement(self, line: str) -> Tuple[Optional[str], dict, bool]:
        ends_colon = line.rstrip().endswith(":")

        if line[0] in "\"'":
            return self._say_or_choice(line)
        if line.startswith("$"):
            return self._python_line(line)

        m = re.match(r"[A-Za-z_]\w*", line)
        word = m.group(0) if m else ""
        rest = line[len(word):].strip() if word else line

        # ---- bloques que no nos interesan (código Python, pantallas, estilos, traducciones)
        if re.match(r"^(?:init\s+(?:-?\d+\s+)?)?python\b.*:$", line):
            return None, {}, True
        if word == "style":
            # style NOMBRE [is PADRE]:   -> se guardan sus propiedades (outlines, font, size...)
            mm = re.match(r"^style\s+([\w.]+)(?:\s+is\s+([\w.]+))?\s*(:)?\s*$", line)
            if mm:
                st = self.styles.setdefault(mm.group(1), {"parent": None, "bodies": []})
                if mm.group(2):
                    st["parent"] = mm.group(2)
                if mm.group(3):
                    body: list = []
                    st["bodies"].append(body)
                    self._capture = body
                    return None, {}, True
                return None, {}, False
            return None, {}, ends_colon
        if word in ("screen", "translate", "class", "def"):
            return None, {}, True
        if re.match(r"^init\b.*:$", line):       # "init:" / "init -1:" -> su contenido sí se lee
            return None, {}, False

        if word == "label":
            mm = re.match(r"^label\s+([\w.]+)", line)
            if not mm:
                return "unknown", {}, False
            return "label", {"name": mm.group(1)}, False

        if word == "scene":
            return self._scene(rest, ends_colon)
        if word == "show":
            return self._show(rest, ends_colon)
        if word == "hide":
            toks = TOKEN_RE.findall(rest)
            tag = toks[0] if toks else ""
            return "hide", {"tag": tag, "with": _after(toks, "with")}, False
        if word == "with":
            return "with", {"expr": rest}, False
        if word in ("play", "queue"):
            toks = TOKEN_RE.findall(rest)
            channel = toks[0] if toks else ""
            target = unquote(toks[1]) if len(toks) > 1 else ""
            return "play", {"channel": channel, "target": target, "args": toks[2:],
                            "queued": word == "queue"}, False
        if word == "stop":
            toks = TOKEN_RE.findall(rest)
            return "stop", {"channel": toks[0] if toks else "", "args": toks[1:]}, False
        if word == "pause":
            return "pause", {"value": rest}, False
        if word == "window":
            return "window", {"mode": rest}, False
        if word == "menu":
            return "menu", {"name": rest.rstrip(":").strip()}, False
        if word in ("if", "elif", "else", "while", "for"):
            return "flow", {"keyword": word, "cond": rest.rstrip(":").strip()}, False
        if word == "jump":
            return "jump", {"target": rest, "dynamic": rest.startswith("expression")}, False
        if word == "call":
            if rest.startswith("screen"):
                return "unknown", {}, False
            mm = re.match(r"([\w.]+)", rest)
            return "call", {"target": mm.group(1) if mm else rest}, False
        if word == "return":
            return "return", {}, False
        if word in ("pass", "nvl"):
            return None, {}, False

        if word in ("define", "default"):
            return self._define(line)
        if word == "image":
            return self._image(line, ends_colon)
        if word == "transform":
            mm = re.match(r"^transform\s+([\w.]+)\s*(?:\((.*)\))?\s*:?\s*$", line, re.S)
            if mm:
                self.transforms.add(mm.group(1))
                td = TransformDef(mm.group(1), (mm.group(2) or "").strip())
                self.transform_defs[td.name] = td
                if ends_colon:
                    self._capture = td.body
            return None, {}, ends_colon

        # ---- ¿diálogo con hablante?  who [attrs] "texto"
        if word and ('"' in line or "'" in line):
            res = self._say_or_choice(line)
            if res[0] == "say":
                return res
        return "unknown", {}, False

    # ------------------------------------------------------------------ sentencias concretas
    def _scene(self, rest: str, ends_colon: bool):
        toks = TOKEN_RE.findall(rest.rstrip(":").strip())
        name: List[str] = []
        for t in toks:
            if t in SHOW_STOP:
                break
            name.append(t)
        return "scene", {"name": " ".join(name), "with": _after(toks, "with"),
                         "onlayer": _after(toks, "onlayer")}, ends_colon

    def _show(self, rest: str, ends_colon: bool):
        ms = list(TOKEN_RE.finditer(rest))
        toks = [m.group(0) for m in ms]
        atl = False
        if toks and toks[-1] == ":":
            atl = True
            toks = toks[:-1]
            ms = ms[:-1]
        if not toks or toks[0] == "expression":
            return "unknown", {}, ends_colon
        i = 0
        name: List[str] = []
        while i < len(toks) and toks[i] not in SHOW_STOP:
            name.append(toks[i])
            i += 1
        d = {"tag": name[0], "attrs": name[1:], "as": None, "at": [], "positions": [],
             "behind": [], "zorder": None, "with": None, "onlayer": None, "atl": atl}
        while i < len(toks):
            t = toks[i]
            if t in ("at", "behind"):
                key = "at" if t == "at" else "behind"
                i += 1
                # lista separada por comas de NIVEL SUPERIOR; cada elemento es un nombre o una llamada
                # (`hop(x=400, z=0.9)`) y se guarda con su texto original.
                depth, first = 0, None
                while i < len(toks) and (depth > 0 or toks[i] not in SHOW_STOP):
                    tk_ = toks[i]
                    if tk_ in "([{":
                        depth += 1
                    elif tk_ in ")]}":
                        depth = max(0, depth - 1)
                    if tk_ == "," and depth == 0:
                        if first is not None:
                            d[key].append(rest[ms[first].start():ms[i - 1].end()])
                        first = None
                    elif first is None:
                        first = i
                    i += 1
                if first is not None:
                    d[key].append(rest[ms[first].start():ms[i - 1].end()])
                continue
            if t in ("as", "onlayer", "with", "zorder") and i + 1 < len(toks):
                val = _after(toks[i:], "with") if t == "with" else toks[i + 1]
                if t == "zorder":
                    try:
                        d["zorder"] = int(val)
                    except ValueError:
                        d["zorder"] = None
                else:
                    d[{"as": "as", "onlayer": "onlayer", "with": "with"}[t]] = val
                if t == "with" and i + 2 < len(toks) and toks[i + 2] == "(":
                    depth = 0
                    while i + 1 < len(toks):
                        i += 1
                        if toks[i] == "(":
                            depth += 1
                        elif toks[i] == ")":
                            depth -= 1
                            if depth == 0:
                                i += 1
                                break
                else:
                    i += 2
                continue
            i += 1
        d["positions"] = [asdict(p) for p in (parse_position(n) for n in d["at"]) if p]
        return "show", d, ends_colon or atl

    def _python_line(self, line: str):
        code = line[1:].strip()
        mm = re.match(r"^([A-Za-z_]\w*)\s*=\s*(\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*')\s*$", code)
        if mm:
            self.variables[mm.group(1)] = unquote(mm.group(2))
        return "python", {"code": code}, False

    def _define(self, line: str):
        mm = re.match(r"^(?:define|default)\s+(?:-?\d+\s+)?([\w.]+)\s*=\s*(.+)$", line, re.S)
        if not mm:
            return "unknown", {}, False
        var, expr = mm.group(1), mm.group(2).strip()
        self.defines[var] = expr
        cm = re.match(r"^(Character|DynamicCharacter)\s*\((.*)\)\s*$", expr, re.S)
        if cm:
            args = cm.group(2)
            toks = TOKEN_RE.findall(args)
            first = toks[0] if toks else ""
            is_lit = bool(first) and first[0] in "\"'"
            dynamic = cm.group(1) == "DynamicCharacter"
            im = re.search(r"""\bimage\s*=\s*(["'])(.*?)\1""", args)
            pre = re.search(r"""\bwhat_prefix\s*=\s*(["'])(.*?)\1""", args)
            suf = re.search(r"""\bwhat_suffix\s*=\s*(["'])(.*?)\1""", args)
            # DynamicCharacter('s_name') -> el string es el NOMBRE DE UNA VARIABLE, no el nombre.
            self.characters[var] = CharacterDef(
                var=var,
                name_expr=unquote(first) if is_lit else first,
                name_is_var=dynamic or not is_lit,
                dynamic=dynamic,
                image_tag=im.group(2) if im else "",
                what_prefix=unquote(pre.group(2)) if pre else "",
                what_suffix=unquote(suf.group(2)) if suf else "",
            )
            return "define", {"var": var, "character": True}, False
        self.defines[var] = expr
        sm = re.match(r"^(\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*')$", expr)
        if sm:
            self.variables[var] = unquote(sm.group(1))
        return "define", {"var": var, "character": False}, False

    def _image(self, line: str, ends_colon: bool):
        mm = re.match(r"^image\s+(.+?)\s*=\s*(.+)$", line, re.S)
        if mm:
            name = " ".join(mm.group(1).split())
            self.images[name] = mm.group(2).strip()
            return "image", {"name": name, "expr": mm.group(2).strip()}, False
        mm = re.match(r"^image\s+(.+?)\s*:$", line)
        if mm:
            name = " ".join(mm.group(1).split())
            self.images[name] = "<ATL>"
            self.image_atl[name] = []
            self._capture = self.image_atl[name]
            return "image", {"name": name, "expr": "<ATL>"}, True
        return "unknown", {}, False

    def _say_or_choice(self, line: str):
        toks = TOKEN_RE.findall(line)
        # --- opción de menú:  "texto" [if cond]:
        if toks and toks[0][0] in "\"'" and line.rstrip().endswith(":"):
            cond = ""
            if len(toks) > 2 and toks[1] == "if":
                cond = line[line.index(" if ") + 4:].rstrip().rstrip(":").strip()
            return "choice", {"text": unquote(toks[0]), "cond": cond}, False
        # --- diálogo
        k = next((i for i, t in enumerate(toks) if t[0] in "\"'"), None)
        if k is None:
            return "unknown", {}, False
        who, attrs = "", []
        if k == 0:
            # "texto"   o   "Nombre" "texto"
            if len(toks) > 1 and toks[1][0] in "\"'":
                who = unquote(toks[0])
                k = 1
        else:
            head = toks[:k]
            if not all(WORD_RE.match(t) for t in head):
                return "unknown", {}, False
            who, attrs = head[0], head[1:]
        text = unquote(toks[k])
        after = toks[k + 1:]
        return "say", {
            "who": who, "who_name": "", "attrs": attrs,
            "text": text, "plain": plain_text(text),
            "with": _after(after, "with"), "extend": who == "extend",
        }, False


def _after(toks: List[str], keyword: str) -> Optional[str]:
    if keyword in toks:
        i = toks.index(keyword)
        if i + 1 < len(toks):
            if i + 2 < len(toks) and toks[i + 2] == "(":
                depth = 0
                expr = []
                for token in toks[i + 1:]:
                    expr.append(token)
                    if token == "(":
                        depth += 1
                    elif token == ")":
                        depth -= 1
                        if depth == 0:
                            return " ".join(expr)
            return toks[i + 1]
    return None


def load(game_dir: str, exclude_dirs: Tuple[str, ...] = ()) -> ScriptDB:
    """Atajo: lee todos los .rpy de game_dir (y subcarpetas)."""
    db = ScriptDB().load_game_folder(game_dir, exclude_dirs)
    for ev in db.events:
        if ev.kind == "say":
            ev.data["who_name"] = db.display_name(ev.data["who"]) if ev.data["who"] else ""
    return db
