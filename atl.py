"""
atl.py - Intérprete del subconjunto de ATL (Animation and Transformation Language) que usa
transforms.rpy de DDLC, para saber DÓNDE y CÓMO se dibuja cada sprite.

Soporta lo que usan t11/t21/h31/f33/hf21/i41/s32/l42/r43/thide...:
    * propiedades: xpos ypos xanchor yanchor xalign yalign xcenter ycenter xoffset yoffset
                   zoom xzoom yzoom alpha (+ pos/anchor/align con tupla)
    * interpolaciones: linear, ease, easein, easeout (con su fórmula de Ren'Py)
    * parallel:, choice: (toma la primera), repeat, pausas, llamadas a otros transforms
      con parámetros (tcommon(200), hop(400, z=0.8)...)
    * eventos on show / on replace / on hide

Todo lo que no entiende se ignora sin romper nada. Las posiciones se convierten a píxeles
de una pantalla de 1280x720 (float = fracción de pantalla, int = píxeles, como en Ren'Py).
"""
from __future__ import annotations

import ast
import math
import operator
import re
from typing import Dict, List, Optional, Sequence, Tuple
from i18n import _t

SCREEN_W, SCREEN_H = 1280, 720

DEFAULT_STATE: Dict[str, float] = {
    "xpos": 0.0, "ypos": 741.6, "xanchor": 0.5, "yanchor": 1.0,
    "xoffset": 0.0, "yoffset": 0.0,
    "zoom": 0.8, "xzoom": 1.0, "yzoom": 1.0, "alpha": 1.0,
}
STATE_KEYS = tuple(DEFAULT_STATE)

PROP_NAMES = {
    "xpos", "ypos", "pos", "xanchor", "yanchor", "anchor", "xalign", "yalign", "align",
    "xcenter", "ycenter", "xoffset", "yoffset", "offset", "zoom", "xzoom", "yzoom", "alpha",
    # reconocidas pero sin efecto en el preview:
    "rotate", "subpixel", "transform_anchor", "crop", "around", "additive", "blur", "size",
    "xsize", "ysize", "xysize", "fit", "matrixcolor", "delay", "events", "rotate_pad",
    "xtile", "ytile", "alignaround", "xanchoraround", "yanchoraround", "xpan", "ypan",
}
KEYWORD_POS = {
    "truecenter": {"xpos": 0.5, "ypos": 0.5, "xanchor": 0.5, "yanchor": 0.5},
    "center": {"xpos": 0.5, "xanchor": 0.5},
    "left": {"xpos": 0.0, "xanchor": 0.0},
    "right": {"xpos": 1.0, "xanchor": 1.0},
    "top": {"ypos": 0.0, "yanchor": 0.0},
    "bottom": {"ypos": 1.0, "yanchor": 1.0},
    "topleft": {"xpos": 0.0, "ypos": 0.0, "xanchor": 0.0, "yanchor": 0.0},
    "topright": {"xpos": 1.0, "ypos": 0.0, "xanchor": 1.0, "yanchor": 0.0},
    "bottomleft": {"xpos": 0.0, "ypos": 1.0, "xanchor": 0.0, "yanchor": 1.0},
    "bottomright": {"xpos": 1.0, "ypos": 1.0, "xanchor": 1.0, "yanchor": 1.0},
}
T_FINAL = 60.0          # "tiempo infinito": cuando ya terminó cualquier animación finita
MAX_DEPTH = 8


class ATLError(Exception):
    pass


# --------------------------------------------------------------------------------------
# Expresiones seguras (solo aritmética con los parámetros del transform)
# --------------------------------------------------------------------------------------
_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
        ast.Pow: operator.pow}


def safe_eval(expr: str, names: Optional[Dict[str, object]] = None):
    names = names or {}
    try:
        node = ast.parse(expr.strip(), mode="eval").body
    except SyntaxError as exc:
        raise ATLError(_t("expresión ilegible: {0!r}", expr)) from exc
    return _ev(node, names)


