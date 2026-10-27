"""
guiconf.py - Lee de los .rpy del mod cómo se ve la caja de texto: tamaños, posiciones,
fuente, colores y outlines (define gui.* y los bloques `style`).

Cada valor sale, por orden, de: el `style` correspondiente -> el `define gui.*` -> un valor
por defecto. `GuiConf.sources` dice de dónde salió cada uno (para que puedas comprobar que el
preview usa TUS valores y no los de reserva). `python prtt.py --gui` los imprime.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

Color = Tuple[int, int, int, int]
Outline = Tuple[int, Color, float, float]


def parse_color(value, default: Color = (255, 255, 255, 255)) -> Color:
    """'#rgb' '#rgba' '#rrggbb' '#rrggbbaa' (o tupla) -> (r, g, b, a)."""
    if isinstance(value, (tuple, list)) and len(value) in (3, 4):
        v = [int(x) for x in value]
        return (v[0], v[1], v[2], v[3] if len(v) == 4 else 255)
    if not isinstance(value, str):
        return default
    s = value.strip().lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{3,8}", s) or len(s) in (5, 7):
        return default
    if len(s) in (3, 4):
        s = "".join(c * 2 for c in s)
    r, g, b = int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    a = int(s[6:8], 16) if len(s) == 8 else 255
    return (r, g, b, a)


# --------------------------------------------------------------------------------------
# Evaluador de expresiones de gui.* (números, strings, tuplas, Borders(...), otros gui.x)
# --------------------------------------------------------------------------------------
def _dotted(node: ast.AST) -> str:
    parts: List[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return ""


class _Eval:
    def __init__(self, db) -> None:
        self.db = db

    def __call__(self, expr: str, depth: int = 0):
        if depth > 8:
            raise ValueError("referencia circular")
        return self._n(ast.parse(expr.strip(), mode="eval").body, depth)

    def _n(self, n: ast.AST, depth: int):
        if isinstance(n, ast.Constant):
            return n.value
        if isinstance(n, (ast.Name, ast.Attribute)):
            key = _dotted(n)
            if key in self.db.defines:
                return self(self.db.defines[key], depth + 1)
            if key in self.db.variables:
                return self.db.variables[key]
            if key in ("None", "True", "False"):
                return {"None": None, "True": True, "False": False}[key]
            raise ValueError(f"desconocido: {key}")
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.UAdd)):
            v = self._n(n.operand, depth)
            return -v if isinstance(n.op, ast.USub) else v
        if isinstance(n, ast.BinOp):
            a, b = self._n(n.left, depth), self._n(n.right, depth)
            ops = {ast.Add: lambda: a + b, ast.Sub: lambda: a - b, ast.Mult: lambda: a * b,
                   ast.Div: lambda: a / b, ast.FloorDiv: lambda: a // b}
            if type(n.op) in ops:
                return ops[type(n.op)]()
        if isinstance(n, (ast.Tuple, ast.List)):
            return tuple(self._n(e, depth) for e in n.elts)
        if isinstance(n, ast.Call) and _dotted(n.func) in ("Borders", "Color", "tuple"):
            return tuple(self._n(a, depth) for a in n.args)
        raise ValueError("expresión no soportada")


@dataclass
class GuiConf:
    width: int = 1280
    height: int = 720
    textbox_img: str = "gui/textbox.png"
    textbox_height: float = 182
    textbox_yalign: float = 0.99
    namebox_img: str = "gui/namebox.png"
    name_xpos: float = 350
    name_ypos: float = 0
    name_xalign: float = 0.5
    namebox_width: Optional[float] = 168
    namebox_height: Optional[float] = 39
    namebox_borders: Tuple[int, int, int, int] = (5, 5, 5, 2)
    dialogue_xpos: float = 268
    dialogue_ypos: float = 50
    dialogue_width: float = 744
    font: str = "gui/font/Aller_Rg.ttf"
    text_size: int = 28
    text_color: Color = (255, 255, 255, 255)
    outlines: List[Outline] = field(default_factory=lambda: [(2, (0, 0, 0, 255), 0, 0)])
    line_spacing: float = 0
    name_font: str = "gui/font/Aller_Rg.ttf"
    name_size: int = 30
    name_color: Color = (255, 255, 255, 255)
    name_outlines: List[Outline] = field(default_factory=lambda: [
        (3, (187, 85, 153, 255), 0, 0), (1, (187, 85, 153, 255), 2, 2)])
    sources: Dict[str, str] = field(default_factory=dict)

    def describe(self) -> List[str]:
        rows = []
        for key in self.sources:
            rows.append(f"{key:<18} = {getattr(self, key)!r:<46} <- {self.sources[key]}")
        return rows


def _first_image_path(expr: str) -> Optional[str]:
    m = re.search(r"""["']([^"']+\.(?:png|jpg|jpeg|webp|gif))["']""", expr, re.I)
    return m.group(1) if m else None


def _outlines(val) -> List[Outline]:
    out: List[Outline] = []
    for item in (val if isinstance(val, (tuple, list)) else ()):
        if isinstance(item, (tuple, list)) and len(item) >= 2:
            dx = item[2] if len(item) > 2 else 0
            dy = item[3] if len(item) > 3 else 0
            out.append((max(int(round(float(item[0]))), 0), parse_color(item[1]), float(dx), float(dy)))
    return out


def load_gui(db) -> GuiConf:
    ev = _Eval(db)
    g = GuiConf()

    def pick(key: str, styles: List[Tuple[str, str]], gui_names: List[str], default, conv=lambda v: v):
        for style, prop in styles:
            expr = db.style_props(style).get(prop)
            if expr:
                try:
                    setattr(g, key, conv(ev(expr)))
                    g.sources[key] = f"style {style}.{prop}"
                    return
                except (ValueError, SyntaxError, TypeError):
                    pass
        for name in gui_names:
            expr = db.defines.get("gui." + name)
            if expr:
                try:
                    setattr(g, key, conv(ev(expr)))
                    g.sources[key] = f"define gui.{name}"
                    return
                except (ValueError, SyntaxError, TypeError):
                    pass
        setattr(g, key, default)
        g.sources[key] = "POR DEFECTO (no lo encontré en tus .rpy)"

    # --- tamaño de pantalla: gui.init(1280, 720) no es legible desde .rpy; se asume 1280x720
    g.sources["width"] = g.sources["height"] = "fijo 1280x720"

    # --- caja de texto
    pick("textbox_height", [("window", "ysize")], ["textbox_height"], 182, float)
    pick("textbox_yalign", [("window", "yalign")], ["textbox_yalign"], 0.99, float)
    bg = db.style_props("window").get("background", "")
    path = _first_image_path(bg)
    g.textbox_img = path or "gui/textbox.png"
    g.sources["textbox_img"] = "style window.background" if path else "POR DEFECTO"

    # --- caja del nombre
    pick("name_xpos", [("namebox", "xpos")], ["name_xpos"], 350, float)
    pick("name_ypos", [("namebox", "ypos")], ["name_ypos"], 0, float)
    pick("name_xalign", [("namebox", "xanchor")], ["name_xalign"], 0.5, float)
    pick("namebox_width", [("namebox", "xsize")], ["namebox_width"], 168,
         lambda v: None if v is None else float(v))
    pick("namebox_height", [("namebox", "ysize")], ["namebox_height"], 39,
         lambda v: None if v is None else float(v))
    pick("namebox_borders", [], ["namebox_borders"], (5, 5, 5, 2),
         lambda v: tuple(int(x) for x in v)[:4] if len(v) >= 4 else (int(v[0]),) * 4)
    nb = _first_image_path(db.style_props("namebox").get("background", ""))
    g.namebox_img = nb or "gui/namebox.png"
    g.sources["namebox_img"] = "style namebox.background" if nb else "POR DEFECTO"

    # --- diálogo
    pick("dialogue_xpos", [("say_dialogue", "xpos")], ["dialogue_xpos"], 268, float)
    pick("dialogue_ypos", [("say_dialogue", "ypos")], ["dialogue_ypos"], 50, float)
    pick("dialogue_width", [("say_dialogue", "xsize")], ["dialogue_width"], 744, float)
    pick("font", [("say_dialogue", "font")], ["default_font"], "gui/font/Aller_Rg.ttf", str)
    pick("text_size", [("say_dialogue", "size")], ["text_size"], 28, lambda v: int(round(float(v))))
    pick("text_color", [("say_dialogue", "color")], ["text_color"], (255, 255, 255, 255), parse_color)
    pick("line_spacing", [("say_dialogue", "line_spacing")], ["line_spacing"], 0, float)
    default_outline = [(2, (0, 0, 0, 255), 0, 0)]
    pick("outlines", [("say_dialogue", "outlines")], [], default_outline, _outlines)

    # --- texto del nombre
    pick("name_font", [("say_label", "font")], ["name_text_font"], g.font, str)
    pick("name_size", [("say_label", "size")], ["name_text_size"], 30, lambda v: int(round(float(v))))
    pick("name_color", [("say_label", "color")], ["accent_color"], (255, 255, 255, 255), parse_color)
    pick("name_outlines", [("say_label", "outlines")], [], list(g.name_outlines), _outlines)
    return g
