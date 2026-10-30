"""
rpyc_decompile.py - AST de un .rpyc (ver rpyc_reader.py) -> texto .rpy que entiende rpy_parser.ScriptDB.parse_text.

NO ES UN DESCOMPILADOR COMPLETO. Solo reconstruye lo que PRTT necesita leer: labels, diálogos, scene/show/hide/with,
play/stop/queue/pause/window, menús, if/while/jump/call/return, `$` y `python:`, define/default/image/transform (con su
ATL) y style. Las pantallas (`screen`), las traducciones (`translate`) y lo demás se omiten. Lo que sí se escribe procura
parecerse al original, pero no es idéntico (se pierden comentarios y formato).

LÍNEAS ORIGINALES
  Cada nodo trae el nº de línea del .rpy del que salió. Cada instrucción se coloca en esa misma línea del texto
  generado (rellenando con líneas en blanco), de modo que `file:line` de los eventos apunte al script real. Si dos
  instrucciones chocan (porque una ocupa varias líneas), la segunda va justo debajo.

Los nodos y campos de Ren'Py cambian entre versiones: todo se lee con getattr y valores por defecto. Lo no soportado
se anota en `warnings` (una vez por tipo) y se salta; nunca se detiene el resto del archivo.

No depende de Tkinter.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from i18n import _t

from rpyc_reader import fix_text

INDENT = "    "

# Nodos que se omiten sin avisar (no aportan nada a PRTT)
_IGNORED = {
    "Screen", "TranslateString", "TranslatePython", "TranslateBlock", "TranslateEarlyBlock", "EndTranslate",
    "Testcase", "RPY", "Camera", "ShowLayer", "Transform_", "Style_",
}
# Nodos que el parser trata como «un solo statement» envuelto en Init
_INIT_IMPLICIT = {"Define", "Default", "Image", "Transform", "Style", "Screen", "Testcase", "Translate", "TranslateString"}


def encode_text(text: str) -> str:
    """Literal de Ren'Py entre comillas dobles (mismas reglas que dialogue.encode_text)."""
    s = text.replace("\\", "\\\\").replace('"', '\\"').replace("\r\n", "\n").replace("\r", "\n")
    return '"' + s.replace("\n", "\\n").replace("\t", "\\t") + '"'


def _s(x: Any) -> str:
    return fix_text(str(x)) if x is not None else ""


def fmt_args(info: Any) -> str:
    """ArgumentInfo -> '(a, b=1)'. '' si no hay."""
    if info is None:
        return ""
    args = getattr(info, "arguments", None) or []
    star = set(getattr(info, "starred_indexes", None) or ())
    dstar = set(getattr(info, "doublestarred_indexes", None) or ())
    out = []
    for i, (name, expr) in enumerate(args):
        pre = "*" if i in star else "**" if i in dstar else ""
        out.append(f"{pre}{expr}" if name is None else f"{name}={expr}")
    return "(" + ", ".join(_s(o) for o in out) + ")"


def fmt_params(info: Any) -> str:
    """ParameterInfo -> '(a, b=1, *args, **kwargs)'. '' si es None."""
    if info is None:
        return ""
    out = []
    for name, default in getattr(info, "parameters", None) or []:
        out.append(_s(name) if default is None else f"{_s(name)}={_s(default)}")
    if getattr(info, "extrapos", None):
        out.append("*" + _s(info.extrapos))
    if getattr(info, "extrakw", None):
        out.append("**" + _s(info.extrakw))
    return "(" + ", ".join(out) + ")"


def code_source(code: Any) -> str:
    """Texto de un PyCode / PyExpr / str."""
    if code is None:
        return ""
    src = getattr(code, "source", code)
    return _s(src)


@dataclass
class Decompiled:
    text: str
    warnings: List[str] = field(default_factory=list)


