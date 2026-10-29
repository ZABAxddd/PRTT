"""
transitions.py - Transiciones de Ren'Py (`with dissolve`, `with wipeleft`...) dibujadas con Pillow.

Sirve para la vista previa en tiempo real del selector de escenarios: dadas la imagen vieja y la nueva y un
instante `t` (segundos), `render()` devuelve el fotograma. No ejecuta Ren'Py: reproduce el efecto de cada
transición estándar y lee las tuyas de los `define nombre = Dissolve(...)` de tus .rpy.

Qué transiciones salen en la lista (collect_transitions):
  · las estándar de Ren'Py (siempre existen en cualquier juego);
  · las que defines tú: `define mi_fade = Fade(...)`, `Dissolve`, `ImageDissolve`, `CropMove`, `Pixellate`...;
  · las que tus scripts USAN (`with dissolve_scene_full`) aunque no estén definidas en tus .rpy (suelen venir
    del juego base, dentro de un .rpa): se dibujan por su nombre y se marcan como «aproximada».

No depende de Tkinter. Requiere Pillow solo para render().
"""
from __future__ import annotations

import ast
import math
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional
from i18n import _t

try:
    from PIL import Image
    HAVE_PIL = True
except ImportError:  # pragma: no cover
    Image = None
    HAVE_PIL = False


@dataclass
class Spec:
    name: str
    kind: str                       # none dissolve fade pixellate wipe slide slideaway iris blinds squares punch mask
    duration: float
    params: Dict[str, object] = field(default_factory=dict)
    source: str = "renpy"           # renpy | mod | usada
    approx: bool = False            # el dibujo es una aproximación del efecto real
    note: str = ""

    @property
    def label(self) -> str:
        tag = {"renpy": "", "mod": _t("  · tuya"), "usada": _t("  · usada en tus scripts")}[self.source]
        return f"{self.name}{tag}{'  (aprox.)' if self.approx else ''}"


def _spec(name, kind, dur, approx=False, **params) -> Spec:
    return Spec(name, kind, dur, dict(params), "renpy", approx)


def _standard() -> List[Spec]:
    out = [
        _spec("dissolve", "dissolve", 0.5),
        _spec("fade", "fade", 1.0, out=0.5, hold=0.0, inn=0.5, color=(0, 0, 0)),
        _spec("flash", "fade", 0.3, True, out=0.0, hold=0.0, inn=0.3, color=(255, 255, 255)),
        _spec("pixellate", "pixellate", 1.0, steps=5),
        _spec("blinds", "blinds", 1.0, True),
        _spec("squares", "squares", 1.0, True),
        _spec("irisout", "iris", 1.0, mode="out"),
        _spec("irisin", "iris", 1.0, mode="in"),
        _spec("hpunch", "punch", 0.25, axis="x"),
        _spec("vpunch", "punch", 0.25, axis="y"),
        _spec("zoomin", "dissolve", 0.5, True),
        _spec("zoomout", "dissolve", 0.5, True),
        _spec("zoominout", "dissolve", 0.5, True),
    ]
    for d in ("left", "right", "up", "down"):
        out.append(_spec("wipe" + d, "wipe", 1.0, dir=d))
        out.append(_spec("slide" + d, "slide", 1.0, dir=d))
        out.append(_spec("slideaway" + d, "slideaway", 1.0, dir=d))
        out.append(_spec("push" + d, "push", 0.5, dir=d))
        out.append(_spec("movein" + {"left": "left", "right": "right", "up": "top", "down": "bottom"}[d],
                         "slide", 0.5, True, dir={"left": "right", "right": "left", "up": "down", "down": "up"}[d]))
        out.append(_spec("moveout" + {"left": "left", "right": "right", "up": "top", "down": "bottom"}[d],
                         "slideaway", 0.5, True, dir=d))
        out.append(_spec("easein" + {"left": "left", "right": "right", "up": "top", "down": "bottom"}[d],
                         "slide", 0.5, True, dir={"left": "right", "right": "left", "up": "down", "down": "up"}[d]))
        out.append(_spec("easeout" + {"left": "left", "right": "right", "up": "top", "down": "bottom"}[d],
                         "slide", 0.5, True, dir={"left": "right", "right": "left", "up": "down", "down": "up"}[d]))
    out.append(_spec("move", "dissolve", 0.5, True))
    out.append(_spec("ease", "dissolve", 0.5, True))
    out.append(_spec("easein", "dissolve", 0.5, True))
    out.append(_spec("easeout", "dissolve", 0.5, True))
    return out