def _ev(n: ast.AST, names: Dict[str, object]):
    if isinstance(n, ast.Constant):
        if isinstance(n.value, (int, float, str, bool)) or n.value is None:
            return n.value
        raise ATLError(_t("constante no soportada"))
    if isinstance(n, ast.Name):
        if n.id in names and names[n.id] is not None:
            return names[n.id]
        raise ATLError(_t("nombre desconocido: {0}", n.id))
    if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.UAdd)):
        v = _ev(n.operand, names)
        return -v if isinstance(n.op, ast.USub) else +v
    if isinstance(n, ast.BinOp) and type(n.op) in _BIN:
        try:
            return _BIN[type(n.op)](_ev(n.left, names), _ev(n.right, names))
        except (ZeroDivisionError, TypeError, OverflowError) as exc:
            raise ATLError(_t("operación inválida")) from exc
    if isinstance(n, (ast.Tuple, ast.List)):
        return tuple(_ev(e, names) for e in n.elts)
    raise ATLError(_t("expresión no soportada"))


def split_commas(text: str) -> List[str]:
    parts, cur, depth, quote = [], [], 0, None
    for c in text:
        if quote:
            cur.append(c)
            quote = None if c == quote else quote
            continue
        if c in "\"'":
            quote = c
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "," and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
            continue
        cur.append(c)
    if "".join(cur).strip():
        parts.append("".join(cur).strip())
    return parts


# --------------------------------------------------------------------------------------
# Warpers (fórmulas de Ren'Py)
# --------------------------------------------------------------------------------------
def warper(name: str):
    if name == "linear":
        return lambda t: t
    if name.startswith("easein"):
        return lambda t: math.sin(t * math.pi / 2.0)
    if name.startswith("easeout"):
        return lambda t: 1.0 - math.cos(t * math.pi / 2.0)
    return lambda t: 0.5 - math.cos(math.pi * t) / 2.0       # ease


def is_warper(tok: str) -> bool:
    return tok == "linear" or tok.startswith("ease")


# --------------------------------------------------------------------------------------
# Del texto del cuerpo a un árbol, y del árbol a "operaciones"
# --------------------------------------------------------------------------------------
Lines = Sequence[Tuple[int, str]]
Ops = List[tuple]


def parse_block(lines: Lines, i: int, indent: int) -> Tuple[list, int]:
    nodes: list = []
    while i < len(lines) and lines[i][0] >= indent:
        ind, text = lines[i]
        i += 1
        if ind > indent:
            continue                                  # línea más sangrada sin padre: se ignora
        text = text.strip()
        if text.endswith(":"):
            children: list = []
            if i < len(lines) and lines[i][0] > ind:
                children, i = parse_block(lines, i, lines[i][0])
            nodes.append(("block", text[:-1].strip(), children))
        else:
            nodes.append(("stmt", text))
    return nodes, i


def parse_body(lines: Lines) -> list:
    if not lines:
        return []
    return parse_block(list(lines), 0, min(i for i, _ in lines))[0]


def _px(v, size: float) -> float:
    return float(v) * size if isinstance(v, float) else float(v)


def normalize(name: str, value) -> Dict[str, float]:
    """Propiedad ATL -> propiedades del estado (en píxeles / fracciones de ancla)."""
    try:
        if name == "xpos":
            return {"xpos": _px(value, SCREEN_W)}
        if name == "ypos":
            return {"ypos": _px(value, SCREEN_H)}
        if name == "xanchor":
            return {"xanchor": float(value)}
        if name == "yanchor":
            return {"yanchor": float(value)}
        if name == "xalign":
            return {"xpos": float(value) * SCREEN_W, "xanchor": float(value)}
        if name == "yalign":
            return {"ypos": float(value) * SCREEN_H, "yanchor": float(value)}
        if name == "xcenter":
            return {"xpos": _px(value, SCREEN_W), "xanchor": 0.5}
        if name == "ycenter":
            return {"ypos": _px(value, SCREEN_H), "yanchor": 0.5}
        if name in ("xoffset", "yoffset", "zoom", "xzoom", "yzoom", "alpha"):
            return {name: float(value)}
        if name in ("pos", "anchor", "align", "offset") and isinstance(value, tuple) and len(value) == 2:
            a, b = value
            if name == "pos":
                return {**normalize("xpos", a), **normalize("ypos", b)}
            if name == "anchor":
                return {"xanchor": float(a), "yanchor": float(b)}
            if name == "align":
                return {**normalize("xalign", a), **normalize("yalign", b)}
            return {"xoffset": float(a), "yoffset": float(b)}
    except (TypeError, ValueError):
        pass
    return {}