class _Out:
    """Líneas de salida; cada instrucción se coloca en su línea original si es posible."""

    def __init__(self) -> None:
        self.lines: List[str] = []

    def put(self, indent: int, text: str, line: Optional[int] = None) -> None:
        if isinstance(line, int) and line > len(self.lines) + 1:
            self.lines.extend([""] * (line - 1 - len(self.lines)))
        first, *rest = text.split("\n")
        self.lines.append(INDENT * indent + first if first.strip() else "")
        self.lines.extend(rest)                       # continuación (dentro de paréntesis o python:): tal cual

    def text(self) -> str:
        return "\n".join(self.lines) + "\n"


class Decompiler:
    def __init__(self) -> None:
        self.o = _Out()
        self.warnings: List[str] = []
        self._warned: set = set()
        self._paired: Optional[str] = None            # `show x with d`: With None(paired=d) + Show + With d(paired=d)
        self._paired_done = False

    # ------------------------------------------------------------------ utilidades
    def warn(self, kind: str, line: Any = None) -> None:
        if kind not in self._warned:
            self._warned.add(kind)
            self.warnings.append(_t("instrucción no soportada «{0}» (línea {1}): se omite", kind, line or "?"))

    def block(self, stmts: Sequence[Any], indent: int, *, allow_empty: bool = False) -> None:
        stmts = [n for n in (stmts or []) if n is not None]
        n_before = len(self.o.lines)
        i = 0
        while i < len(stmts):
            node = stmts[i]
            nxt = stmts[i + 1] if i + 1 < len(stmts) else None
            used = self.stmt(node, nxt, indent)
            i += max(used, 1)
        if len(self.o.lines) == n_before and not allow_empty:
            self.o.put(indent, "pass")

    def stmt(self, n: Any, nxt: Any, indent: int) -> int:
        kind = type(n).__name__
        fn: Optional[Callable] = getattr(self, "n_" + kind, None)
        if fn is None:
            if kind not in _IGNORED:
                self.warn(kind, getattr(n, "linenumber", None))
            return 1
        return fn(n, nxt, indent) or 1

    # ------------------------------------------------------------------ imágenes
    def _imspec(self, spec: Any) -> Tuple[str, List[str]]:
        spec = list(spec or []) + [None] * 7
        name, expression, tag, at_list, layer, zorder, behind = spec[:7]
        head = f"expression {_s(expression)}" if expression else " ".join(_s(x) for x in (name or ()))
        extra: List[str] = []
        if tag:
            extra.append(f"as {_s(tag)}")
        if at_list:
            extra.append("at " + ", ".join(_s(a) for a in at_list))
        if behind:
            extra.append("behind " + ", ".join(_s(b) for b in behind))
        if layer:
            extra.append(f"onlayer {_s(layer)}")
        if zorder is not None:
            extra.append(f"zorder {_s(zorder)}")
        return head, extra

    def _with_tail(self) -> str:
        if self._paired is not None:
            w, self._paired, self._paired_done = self._paired, None, True
            return f" with {_s(w)}"
        return ""

    def _image_stmt(self, word: str, n: Any, head: str, extra: List[str], indent: int) -> None:
        text = " ".join([word] + ([head] if head else []) + extra) + self._with_tail()
        atl = getattr(n, "atl", None)
        self.o.put(indent, text + (":" if atl is not None else ""), getattr(n, "linenumber", None))
        if atl is not None:
            self.atl(atl, indent + 1)

    def n_Show(self, n: Any, nxt: Any, indent: int) -> None:
        head, extra = self._imspec(getattr(n, "imspec", None))
        self._image_stmt("show", n, head, extra, indent)

    def n_Hide(self, n: Any, nxt: Any, indent: int) -> None:
        head, extra = self._imspec(getattr(n, "imspec", None))
        self._image_stmt("hide", n, head, extra, indent)

    def n_Scene(self, n: Any, nxt: Any, indent: int) -> None:
        spec = getattr(n, "imspec", None)
        if spec:
            head, extra = self._imspec(spec)
        else:                                          # `scene` sola, o `scene onlayer x`
            head, extra = "", ([f"onlayer {_s(n.layer)}"] if getattr(n, "layer", None) else [])
        self._image_stmt("scene", n, head, extra, indent)

    def n_With(self, n: Any, nxt: Any, indent: int) -> None:
        paired = getattr(n, "paired", None)
        expr = _s(getattr(n, "expr", ""))
        if paired is not None:
            if expr == "None":
                self._paired, self._paired_done = paired, False   # se escribirá como `with X` al final del show/scene/hide
                return
            if self._paired_done:
                self._paired_done = False
                return
        self.o.put(indent, f"with {expr}", getattr(n, "linenumber", None))

    # ------------------------------------------------------------------ diálogo
    def _say_text(self, n: Any) -> str:
        parts: List[str] = []
        who = getattr(n, "who", None)
        if who:
            parts.append(_s(who))
        parts.extend(_s(a) for a in (getattr(n, "attributes", None) or ()))
        tmp = getattr(n, "temporary_attributes", None)
        if tmp:
            parts.append("@")
            parts.extend(_s(a) for a in tmp)
        parts.append(encode_text(_s(getattr(n, "what", ""))))
        args = fmt_args(getattr(n, "arguments", None))
        if args:
            parts.append(args)
        if getattr(n, "interact", True) is False:
            parts.append("nointeract")
        if getattr(n, "with_", None):
            parts.append("with " + _s(n.with_))
        return " ".join(parts)

    @staticmethod
    def _caption_of(say: Any, nxt: Any) -> bool:
        """¿Este Say es el texto de cabecera de un `menu:` (se escribe dentro del menú)?"""
        return (type(nxt).__name__ == "Menu" and getattr(say, "interact", True) is False
                and getattr(say, "who", None) is not None and not getattr(say, "with_", None)
                and not getattr(say, "attributes", None) and bool(getattr(nxt, "items", None))
                and nxt.items[0][2] is not None)

    def n_Say(self, n: Any, nxt: Any, indent: int) -> int:
        if self._caption_of(n, nxt):
            self._menu(nxt, indent, name=None, caption=n)
            return 2
        self.o.put(indent, self._say_text(n), getattr(n, "linenumber", None))
        return 1

    # ------------------------------------------------------------------ flujo
    def n_Label(self, n: Any, nxt: Any, indent: int) -> int:
        block = getattr(n, "block", None) or []
        name = _s(n.name)
        if not block and type(nxt).__name__ == "Menu" and getattr(nxt, "linenumber", None) == getattr(n, "linenumber", 0):
            self._menu(nxt, indent, name=name)                          # `menu nombre:` = Label + Menu
            return 2
        if name.startswith("_") and not block and not getattr(n, "parameters", None):
            return 1                                                    # etiquetas internas generadas por Ren'Py
        head = f"label {name}{fmt_params(getattr(n, 'parameters', None))}"
        if getattr(n, "hide", False):
            head += " hide"
        self.o.put(indent, head + ":", getattr(n, "linenumber", None))
        self.block(block, indent + 1, allow_empty=True)
        return 1

    def n_Menu(self, n: Any, nxt: Any, indent: int) -> None:
        self._menu(n, indent, name=None)

    def _menu(self, menu: Any, indent: int, name: Optional[str], caption: Any = None) -> None:
        self.o.put(indent, "menu" + (f" {name}" if name else "") + ":", getattr(menu, "linenumber", None))
        if getattr(menu, "set", None):
            self.o.put(indent + 1, f"set {_s(menu.set)}")
        if caption is not None:
            self.o.put(indent + 1, self._say_text(caption).replace(" nointeract", ""), getattr(caption, "linenumber", None))
        if getattr(menu, "with_", None):
            self.o.put(indent + 1, f"with {_s(menu.with_)}")
        for item in getattr(menu, "items", None) or []:
            label, cond, block = (list(item) + [None, None])[:3]
            line = encode_text(_s(label))
            if block is None:                                           # texto suelto dentro del menú
                self.o.put(indent + 1, line)
                continue
            if cond not in (None, "True", True):
                line += f" if {_s(cond)}"
            self.o.put(indent + 1, line + ":")
            self.block(block, indent + 2)

    def n_If(self, n: Any, nxt: Any, indent: int) -> None:
        for i, (cond, block) in enumerate(getattr(n, "entries", None) or []):
            if i == 0:
                head, line = f"if {_s(cond)}:", getattr(n, "linenumber", None)
            elif _s(cond) == "True":
                head, line = "else:", None
            else:
                head, line = f"elif {_s(cond)}:", None
            self.o.put(indent, head, line)
            self.block(block, indent + 1)

    def n_While(self, n: Any, nxt: Any, indent: int) -> None:
        self.o.put(indent, f"while {_s(n.condition)}:", getattr(n, "linenumber", None))
        self.block(getattr(n, "block", None), indent + 1)

    def n_Pass(self, n: Any, nxt: Any, indent: int) -> None:
        self.o.put(indent, "pass", getattr(n, "linenumber", None))

    def n_Jump(self, n: Any, nxt: Any, indent: int) -> None:
        expr = "expression " if getattr(n, "expression", False) else ""
        self.o.put(indent, f"jump {expr}{_s(n.target)}", getattr(n, "linenumber", None))

    def n_Call(self, n: Any, nxt: Any, indent: int) -> int:
        args = fmt_args(getattr(n, "arguments", None))
        if getattr(n, "expression", False):
            text = f"call expression {_s(n.label)}" + (f" pass {args}" if args else "")
        else:
            text = f"call {_s(n.label)}{args}"
        used = 1
        if type(nxt).__name__ == "Label" and not getattr(nxt, "block", None):      # `call x from nombre`
            used = 2
            if not _s(nxt.name).startswith("_"):
                text += f" from {_s(nxt.name)}"
        self.o.put(indent, text, getattr(n, "linenumber", None))
        return used

    def n_Return(self, n: Any, nxt: Any, indent: int) -> None:
        expr = getattr(n, "expression", None)
        self.o.put(indent, "return" + (f" {_s(expr)}" if expr else ""), getattr(n, "linenumber", None))

    def n_UserStatement(self, n: Any, nxt: Any, indent: int) -> None:
        """play / stop / queue / pause / window / voice ...: Ren'Py guarda la línea tal cual."""
        self.o.put(indent, _s(getattr(n, "line", "")), getattr(n, "linenumber", None))

    def n_Translate(self, n: Any, nxt: Any, indent: int) -> None:
        if getattr(n, "language", None) is None:                         # idioma por defecto: lleva el texto original
            for child in getattr(n, "block", None) or []:
                self.stmt(child, None, indent)

    # ------------------------------------------------------------------ Python
    def _python(self, n: Any, indent: int, word: str = "python") -> None:
        src = code_source(getattr(n, "code", None))
        body = src.strip("\n")
        store = getattr(n, "store", "store") or "store"
        line = getattr(n, "linenumber", None)
        if word == "python" and not src.startswith("\n") and "\n" not in body and store == "store" \
                and not getattr(n, "hide", False):
            self.o.put(indent, "$ " + body, line)
            return
        head = word + (" hide" if getattr(n, "hide", False) else "") + \
            (f" in {store[6:] if store.startswith('store.') else store}" if store != "store" else "")
        self.o.put(indent, head + ":", line)
        for raw in src.split("\n")[1 if src.startswith("\n") else 0:]:
            self.o.put(indent + 1, raw) if raw.strip() else self.o.lines.append("")

    def n_Python(self, n: Any, nxt: Any, indent: int) -> None:
        self._python(n, indent)

    def n_EarlyPython(self, n: Any, nxt: Any, indent: int) -> None:
        self._python(n, indent, "python early")

    # ------------------------------------------------------------------ init y definiciones
    def n_Init(self, n: Any, nxt: Any, indent: int) -> None:
        kids = [k for k in (getattr(n, "block", None) or []) if k is not None]
        prio = getattr(n, "priority", 0) or 0
        line = getattr(n, "linenumber", None)
        if len(kids) == 1 and getattr(kids[0], "linenumber", None) == line:
            k = kids[0]
            kind = type(k).__name__
            if kind in _INIT_IMPLICIT:                                   # `define`, `image`, `transform`, `style`...
                self._prio = prio
                self.stmt(k, None, indent)
                return
            if kind in ("Python", "EarlyPython"):                        # `init [N] python:`
                src = code_source(getattr(k, "code", None))
                store = getattr(k, "store", "store") or "store"
                head = "init" + (f" {prio}" if prio else "") + " python" + \
                    (f" in {store[6:] if store.startswith('store.') else store}" if store != "store" else "")
                self.o.put(indent, head + ":", line)
                for raw in src.split("\n")[1 if src.startswith("\n") else 0:]:
                    self.o.put(indent + 1, raw) if raw.strip() else self.o.lines.append("")
                return
        self.o.put(indent, "init" + (f" {prio}" if prio else "") + ":", line)
        self.block(kids, indent + 1)

    _prio = 0

    def _var_name(self, n: Any) -> str:
        store = getattr(n, "store", "store") or "store"
        name = _s(getattr(n, "varname", ""))
        return name if store == "store" else f"{store[6:] if store.startswith('store.') else store}.{name}"

    def _define(self, word: str, n: Any, indent: int) -> None:
        prio = f"{self._prio} " if self._prio and word == "define" else ""
        self._prio = 0
        op = getattr(n, "operator", None) or "="
        self.o.put(indent, f"{word} {prio}{self._var_name(n)} {op} {code_source(getattr(n, 'code', None))}",
                   getattr(n, "linenumber", None))

    def n_Define(self, n: Any, nxt: Any, indent: int) -> None:
        self._define("define", n, indent)

    def n_Default(self, n: Any, nxt: Any, indent: int) -> None:
        self._define("default", n, indent)

    def n_Image(self, n: Any, nxt: Any, indent: int) -> None:
        name = " ".join(_s(x) for x in getattr(n, "imgname", ()))
        atl = getattr(n, "atl", None)
        code = getattr(n, "code", None)
        line = getattr(n, "linenumber", None)
        if atl is not None:
            self.o.put(indent, f"image {name}:", line)
            self.atl(atl, indent + 1)
        else:
            self.o.put(indent, f"image {name} = {code_source(code)}", line)

    def n_Transform(self, n: Any, nxt: Any, indent: int) -> None:
        self.o.put(indent, f"transform {_s(n.varname)}{fmt_params(getattr(n, 'parameters', None))}:",
                   getattr(n, "linenumber", None))
        self.atl(getattr(n, "atl", None), indent + 1)

    def n_Style(self, n: Any, nxt: Any, indent: int) -> None:
        props = getattr(n, "properties", None) or {}
        parent = getattr(n, "parent", None)
        head = f"style {_s(n.style_name)}" + (f" is {_s(parent)}" if parent else "")
        self.o.put(indent, head + (":" if props else ""), getattr(n, "linenumber", None))
        for k, v in props.items():
            self.o.put(indent + 1, f"{_s(k)} {_s(v)}")

    # ------------------------------------------------------------------ ATL
    def atl(self, block: Any, indent: int) -> None:
        lines = self._atl_lines(block, 0)
        if not lines:
            self.o.put(indent, "pass")
        for rel, text in lines:
            self.o.put(indent + rel, text)

    def _atl_lines(self, block: Any, rel: int) -> List[Tuple[int, str]]:
        out: List[Tuple[int, str]] = []
        if block is None:
            return out
        if getattr(block, "animation", False):
            out.append((rel, "animation"))
        for st in getattr(block, "statements", None) or []:
            out.extend(self._atl_stmt(st, rel))
        return out

    def _atl_sub(self, head: str, block: Any, rel: int) -> List[Tuple[int, str]]:
        body = self._atl_lines(block, rel + 1)
        return [(rel, head + ":")] + (body or [(rel + 1, "pass")])

    def _atl_stmt(self, st: Any, rel: int) -> List[Tuple[int, str]]:
        kind = type(st).__name__
        if kind == "RawMultipurpose":
            parts: List[str] = []
            wf, warper = getattr(st, "warp_function", None), getattr(st, "warper", None)
            if wf:
                parts += ["warp", _s(wf), _s(getattr(st, "duration", "0"))]
            elif warper:
                parts += [_s(warper), _s(getattr(st, "duration", "0"))]
            for expr, with_ in getattr(st, "expressions", None) or []:
                parts.append(_s(expr) + (f" with {_s(with_)}" if with_ else ""))
            for name, expr in getattr(st, "properties", None) or []:
                parts.append(f"{_s(name)} {_s(expr)}")
            for name, exprs in getattr(st, "splines", None) or []:
                exprs = list(exprs)
                parts.append(f"{_s(name)} " + " knot ".join(_s(e) for e in exprs))
            if getattr(st, "revolution", None):
                parts.append(_s(st.revolution))
            if getattr(st, "circles", "0") not in (None, "0", 0):
                parts += ["circles", _s(st.circles)]
            return [(rel, " ".join(parts))] if parts else []
        if kind == "RawBlock":
            return self._atl_sub("block", st, rel)
        if kind == "RawParallel":
            out: List[Tuple[int, str]] = []
            for b in getattr(st, "blocks", None) or []:
                out += self._atl_sub("parallel", b, rel)
            return out
        if kind == "RawChoice":
            out = []
            for chance, b in getattr(st, "choices", None) or []:
                ch = _s(chance)
                out += self._atl_sub("choice" + ("" if ch in ("1.0", "1", "") else f" {ch}"), b, rel)
            return out
        if kind == "RawChild":
            out = []
            for b in getattr(st, "children", None) or []:
                out += self._atl_sub("contains", b, rel)
            return out
        if kind == "RawContainsExpr":
            return [(rel, f"contains {_s(getattr(st, 'expression', ''))}")]
        if kind == "RawRepeat":
            rep = getattr(st, "repeats", None)
            return [(rel, "repeat" + (f" {_s(rep)}" if rep else ""))]
        if kind == "RawTime":
            return [(rel, f"time {_s(st.time)}")]
        if kind == "RawEvent":
            return [(rel, f"event {_s(st.name)}")]
        if kind == "RawFunction":
            return [(rel, f"function {_s(st.expr)}")]
        if kind == "RawOn":
            out = []
            for name, b in (getattr(st, "handlers", None) or {}).items():
                out += self._atl_sub(f"on {_s(name)}", b, rel)
            return out
        self.warn("ATL " + kind)
        return []


def decompile(stmts: Sequence[Any]) -> Decompiled:
    """Lista de nodos (RpycFile.stmts) -> texto .rpy y avisos."""
    d = Decompiler()
    stmts = list(stmts)
    # Ren'Py añade un `return` implícito al final de cada archivo: es un Return suelto en la raíz, justo detrás de un
    # label con cuerpo. (En el formato antiguo, con sentencias planas, un return en la raíz sí es real.)
    if len(stmts) >= 2 and type(stmts[-1]).__name__ == "Return" and not getattr(stmts[-1], "expression", None) \
            and type(stmts[-2]).__name__ == "Label" and getattr(stmts[-2], "block", None):
        stmts.pop()
    d.block(stmts, 0, allow_empty=True)
    return Decompiled(d.o.text(), d.warnings)