STANDARD: Dict[str, Spec] = {s.name: s for s in _standard()}
NONE_SPEC = Spec(_t("(ninguna)"), "none", 0.0)


# --------------------------------------------------------------------------------------
# Lectura de tus `define nombre = Transicion(...)`
# --------------------------------------------------------------------------------------
_CTORS = {"Dissolve", "ImageDissolve", "AlphaDissolve", "Fade", "Pixellate", "CropMove", "MoveTransition",
          "MultipleTransition", "ComposeTransition", "PushMove", "Move", "ZoomInOut", "SlideDisplay"}
_CROP = {
    "wipeleft": ("wipe", "left"), "wiperight": ("wipe", "right"), "wipeup": ("wipe", "up"),
    "wipedown": ("wipe", "down"),
    "slideleft": ("slide", "left"), "slideright": ("slide", "right"), "slideup": ("slide", "up"),
    "slidedown": ("slide", "down"),
    "slideawayleft": ("slideaway", "left"), "slideawayright": ("slideaway", "right"),
    "slideawayup": ("slideaway", "up"), "slideawaydown": ("slideaway", "down"),
}


def _name_of(node) -> str:
    parts: List[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _lit(node, default=None):
    try:
        return ast.literal_eval(node)
    except Exception:
        return default


def _color(v, default=(0, 0, 0)):
    if isinstance(v, str) and re.fullmatch(r"#[0-9a-fA-F]{3,8}", v):
        s = v[1:]
        if len(s) in (3, 4):
            s = "".join(c * 2 for c in s)
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    return default


def _multi_steps(lst, expr, known, find_image):
    """Interpreta las pantallas y transiciones que componen un MultipleTransition."""
    out = []
    for i, e in enumerate(lst.elts):
        item = None
        if isinstance(e, ast.Constant) and isinstance(e.value, bool):
            item = ("new", None) if e.value else ("old", None)
        elif isinstance(e, ast.Call):
            nm = _name_of(e.func).split(".")[-1]
            arg = _lit(e.args[0]) if e.args else None
            if nm == "Solid":
                item = ("solid", _color(arg))
            elif nm == "Pause" and isinstance(arg, (int, float)):
                item = ("pause", float(arg))
            else:
                child_expr = ast.get_source_segment(expr, e)
                child = parse_constructor(nm, child_expr, known, find_image) if child_expr else None
                if child is not None and not child.approx:
                    item = ("transition", child)
        elif isinstance(e, (ast.Name, ast.Attribute)):
            child_expr = ast.get_source_segment(expr, e)
            nm = _name_of(e).split(".")[-1]
            child = parse_constructor(nm, child_expr, known, find_image) if child_expr else None
            if child is not None and not child.approx:
                item = ("transition", child)
        if item is None or (item[0] in ("transition", "pause")) != (i % 2 == 1):
            return None
        out.append(item)
    return out if len(out) >= 3 and len(out) % 2 == 1 else None


def _has_unsupported_time_warp(node) -> bool:
    for kw in node.keywords:
        if kw.arg != "time_warp":
            continue
        if isinstance(kw.value, ast.Constant) and kw.value.value is None:
            continue
        if _name_of(kw.value) == "_warper.linear":
            continue
        return True
    return False


def parse_constructor(name: str, expr: str, known: Dict[str, Spec],
                      find_image: Optional[Callable[[str], object]] = None) -> Optional[Spec]:
    """Spec de `define name = <expr>` o None si no es una transición."""
    expr = expr.strip()
    if re.fullmatch(r"[A-Za-z_][\w.]*", expr):                       # alias de otra transición
        base = known.get(expr)
        if base is None:
            return None
        return Spec(name, base.kind, base.duration, dict(base.params), "mod", base.approx, base.note)
    try:
        node = ast.parse(expr, mode="eval").body
    except SyntaxError:
        return None
    if not isinstance(node, ast.Call):
        return None
    ctor = _name_of(node.func).split(".")[-1]
    if ctor not in _CTORS:
        return None
    a = [_lit(x) for x in node.args]
    kw = {k.arg: _lit(k.value) for k in node.keywords if k.arg}
    num = lambda v, d: float(v) if isinstance(v, (int, float)) else d

    if ctor == "Dissolve":
        unsupported_warp = _has_unsupported_time_warp(node)
        return Spec(name, "dissolve", num(a[0] if a else kw.get("time"), 0.5), source="mod",
                    approx=unsupported_warp,
                    note=_t("time_warp personalizado no reproducido") if unsupported_warp else "")
    if ctor == "Fade":
        out_, hold, inn = (num(a[i] if len(a) > i else kw.get(("out_time", "hold_time", "in_time")[i]), d)
                           for i, d in enumerate((0.5, 0.0, 0.5)))
        col = _color(kw.get("color") if "color" in kw else (a[3] if len(a) > 3 else None))
        return Spec(name, "fade", out_ + hold + inn, dict(out=out_, hold=hold, inn=inn, color=col), "mod")
    if ctor == "Pixellate":
        return Spec(name, "pixellate", num(a[0] if a else kw.get("time"), 1.0),
                    dict(steps=int(num(a[1] if len(a) > 1 else kw.get("steps"), 5))), "mod")
    if ctor == "ImageDissolve":
        path = a[0] if a and isinstance(a[0], str) else None
        dur = num(a[1] if len(a) > 1 else kw.get("time"), 1.0)
        ramp = int(num(a[2] if len(a) > 2 else kw.get("ramplen"), 8))
        mask = find_image(path) if (find_image and path) else None
        if mask is None:
            return Spec(name, "dissolve", dur, source="mod", approx=True,
                        note=_t("no encuentro la imagen de máscara {0!r}", path))
        unsupported_warp = _has_unsupported_time_warp(node)
        return Spec(name, "mask", dur, dict(mask=mask, ramp=max(ramp, 1), reverse=bool(kw.get("reverse"))), "mod",
                    approx=unsupported_warp,
                    note=_t("time_warp personalizado no reproducido") if unsupported_warp else "")
    if ctor == "CropMove":
        dur = num(a[0] if a else kw.get("time"), 1.0)
        mode = a[1] if len(a) > 1 else kw.get("mode")
        if isinstance(mode, str) and mode in _CROP:
            kind, d = _CROP[mode]
            return Spec(name, kind, dur, dict(dir=d), "mod")
        if mode in ("irisout", "irisin"):
            return Spec(name, "iris", dur, dict(mode=mode[4:]), "mod")
        return Spec(name, "dissolve", dur, source="mod", approx=True, note=_t("CropMove modo {0!r}", mode))
    if ctor == "MultipleTransition" and node.args and isinstance(node.args[0], (ast.List, ast.Tuple)):
        steps = _multi_steps(node.args[0], expr, known, find_image)
        if steps is not None:
            total = sum(value.duration if kind == "transition" else value
                        for kind, value in steps[1::2])
            return Spec(name, "multi", total, dict(steps=steps), "mod")
    dur = num(a[0] if a else kw.get("delay", kw.get("time")), 0.5)
    return Spec(name, "dissolve", dur, source="mod", approx=True, note=_t("{0}: dibujada como disolución", ctor))


def guess_by_name(name: str) -> Spec:
    """Transición que viene del juego base (no está en tus .rpy): se adivina por su nombre."""
    low = name.lower()
    for d in ("left", "right", "up", "down"):
        if "wipe" + d in low:
            return Spec(name, "wipe", 1.0, dict(dir=d), "usada", True, _t("adivinada por el nombre"))
        if "slide" + d in low:
            return Spec(name, "slide", 1.0, dict(dir=d), "usada", True, _t("adivinada por el nombre"))
    if "fade" in low:
        return Spec(name, "fade", 1.0, dict(out=0.5, hold=0.0, inn=0.5, color=(0, 0, 0)), "usada", True,
                    _t("adivinada por el nombre"))
    return Spec(name, "dissolve", 0.5 if "half" not in low else 0.25, source="usada", approx=True,
                note=_t("adivinada por el nombre: no la encuentro definida en tus .rpy"))


_IDENT = re.compile(r"^[A-Za-z_]\w*$")


def used_names(db) -> List[str]:
    """Nombres de transición que tus scripts usan con `with` (en orden de aparición, sin repetir)."""
    seen: List[str] = []
    for ev in db.events:
        cand = []
        if ev.kind == "with":
            cand.append(ev.data.get("expr", ""))
        elif ev.kind in ("scene", "show", "hide", "say"):
            cand.append(ev.data.get("with") or "")
        for c in cand:
            c = (c or "").strip()
            if _IDENT.match(c) and c != "None" and c not in seen:
                seen.append(c)
    return seen


def collect_transitions(db, find_image: Optional[Callable[[str], object]] = None) -> List[Spec]:
    """(ninguna), las estándar, las tuyas y las que tus scripts usan sin tenerlas definidas."""
    known: Dict[str, Spec] = dict(STANDARD)
    mine: List[Spec] = []
    for name, expr in db.defines.items():
        if "." in name:
            continue
        spec = parse_constructor(name, expr, known, find_image)
        if spec is not None:
            known[name] = spec
            mine.append(spec)
    extra = [guess_by_name(n) for n in used_names(db) if n not in known]
    return [NONE_SPEC] + [s for s in STANDARD.values() if s.name not in {m.name for m in mine}] + mine + extra


def find_spec(specs: List[Spec], name: str) -> Optional[Spec]:
    return next((s for s in specs if s.name == name), None)


# --------------------------------------------------------------------------------------
# Dibujo
# --------------------------------------------------------------------------------------
def render(spec: Spec, old, new, t: float):
    """Fotograma en el instante `t` (s). `old` y `new`: imágenes RGBA del mismo tamaño."""
    w, h = new.size
    p = 1.0 if spec.duration <= 0 else max(0.0, min(1.0, t / spec.duration))
    k, P = spec.kind, spec.params
    if k == "none" or (p >= 1.0 and k not in ("punch", "multi")):
        return new.copy()
    if k == "dissolve":
        return Image.blend(old, new, p)
    if k == "fade":
        out_, hold, inn = P["out"], P["hold"], P["inn"]
        col = Image.new("RGBA", (w, h), tuple(P["color"]) + (255,))
        if t < out_:
            return Image.blend(old, col, t / out_ if out_ else 1.0)
        if t < out_ + hold:
            return col
        return Image.blend(col, new, (t - out_ - hold) / inn if inn else 1.0)
    if k == "pixellate":
        q = p * 2 if p < 0.5 else (1 - p) * 2
        src = old if p < 0.5 else new
        block = 2 ** int(round(q * int(P.get("steps", 5))))
        if block <= 1:
            return src.copy()
        small = src.resize((max(1, w // block), max(1, h // block)), Image.BILINEAR)
        return small.resize((w, h), Image.NEAREST)
    if k == "multi":
        steps = P["steps"]

        def screen(item):
            kind, val = item
            return old if kind == "old" else new if kind == "new" else Image.new("RGBA", (w, h), tuple(val) + (255,))

        t0 = 0.0
        for i in range(1, len(steps), 2):
            kind, value = steps[i]
            d = value.duration if kind == "transition" else value
            if t < t0 + d or i == len(steps) - 2:
                a_img, b_img = screen(steps[i - 1]), screen(steps[i + 1])
                if kind == "transition":
                    return render(value, a_img, b_img, t - t0)
                return b_img if t >= t0 + d else a_img
            t0 += d
        return screen(steps[-1]).copy()
    out = old.copy()
    if k == "wipe":
        d = P["dir"]
        box = {"left": (int(w * (1 - p)), 0, w, h), "right": (0, 0, int(w * p), h),
               "up": (0, int(h * (1 - p)), w, h), "down": (0, 0, w, int(h * p))}[d]
        if box[2] > box[0] and box[3] > box[1]:
            out.paste(new.crop(box), box[:2])
        return out
    if k == "slide":
        d = P["dir"]
        off = {"left": (int(w * (1 - p)), 0), "right": (-int(w * (1 - p)), 0),
               "up": (0, int(h * (1 - p))), "down": (0, -int(h * (1 - p)))}[d]
        out.paste(new, off)
        return out
    if k == "slideaway":
        d = P["dir"]
        off = {"left": (-int(w * p), 0), "right": (int(w * p), 0),
               "up": (0, -int(h * p)), "down": (0, int(h * p))}[d]
        base = new.copy()
        base.paste(old, off)
        return base
    if k == "push":
        d = P["dir"]
        old_off, new_off = {
            "left": ((-int(w * p), 0), (int(w * (1 - p)), 0)),
            "right": ((int(w * p), 0), (-int(w * (1 - p)), 0)),
            "up": ((0, -int(h * p)), (0, int(h * (1 - p)))),
            "down": ((0, int(h * p)), (0, -int(h * (1 - p)))),
        }[d]
        frame = Image.new("RGBA", (w, h))
        frame.paste(old, old_off)
        frame.paste(new, new_off)
        return frame
    if k == "iris":
        pp = p if P["mode"] == "out" else 1 - p
        box = (int(w / 2 * (1 - pp)), int(h / 2 * (1 - pp)), int(w / 2 * (1 + pp)), int(h / 2 * (1 + pp)))
        if P["mode"] == "out":
            if box[2] > box[0] and box[3] > box[1]:
                out.paste(new.crop(box), box[:2])
            return out
        base = new.copy()                                   # irisin: el viejo se encoge hacia el centro
        if box[2] > box[0] and box[3] > box[1]:
            base.paste(old.crop(box), box[:2])
        return base
    if k == "blinds":
        n = 16
        sw = max(1, w // n)
        rw = int(sw * p)
        for i in range(n + 1):
            x0 = i * sw
            if rw > 0 and x0 < w:
                out.paste(new.crop((x0, 0, min(x0 + rw, w), h)), (x0, 0))
        return out
    if k == "squares":
        cell = max(4, w // 16)
        sz = int(cell * p)
        if sz > 0:
            for y0 in range(0, h, cell):
                for x0 in range(0, w, cell):
                    right, bottom = min(x0 + cell, w), min(y0 + cell, h)
                    cx, cy = (x0 + right) // 2, (y0 + bottom) // 2
                    box = (max(cx - sz // 2, x0), max(cy - sz // 2, y0),
                           min(cx + sz // 2 + 1, right), min(cy + sz // 2 + 1, bottom))
                    if box[2] > box[0] and box[3] > box[1]:
                        out.paste(new.crop(box), box[:2])
        return out
    if k == "punch":
        amp = (1 - p) * (w if P["axis"] == "x" else h) * 0.03
        s = int(round(math.sin(p * math.pi * 10) * amp))
        base = Image.new("RGBA", (w, h), (0, 0, 0, 255))
        base.paste(new, (s, 0) if P["axis"] == "x" else (0, s))
        return base
    if k == "mask":
        cache = P.setdefault("_cache", {})
        mask = cache.get((w, h))
        if mask is None:
            mask = P["mask"].convert("RGB").getchannel("R").resize((w, h))
            if P["reverse"]:
                mask = mask.point(lambda v: 255 - v)
            cache[(w, h)] = mask
        ramp = P["ramp"]
        offset = -1.0 + (1.0 + ramp / 256.0) * p
        lut = [max(0, min(255, int(255 * (v / 255.0 + offset) * 256.0 / ramp)))
               for v in range(256)]
        return Image.composite(new, old, mask.point(lut))
    return new.copy()
