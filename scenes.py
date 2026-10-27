"""
scenes.py - Fondos (backgrounds) disponibles en tus .rpy y el modelo del carrusel del selector de escenarios.

Qué se detecta (collect_backgrounds):
  · todas las `image bg ... = ...` / `image bg ...:` (definitions.rpy o cualquier otro .rpy);
  · fondos de color sólido: `image bg negro = "#000000"` (y `black`, que Ren'Py trae de serie);
  · los que tus `scene` usan pero no están definidos (se marcan «sin definir»);
  · opcionalmente, cualquier otra imagen definida que no sea un personaje (cg, dark, white...).
Un fondo de color nuevo se crea con color_background(): al aplicarlo se escribe su `image bg color_rrggbb = "#rrggbb"`
justo después de tus otras definiciones de fondo.

No depende de Tkinter.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

HEX_RE = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_COLOR_EXPR = re.compile(r"""^\s*["'](#[0-9a-fA-F]{3,8})["']\s*$""")


@dataclass
class Background:
    name: str                 # nombre completo de la imagen: "bg club_day"
    kind: str                 # image | color | atl | other | missing
    expr: str = ""
    file: str = ""            # archivo .rpy donde se define
    line: int = 0
    color: str = ""           # "#rrggbb" si es de color sólido
    new: bool = False         # color creado en el selector, aún sin escribir en los .rpy

    @property
    def short(self) -> str:
        return self.name[3:] if self.name.startswith("bg ") else self.name

    @property
    def origin(self) -> str:
        if self.new:
            return "nuevo: se definirá al aplicar"
        if self.kind == "missing":
            return "sin definir (tus scripts lo usan)"
        return f"{self.file}:{self.line}" if self.file else "de serie en Ren'Py"


_SOLID = re.compile(r"""^\s*Solid\(\s*(?:["'](#[0-9a-fA-F]{3,8})["']|\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*\d+\s*)?\))""")


def expr_color(expr: str) -> str:
    """'#rrggbb' si la expresión es un color sólido: `"#123"`, `"#112233aa"`, `Solid("#123")`, `Solid((r, g, b))`."""
    m = _COLOR_EXPR.match(expr or "")
    s = m.group(1) if m else None
    if s is None:
        m = _SOLID.match(expr or "")
        if m and m.group(1):
            s = m.group(1)
        elif m:
            return "#%02x%02x%02x" % tuple(min(int(m.group(i)), 255) for i in (2, 3, 4))
    if s is None:
        return ""
    digits = s[1:]
    if len(digits) in (4, 8):                         # con alfa: se ignora
        digits = digits[:-1] if len(digits) == 4 else digits[:6]
    return normalize_hex("#" + digits) or ""


def normalize_hex(value: str) -> Optional[str]:
    """'#abc' / '#AABBCC' -> '#aabbcc'. None si no es un color."""
    value = (value or "").strip()
    if not HEX_RE.match(value):
        return None
    s = value[1:].lower()
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    return "#" + s


def color_background(hex_color: str) -> Background:
    c = normalize_hex(hex_color)
    if c is None:
        raise ValueError(f"color no válido: {hex_color!r} (usa #rrggbb)")
    return Background(f"bg color_{c[1:]}", "color", f'"{c}"', color=c, new=True)


def definition_line(bg: Background) -> str:
    return f'image {bg.name} = "{bg.color}"'


def collect_backgrounds(db, include_all: bool = False) -> List[Background]:
    where = {}
    for ev in db.events:
        if ev.kind == "image":
            where.setdefault(ev.data["name"], (ev.file, ev.line))
    chars = {c.image_tag for c in db.characters.values() if c.image_tag}
    out: List[Background] = []
    for name, expr in db.images.items():
        tag = name.split()[0]
        col = expr_color(expr)
        solid = bool(col) and tag not in chars               # `image black = "#000000"` o `Solid(...)` también valen como fondo
        if tag != "bg" and not solid and not (include_all and tag not in chars):
            continue
        f, ln = where.get(name, ("", 0))
        kind = "atl" if expr == "<ATL>" else "color" if col else "image"
        out.append(Background(name, kind, expr, f, ln, color=col))
    have = {b.name for b in out}
    if "black" not in db.images and "black" not in have:
        out.append(Background("black", "color", '"#000000"', color="#000000"))
        have.add("black")
    for ev in db.events:                                   # los que se usan en `scene` pero no están definidos
        if ev.kind == "scene" and not ev.data.get("onlayer"):
            nm = ev.data.get("name", "")
            if nm and nm not in have and nm not in db.images and not nm.startswith("expression"):
                out.append(Background(nm, "missing", "", ev.file, ev.line))
                have.add(nm)
    return out


@dataclass
class SceneModel:
    """Qué fondo está elegido en el carrusel (con filtro por nombre). Sin Tk: se prueba aparte."""
    items: List[Background]
    query: str = ""
    index: int = 0
    _view: List[Background] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.refilter()

    def refilter(self, keep: Optional[str] = None) -> None:
        keep = keep or (self.current.name if self._view and self.index < len(self._view) else None)
        q = self.query.strip().lower()
        self._view = [b for b in self.items if q in b.name.lower()]
        self.index = next((i for i, b in enumerate(self._view) if b.name == keep), 0) if keep else 0

    @property
    def view(self) -> List[Background]:
        return self._view

    @property
    def current(self) -> Optional[Background]:
        return self._view[self.index] if 0 <= self.index < len(self._view) else None

    def step(self, d: int) -> Optional[Background]:
        """Mueve el carrusel (circular)."""
        if self._view:
            self.index = (self.index + d) % len(self._view)
        return self.current

    def neighbor(self, d: int) -> Optional[Background]:
        return self._view[(self.index + d) % len(self._view)] if len(self._view) > 1 else None

    def select(self, name: str) -> bool:
        for i, b in enumerate(self._view):
            if b.name == name:
                self.index = i
                return True
        return False

    def add(self, bg: Background) -> Background:
        """Añade (o reutiliza) un fondo y lo deja elegido; limpia el filtro para que se vea."""
        found = next((b for b in self.items if b.name == bg.name), None)
        if found is None:
            self.items.append(bg)
            found = bg
        self.query = ""
        self.refilter(keep=found.name)
        return found