def parse_props(tokens: List[str], names: Dict[str, object]) -> Dict[str, float]:
    props: Dict[str, float] = {}
    i = 0
    while i < len(tokens):
        if tokens[i] not in PROP_NAMES:
            i += 1
            continue
        pname = tokens[i]
        j = i + 1
        while j < len(tokens) and tokens[j] not in PROP_NAMES:
            j += 1
        expr = " ".join(tokens[i + 1:j])
        i = j
        if not expr:
            continue
        try:
            props.update(normalize(pname, safe_eval(expr, names)))
        except ATLError:
            continue
    return props


def touched(ops: Ops) -> set:
    keys: set = set()
    for op in ops:
        if op[0] == "set":
            keys |= set(op[1])
        elif op[0] == "interp":
            keys |= set(op[3])
        elif op[0] == "par":
            for _ops, t in op[1]:
                keys |= t
    return keys


class TransformLib:
    """Todos los transforms del mod (db.transform_defs) listos para evaluar."""

    def __init__(self, defs: Dict[str, object]) -> None:
        self.defs = defs
        self._trees: Dict[str, list] = {}
        self._ops: Dict[Tuple[str, str], Ops] = {}

    def has(self, name: str) -> bool:
        return name in self.defs

    # ---------------------------------------------------------------- compilación
    def _tree(self, name: str) -> list:
        if name not in self._trees:
            self._trees[name] = parse_body(self.defs[name].body)
        return self._trees[name]

    def _bind(self, name: str, args: list, kwargs: dict) -> Dict[str, object]:
        names: Dict[str, object] = {}
        for idx, part in enumerate(split_commas(self.defs[name].params or "")):
            pname, _, default = part.partition("=")
            pname = pname.strip().lstrip("*")
            if not pname:
                continue
            if idx < len(args):
                names[pname] = args[idx]
            elif pname in kwargs:
                names[pname] = kwargs[pname]
            elif default.strip():
                try:
                    names[pname] = safe_eval(default, names)
                except ATLError:
                    names[pname] = None
            else:
                names[pname] = None
        return names

    def ops_for(self, name: str, event: str) -> Ops:
        key = (name, event)
        if key not in self._ops:
            if name not in self.defs:
                self._ops[key] = []
            else:
                self._ops[key] = self._compile(name, [], {}, event, 0)
        return self._ops[key]

    def _compile(self, name: str, args: list, kwargs: dict, event: str, depth: int) -> Ops:
        if depth > MAX_DEPTH or name not in self.defs:
            return []
        names = self._bind(name, args, kwargs)
        return self._nodes(self._tree(name), names, event, depth)

    def _nodes(self, nodes: list, names: Dict[str, object], event: str, depth: int) -> Ops:
        present = {e.strip() for n in nodes if n[0] == "block" and n[1].split()[:1] == ["on"]
                   for e in n[1][2:].split(",")}
        effective = event if event in present or event != "replace" else "show"
        ops: Ops = []
        i = 0
        while i < len(nodes):
            n = nodes[i]
            if n[0] == "stmt":
                ops.extend(self._stmt(n[1], names, event, depth))
                i += 1
                continue
            head = n[1].split()
            kind = head[0] if head else ""
            if kind == "parallel":
                branches = []
                while i < len(nodes) and nodes[i][0] == "block" and nodes[i][1].split()[:1] == ["parallel"]:
                    br = self._nodes(nodes[i][2], names, event, depth)
                    branches.append((br, touched(br)))
                    i += 1
                ops.append(("par", branches))
                continue
            if kind == "choice":
                first = True
                while i < len(nodes) and nodes[i][0] == "block" and nodes[i][1].split()[:1] == ["choice"]:
                    if first:
                        ops.extend(self._nodes(nodes[i][2], names, event, depth))
                        first = False
                    i += 1
                continue
            if kind == "on":
                events = [e.strip() for e in n[1][2:].split(",")]
                if effective in events:
                    ops.extend(self._nodes(n[2], names, event, depth))
            elif kind == "block":
                ops.extend(self._nodes(n[2], names, event, depth))
            i += 1
        return ops

    def _stmt(self, text: str, names: Dict[str, object], event: str, depth: int) -> Ops:
        text = text.strip()
        toks = text.split()
        if not toks:
            return []
        first = toks[0]
        if text == "repeat" or first == "repeat":
            return [("repeat",)]
        if first in ("pass", "function", "time", "with"):
            return []
        if text in KEYWORD_POS:
            return [("set", dict(KEYWORD_POS[text]))]
        if first[0] in "\"'":
            return []                                        # cambio de imagen: no afecta la posición
        if is_warper(first) and first not in names:
            rest = toks[1:]
            k = 0
            while k < len(rest) and rest[k] not in PROP_NAMES:
                k += 1
            try:
                dur = float(safe_eval(" ".join(rest[:k]), names))
            except ATLError:
                return []
            props = parse_props(rest[k:], names)
            return [("interp", warper(first), dur, props)] if props else [("wait", max(dur, 0.0))]
        if first in PROP_NAMES and first not in names:
            props = parse_props(toks, names)
            return [("set", props)] if props else []
        # llamada a otro transform:  tcommon(200)   /   i11
        m = re.match(r"^([A-Za-z_][\w.]*)\s*(\(.*\))?\s*$", text)
        if m and m.group(1) in self.defs and m.group(1) not in names:
            args, kwargs = [], {}
            if m.group(2):
                try:
                    call = ast.parse(m.group(1) + m.group(2), mode="eval").body
                    args = [_ev(a, names) for a in call.args]
                    kwargs = {k.arg: _ev(k.value, names) for k in call.keywords if k.arg}
                except (SyntaxError, ATLError):
                    return []
            return self._compile(m.group(1), args, kwargs, event, depth + 1)
        # pausa:  0.1   /   t   /   t + 2.0
        try:
            d = safe_eval(text, names)
            if isinstance(d, (int, float)):
                return [("wait", max(float(d), 0.0))]
        except ATLError:
            pass
        return []

    # ---------------------------------------------------------------- evaluación
    def sample(self, name: str, event: str, start: Optional[Dict[str, float]], t: float) -> Dict[str, float]:
        """Estado (posición/zoom/alpha) de `name` a los `t` segundos de dispararse `event`."""
        state = dict(DEFAULT_STATE)
        if start:
            state.update({k: v for k, v in start.items() if k in DEFAULT_STATE})
        run_ops(self.ops_for(name, event), state, max(t, 0.0))
        return state

    def final(self, name: str, event: str, start: Optional[Dict[str, float]] = None) -> Dict[str, float]:
        """Estado en reposo (cuando ya terminó la animación)."""
        state = dict(DEFAULT_STATE)
        if start:
            state.update({k: v for k, v in start.items() if k in DEFAULT_STATE})
        ops = self.ops_for(name, event)
        if run_ops(ops, state, T_FINAL) is None:             # sigue corriendo: bucle infinito
            state = dict(DEFAULT_STATE)
            if start:
                state.update({k: v for k, v in start.items() if k in DEFAULT_STATE})
            run_ops(ops, state, 0.0)
        return state

    def duration(self, name: str, event: str) -> float:
        """Segundos que dura la animación (0 si es instantánea; los bucles infinitos cuentan 0)."""
        return ops_duration(self.ops_for(name, event))

    # ---------------------------------------------------------------- expresiones `at` (modo avanzado)
    def parse_expr(self, text: str) -> List[Tuple[str, list, dict]]:
        """'t21, hop(x=400, z=0.9)' -> [('t21', [], {}), ('hop', [], {'x': 400, 'z': 0.9})].

        Los nombres que no existen en tu transforms.rpy se devuelven igual (compilan a nada)."""
        out: List[Tuple[str, list, dict]] = []
        for part in split_commas(text or ""):
            m = re.match(r"^([A-Za-z_][\w.]*)\s*(\(.*\))?$", part.strip(), re.S)
            if not m:
                continue
            args: list = []
            kwargs: dict = {}
            if m.group(2):
                try:
                    call = ast.parse(m.group(1) + m.group(2), mode="eval").body
                    args = [_ev(x, {}) for x in call.args]
                    kwargs = {k.arg: _ev(k.value, {}) for k in call.keywords if k.arg}
                except (SyntaxError, ATLError):
                    pass
            out.append((m.group(1), args, kwargs))
        return out

    def unknown_in(self, text: str) -> List[str]:
        return [n for n, _a, _k in self.parse_expr(text) if n not in self.defs]

    def _has_on(self, name: str, event: str) -> bool:
        """¿El transform tiene un bloque `on <event>`? (la salida solo se anima si lo tiene: thide, lhide...)"""
        return any(n[0] == "block" and n[1].split()[:1] == ["on"]
                   and event in [e.strip() for e in n[1][2:].split(",")] for n in self._tree(name))

    def has_event(self, text: str, event: str) -> bool:
        """Indica si algún transform de una expresión `at` define el evento ATL indicado."""
        return any(name in self.defs and self._has_on(name, event)
                   for name, _args, _kwargs in self.parse_expr(text))

    def _chain(self, text: str, event: str) -> List[Ops]:
        items = [(n, a, k) for n, a, k in self.parse_expr(text) if n in self.defs]
        if event == "hide":                       # sin `on hide` el personaje simplemente desaparece
            items = [(n, a, k) for n, a, k in items if self._has_on(n, "hide")]
        return [self._compile(n, a, k, event, 0) for n, a, k in items]

    def sample_expr(self, text: str, event: str, start: Optional[Dict[str, float]], t: float) -> Dict[str, float]:
        """Estado a los `t` segundos con VARIOS transforms encadenados (`at t21, hop`): cada uno se
        aplica en orden sobre el mismo estado y los posteriores pisan lo que tocan, como en Ren'Py."""
        state = dict(DEFAULT_STATE)
        if start:
            state.update({k: v for k, v in start.items() if k in DEFAULT_STATE})
        for ops in self._chain(text, event):
            run_ops(ops, state, max(t, 0.0))
        return state

    def final_expr(self, text: str, event: str, start: Optional[Dict[str, float]] = None) -> Dict[str, float]:
        state = dict(DEFAULT_STATE)
        if start:
            state.update({k: v for k, v in start.items() if k in DEFAULT_STATE})
        for ops in self._chain(text, event):
            tmp = dict(state)
            if run_ops(ops, tmp, T_FINAL) is None:             # bucle infinito: se queda en el primer fotograma
                tmp = dict(state)
                run_ops(ops, tmp, 0.0)
            state = tmp
        return state

    def duration_expr(self, text: str, event: str) -> float:
        return max((ops_duration(ops) for ops in self._chain(text, event)), default=0.0)

    def loops_expr(self, text: str, event: str) -> bool:
        """¿Alguno de los transforms se repite para siempre (panic...)?"""
        return any(ops and ops[-1][0] == "repeat" or any(o[0] == "par" and any(
            b and b[-1][0] == "repeat" for b, _k in o[1]) for o in ops) for ops in self._chain(text, event))


def run_ops(ops: Ops, state: Dict[str, float], t: float) -> Optional[float]:
    """Avanza `state` durante `t` segundos. Devuelve el tiempo sobrante, o None si sigue corriendo."""
    repeat = bool(ops) and ops[-1][0] == "repeat"
    body = ops[:-1] if repeat else ops
    first_pass = True
    while True:
        spent = 0.0
        for op in body:
            kind = op[0]
            if kind == "set":
                state.update(op[1])
            elif kind == "wait":
                if t < op[1]:
                    return None
                t -= op[1]
                spent += op[1]
            elif kind == "interp":
                _, warp, dur, props = op
                if dur <= 0:
                    state.update(props)
                    continue
                if t < dur:
                    w = warp(t / dur)
                    for key, val in props.items():
                        a = state.get(key, DEFAULT_STATE[key])
                        state[key] = a + (val - a) * w
                    return None
                t -= dur
                spent += dur
                state.update(props)
            elif kind == "par":
                base = dict(state)
                result = dict(state)
                lefts: List[Optional[float]] = []
                for branch_ops, keys in op[1]:
                    bs = dict(base)
                    lefts.append(run_ops(branch_ops, bs, t))
                    for k in keys:
                        result[k] = bs[k]
                state.update(result)
                if any(x is None for x in lefts):
                    return None
                m = min(lefts)                              # type: ignore[type-var]
                spent += t - m
                t = m
        if not repeat or spent <= 0:
            return t
        if first_pass:
            t = t % spent                                   # saltar vueltas completas del bucle
            first_pass = False


def ops_duration(ops: Ops) -> float:
    total = 0.0
    for op in ops:
        if op[0] == "wait":
            total += op[1]
        elif op[0] == "interp":
            total += max(op[2], 0.0)
        elif op[0] == "par":
            total += max((ops_duration(b) if not (b and b[-1][0] == "repeat") else 0.0)
                         for b, _k in op[1]) if op[1] else 0.0
    if ops and ops[-1][0] == "repeat":
        return 0.0
    return total
