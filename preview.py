"""
preview.py - Dibuja la escena como se vería en el juego (1280x720), sin abrir Ren'Py.

Capas, de atrás hacia adelante:
    1. fondo      (scene)      -> definiciones `image bg ... = "bg/x.png"`, colores "#000000",
                                  LiveTile, `image x:` con ATL (truecenter, choice)
    2. personajes (show)       -> poses armadas con sprites.py, colocadas por transforms.rpy
       y overlays (dark, white, vignette...) ordenados por zorder
    3. textbox, caja del nombre y texto (gui/textbox.png, gui/namebox.png, la fuente del mod
       y sus outlines), con las medidas de guiconf.py

Requiere Pillow (pip install pillow). No modifica ningún archivo.
"""
from __future__ import annotations

import ast
import os
import re
from typing import Dict, List, Optional, Tuple

try:
    from PIL import Image, ImageDraw, ImageFont
    HAVE_PIL = True
except ImportError:  # pragma: no cover
    Image = ImageDraw = ImageFont = None
    HAVE_PIL = False

from atl import DEFAULT_STATE, SCREEN_H, SCREEN_W, TransformLib, parse_body
from guiconf import GuiConf, load_gui, parse_color
from rpy_parser import HIDE_TRANSFORMS, parse_position
from sprites import SpriteBook, SpriteError
from stage import Actor, Overlay
import transitions

W, H = SCREEN_W, SCREEN_H
IMG_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")
# Respaldo si el mod no trae transforms.rpy: x del centro de cada celda (cuántos, cuál)
FALLBACK_X = {"41": 200, "42": 493, "43": 786, "44": 1080, "31": 240, "32": 640, "33": 1040,
              "21": 400, "22": 880, "11": 640}
CELL_W, CELL_H, CELL_Y = 250, 330, 60      # tamaño y altura de las celdas de selección (px de pantalla)
FALLBACK_FONTS =("DejaVuSans.ttf", "arial.ttf", "Arial.ttf", "LiberationSans-Regular.ttf")


def _resample():
    return getattr(Image, "Resampling", Image)


def paste_rgba(canvas, img, x: int, y: int) -> None:
    """alpha_composite que admite posiciones negativas o fuera de pantalla."""
    cw, ch = canvas.size
    iw, ih = img.size
    x0, y0, x1, y1 = max(x, 0), max(y, 0), min(x + iw, cw), min(y + ih, ch)
    if x0 >= x1 or y0 >= y1:
        return
    canvas.alpha_composite(img.crop((x0 - x, y0 - y, x1 - x, y1 - y)), (x0, y0))


def nine_slice(img, size: Tuple[float, float], borders: Tuple[int, int, int, int]):
    """Como Frame(img, borders) de Ren'Py: estira el centro y los lados, deja las esquinas."""
    w, h = max(int(round(size[0])), 1), max(int(round(size[1])), 1)
    l, t, r, b = borders
    iw, ih = img.size
    if w < l + r or h < t + b or iw < l + r + 1 or ih < t + b + 1:
        return img.resize((w, h), _resample().BILINEAR)
    xs, ys = [0, l, iw - r, iw], [0, t, ih - b, ih]
    xd, yd = [0, l, w - r, w], [0, t, h - b, h]
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    for j in range(3):
        for i in range(3):
            dw, dh = xd[i + 1] - xd[i], yd[j + 1] - yd[j]
            if dw <= 0 or dh <= 0 or xs[i + 1] <= xs[i] or ys[j + 1] <= ys[j]:
                continue
            piece = img.crop((xs[i], ys[j], xs[i + 1], ys[j + 1]))
            out.paste(piece.resize((dw, dh), _resample().BILINEAR), (xd[i], yd[j]))
    return out


def _pos(v: float, size: float) -> float:
    """float = fracción del contenedor, int = píxeles (regla de Ren'Py)."""
    return float(v) * size if isinstance(v, float) and abs(v) <= 1.0 and not float(v).is_integer() else float(v)


class Preview:
    def __init__(self, db, game_dir: str, book: Optional[SpriteBook] = None) -> None:
        if not HAVE_PIL:
            raise RuntimeError("Falta Pillow: instala con  py -3 -m pip install pillow")
        self.db = db
        self.game_dir = os.path.abspath(game_dir)
        self.book = book or SpriteBook(db, game_dir)
        self.gui: GuiConf = load_gui(db)
        self.lib = TransformLib(db.transform_defs)
        self.warnings: List[str] = []
        self._cur: List[str] = []
        self._files: Dict[str, object] = {}
        self._fonts: Dict[Tuple[str, int], object] = {}
        self._font_warn: Dict[Tuple[str, int], str] = {}
        self._disp: Dict[str, tuple] = {}
        self._ph: Dict[str, object] = {}
        self._transition_specs = None
        self._transition_masks: Dict[str, object] = {}

    # ------------------------------------------------------------------ avisos y archivos
    def _warn(self, msg: str) -> None:
        if msg not in self._cur:
            self._cur.append(msg)

    def _load_file(self, path: str):
        if path not in self._files:
            img = None
            real = self.book.resolve_file(path)
            if real:
                try:
                    with Image.open(real) as im:
                        img = im.convert("RGBA")
                except (OSError, ValueError):
                    img = None
            self._files[path] = img
        return self._files[path]

    def font(self, path: str, size: int):
        key = (path, size)
        if key in self._fonts:
            if key in self._font_warn:           # repetir el aviso en cada render, no solo el primero
                self._warn(self._font_warn[key])
            return self._fonts[key]
        real = self.book.resolve_file(path) if path else None
        font = None
        for cand in ([real] if real else []) + list(FALLBACK_FONTS):
            try:
                font = ImageFont.truetype(cand, size)
                break
            except OSError:
                continue
        if font is None:
            try:
                font = ImageFont.load_default(size)
            except TypeError:
                font = ImageFont.load_default()
        if not real:
            msg = f"no encuentro la fuente '{path}': uso una de reserva (el texto no será igual al juego)"
            self._font_warn[key] = msg
            self._warn(msg)
        self._fonts[key] = font
        return font

    # ------------------------------------------------------------------ imágenes (fondos, overlays)
    def displayable(self, name: str):
        """(imagen RGBA, 'topleft'|'center') o None. Guarda los avisos para repetirlos en cada render."""
        if name not in self._disp:
            saved, self._cur = self._cur, []
            res = self._displayable(name, 0)
            self._disp[name] = (res, self._cur)
            self._cur = saved
        res, msgs = self._disp[name]
        for m in msgs:
            self._warn(m)
        return res

    def _displayable(self, name: str, depth: int):
        if depth > 6:
            return None
        images = self.db.images
        if name not in images:
            toks = name.split()
            alt = self.book.find_name(toks[0], toks[1:]) if toks else None
            if alt is None:
                self._warn(f"no hay 'image {name}' en tus .rpy")
                return None
            name = alt
        expr = images[name]
        if expr == "<ATL>":
            return self._atl_image(name, depth)
        cm = re.fullmatch(r"""\s*["'](#[0-9a-fA-F]+)["']\s*""", expr)
        if cm:
            return Image.new("RGBA", (W, H), parse_color(cm.group(1))), "topleft"
        d = self.book.get_def(name)
        if d is None:
            return None
        if d.kind == "composite":
            try:
                return self.book.compose(name)[0], "topleft"
            except SpriteError as exc:
                self._warn(f"{name}: {exc}")
                return None
        if d.kind == "single":
            return self._from_string(d.layers[0].path, depth)
        if d.kind == "tile":
            res = self._from_string(d.layers[0].path, depth)
            if res is None:
                return None
            tile, _ = res
            canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            for ty in range(0, H, tile.height):
                for tx in range(0, W, tile.width):
                    canvas.paste(tile, (tx, ty))
            return canvas, "topleft"
        self._warn(f"image {name}: {d.note}")
        return None

    def _from_string(self, s: str, depth: int):
        s = s.strip()
        if s.startswith("#"):
            return Image.new("RGBA", (W, H), parse_color(s)), "topleft"
        if os.path.splitext(s)[1].lower() in IMG_EXT or "/" in s:
            img = self._load_file(s)
            if img is None:
                self._warn(f"no encuentro el archivo de imagen '{s}'")
                return None
            return img, "topleft"
        return self._displayable(s, depth + 1)

    def _atl_image(self, name: str, depth: int):
        nodes = parse_body(self.db.image_atl.get(name, []))
        place = {"v": "topleft"}

        def walk(ns) -> Optional[str]:
            for n in ns:
                if n[0] == "stmt":
                    t = n[1].strip()
                    if t in ("truecenter", "center"):
                        place["v"] = "center"
                    elif t[:1] in "\"'":
                        try:
                            return str(ast.literal_eval(re.match(r"""("[^"]*"|'[^']*')""", t).group(1)))
                        except (ValueError, SyntaxError, AttributeError):
                            continue
                elif n[1].split()[:1] in (["choice"], ["parallel"], ["block"], ["contains"]):
                    r = walk(n[2])
                    if r:
                        return r
            return None

        s = walk(nodes)
        if s is None:
            self._warn(f"image {name}: su ATL no muestra ninguna imagen que yo entienda")
            return None
        res = self._from_string(s, depth + 1)
        if res is None:
            return None
        return res[0], (place["v"] if place["v"] != "topleft" else res[1])

    def _draw_full(self, canvas, name: str, label: str) -> None:
        res = self.displayable(name)
        if res is None:
            ImageDraw.Draw(canvas, "RGBA").rectangle((0, 0, W, H), fill=(36, 36, 44, 255))
            self._label(canvas, f"{label}: {name}\n(sin imagen)", (24, 24))
            return
        img, place = res
        x, y = ((W - img.width) // 2, (H - img.height) // 2) if place == "center" else (0, 0)
        paste_rgba(canvas, img, x, y)

    def _label(self, canvas, text: str, xy, size: int = 22) -> None:
        ImageDraw.Draw(canvas, "RGBA").multiline_text(
            xy, text, font=self.font("", size), fill=(255, 255, 255, 230))

    # ------------------------------------------------------------------ sprites y transforms
    def _placeholder(self, title: str, reason: str):
        key = f"{title}|{reason}"
        if key not in self._ph:
            ph = Image.new("RGBA", (960, 960), (0, 0, 0, 0))
            d = ImageDraw.Draw(ph, "RGBA")
            d.rectangle((60, 60, 900, 900), fill=(40, 40, 60, 110), outline=(255, 255, 255, 200), width=6)
            f = self.font("", 54)
            d.multiline_text((90, 380), f"{title}\n{reason}", font=f, fill=(255, 255, 255, 235), spacing=10)
            self._ph[key] = ph
        return self._ph[key]

    def actor_image(self, actor: Actor):
        name = self.book.find_name(actor.tag, actor.attrs)
        if name is None:
            self._warn(f"no hay 'image {actor.image}' en tus .rpy")
            return self._placeholder(actor.image, "sin definición")
        try:
            img, missing = self.book.compose(name)
        except SpriteError as exc:
            self._warn(f"{name}: {exc}")
            return self._placeholder(name, "sin imagen")
        for m in missing:
            self._warn(f"{name}: falta la capa {m}")
        return img

    def _fallback_state(self, name: str) -> Dict[str, float]:
        pos = parse_position(name)
        self._warn(f"no encuentro el transform '{name}' (transforms.rpy): uso la posición estándar")
        s = dict(DEFAULT_STATE)
        x = FALLBACK_X.get(f"{pos.count}{pos.slot}", 640) if pos else 640
        zoom = 0.8 * (1.05 if pos and pos.motion in ("focus", "hop_focus") else 1.0)
        y = 1.06 if pos and pos.motion == "sink" else 1.03
        s.update(xpos=float(x), xanchor=0.5, ypos=y * H, yanchor=1.0, zoom=zoom)
        return s

    def rest_state(self, actor: Actor) -> Dict[str, float]:
        """Dónde queda el personaje cuando termina la animación."""
        name = actor.pos_name
        if not name or name in HIDE_TRANSFORMS:           # sin posición, o ya se está yendo
            name = f"t{actor.count}{actor.slot}" if actor.count and actor.slot else "t11"
        if self.lib.has(name):
            return self.lib.final(name, "show")
        return self._fallback_state(name)

    def cell_rects(self, count: int) -> List[Tuple[int, str, float, float, float, float]]:
        """Celdas para elegir personaje cuando hay `count` en escena (0 = ninguna).

        Devuelve [(nº de celda, 't21', x0, y0, x1, y1)] en píxeles de la pantalla 1280x720.
        El centro de cada celda sale de la posición real t{count}{celda} de tu transforms.rpy.
        """
        out = []
        for k in range(1, max(count, 0) + 1):
            name = f"t{count}{k}"
            if self.lib.has(name):
                x = float(self.lib.final(name, "show")["xpos"])
            else:
                x = float(FALLBACK_X.get(f"{count}{k}", 640))
            out.append((k, name, x - CELL_W / 2, CELL_Y, x + CELL_W / 2, CELL_Y + CELL_H))
        return out

    def actor_state(self, actor: Actor, prev_actor: Optional[Actor], t: Optional[float]) -> Dict[str, float]:
        name = actor.pos_name
        if t is None or not name or name in HIDE_TRANSFORMS or not self.lib.has(name):
            return self.rest_state(actor)
        if prev_actor is None:
            return self.lib.sample(name, "show", None, t)
        if actor.motion:
            return self.lib.sample(name, "replace", self.rest_state(prev_actor), t)
        return self.rest_state(actor)

    @staticmethod
    def _actor_transform(actor: Actor) -> str:
        names = ([actor.pos_name] if actor.pos_name else []) + list(actor.extra_transforms)
        return ", ".join(dict.fromkeys(name for name in names if name))

    def _ghosts(self, frame, prev) -> List[Tuple[str, Actor, str]]:
        """Personajes que desaparecen entre `prev` y `frame` con thide/lhide/rhide (y su transform)."""
        if prev is None or any(e.kind == "scene" for e in frame.actions):
            return []
        out = []
        for key, pa in prev.stage.actors.items():
            current = frame.stage.actors.get(key)
            tf = self._actor_transform(current) if current is not None and current.hiding else ""
            if tf and not self.lib.has_event(tf, "hide"):
                tf = ""
            if current is None:
                for ev in frame.actions:
                    if ev.kind == "show" and (ev.data["as"] or ev.data["tag"]) == key:
                        candidate = ", ".join(ev.data["at"])
                        if self.lib.has_event(candidate, "hide"):
                            tf = candidate
                            break
                if not tf and any(e.kind == "hide" and e.data["tag"] == key for e in frame.actions):
                    candidate = self._actor_transform(pa)
                    if self.lib.has_event(candidate, "hide"):
                        tf = candidate
            elif not tf:
                candidate = self._actor_transform(current)
                if self.lib.has_event(candidate, "hide") and candidate != self._actor_transform(pa):
                    tf = candidate
            if tf:
                out.append((key, pa, tf))
        for key, actor in frame.stage.actors.items():
            if key not in prev.stage.actors and actor.hiding:
                tf = self._actor_transform(actor)
                if self.lib.has_event(tf, "hide"):
                    out.append((key, actor, tf))
        return out

    def duration(self, frame, prev) -> float:
        """Segundos que dura la animación al pasar de `prev` a `frame`."""
        if prev is None:
            return 0.0
        dur = 0.0
        for key, a in frame.stage.actors.items():
            if not a.pos_name or not self.lib.has(a.pos_name) or a.pos_name in HIDE_TRANSFORMS:
                continue
            pa = prev.stage.actors.get(key)
            if pa is None:
                dur = max(dur, self.lib.duration(a.pos_name, "show"))
            elif a.motion:
                dur = max(dur, self.lib.duration(a.pos_name, "replace"))
        for _key, _pa, tf in self._ghosts(frame, prev):
            dur = max(dur, self.lib.duration(tf, "hide"))
        return dur

    def background_transition(self, frame, prev):
        """Transición declarada para el cambio de fondo entre dos diálogos, si existe."""
        if prev is None or prev.stage.bg == frame.stage.bg:
            return None

        events = frame.actions + [frame.event]
        scene_index = next((i for i in range(len(events) - 1, -1, -1)
                            if events[i].kind == "scene" and not events[i].data.get("onlayer")), None)
        if scene_index is None:
            return None

        expression = None
        for event in events[scene_index:]:
            if event.kind == "with":
                expression = event.data.get("expr", "")
            elif event.kind in ("scene", "show", "hide", "say"):
                attached = event.data.get("with")
                if attached is not None:
                    expression = attached

        expression = (expression or "").strip()
        if not expression or expression == "None":
            return None

        if self._transition_specs is None:
            self._transition_specs = transitions.collect_transitions(self.db, self._transition_mask)
        spec = transitions.find_spec(self._transition_specs, expression)
        if spec is not None:
            return spec
        return transitions.parse_constructor(
            expression, expression, transitions.STANDARD, self._transition_mask)

    def _transition_mask(self, path: str):
        if path not in self._transition_masks:
            real = self.book.resolve_file(path) if path else None
            mask = None
            if real:
                try:
                    with Image.open(real) as image:
                        mask = image.convert("RGB").getchannel("R")
                except (OSError, ValueError):
                    mask = None
            self._transition_masks[path] = mask
        return self._transition_masks[path]

    def _draw_actor(self, canvas, actor: Actor, st: Dict[str, float], quick: bool) -> None:
        img = self.actor_image(actor)
        zx, zy = st["zoom"] * st["xzoom"], st["zoom"] * st["yzoom"]
        w, h = max(int(round(img.width * abs(zx))), 1), max(int(round(img.height * abs(zy))), 1)
        if (w, h) != img.size:
            img = img.resize((w, h), _resample().BILINEAR if quick else _resample().LANCZOS)
        if zx < 0:
            img = img.transpose(Image.FLIP_LEFT_RIGHT)
        if zy < 0:
            img = img.transpose(Image.FLIP_TOP_BOTTOM)
        alpha = min(max(st["alpha"], 0.0), 1.0)
        if alpha <= 0.0:
            return
        if alpha < 1.0:
            img = img.copy()
            img.putalpha(img.getchannel("A").point(lambda v: int(v * alpha)))
        x = st["xpos"] - st["xanchor"] * w + st["xoffset"]
        y = st["ypos"] - st["yanchor"] * h + st["yoffset"]
        paste_rgba(canvas, img, int(round(x)), int(round(y)))

    # ------------------------------------------------------------------ un solo sprite (ventana de transforms)
    def sprite_frame(self, tag: str, attrs, expr: str, event: str, t: Optional[float],
                     start_expr: str = "", bg: str = "", quick: bool = False):
        """Imagen RGB 1280x720 con SOLO ese personaje animado por el transform `expr` (texto del `at`).

        event: "show" (entrada), "replace" (cambio desde `start_expr`) o "hide" (salida).
        t: segundos desde que empezó la animación (None = reposo)."""
        self._cur = []
        canvas = Image.new("RGBA", (W, H), (20, 20, 28, 255))
        if bg:
            self._draw_full(canvas, bg, "fondo")
        actor = Actor(tag=tag, attrs=tuple(attrs))
        if event == "show":
            st = self.lib.sample_expr(expr, "show", None, t if t is not None else 1e6)
        elif event == "hide":
            rest = self.lib.final_expr(expr, "show")
            st = self.lib.sample_expr(expr, "hide", rest, t if t is not None else 1e6)
        else:
            rest = self.lib.final_expr(start_expr or expr, "show")
            st = self.lib.sample_expr(expr, "replace", rest, t if t is not None else 1e6)
        self._draw_actor(canvas, actor, st, quick)
        self.warnings = list(self._cur)
        return canvas.convert("RGB")

    # ------------------------------------------------------------------ textbox
    def speaker_label(self, who_var: str) -> str:
        if not who_var or who_var == "extend":
            return ""
        ch = self.db.characters.get(who_var)
        if ch is None:
            return who_var
        if ch.name_is_var:
            val = self.db.variables.get(ch.name_expr)
            return val if val is not None else f"[{ch.name_expr}]"
        return ch.name_expr

    def dialogue_text(self, text: str, who_var: str) -> str:
        text = text.replace("[[", "\x00")
        text = re.sub(r"\[(\w+)\]", lambda m: self.db.variables.get(m.group(1), m.group(0)), text)
        text = text.replace("\x00", "[").replace("{{", "{")
        ch = self.db.characters.get(who_var) if who_var else None
        if ch:
            text = ch.what_prefix + text + ch.what_suffix
        return text

    @staticmethod
    def _tw(font, text: str) -> float:
        try:
            return font.getlength(text)
        except AttributeError:  # pragma: no cover
            return font.getsize(text)[0]

    @staticmethod
    def _line_h(font) -> float:
        try:
            a, d = font.getmetrics()
            return a + d
        except AttributeError:  # pragma: no cover
            return font.getsize("Ag")[1]

    def wrap(self, text: str, font, width: float) -> List[str]:
        lines: List[str] = []
        for para in text.split("\n"):
            cur = ""
            for word in para.split(" "):
                trial = word if not cur else cur + " " + word
                if cur and self._tw(font, trial) > width:
                    lines.append(cur)
                    cur = word
                else:
                    cur = trial
            lines.append(cur)
        return lines

    def _text(self, draw, xy, text: str, font, fill, outlines) -> None:
        x, y = xy
        for size, color, dx, dy in reversed(outlines):        # el primer outline queda más cerca del texto
            draw.text((x + dx, y + dy), text, font=font, fill=color,
                      stroke_width=int(round(size)), stroke_fill=color)
        draw.text((x, y), text, font=font, fill=fill)

    def _draw_textbox(self, canvas, frame) -> None:
        g = self.gui
        who = frame.event.data["who"]
        label = self.speaker_label(who)
        text = self.dialogue_text(frame.stage.text, who)
        win_h = float(g.textbox_height)
        win_top = float(g.textbox_yalign) * (H - win_h)
        draw = ImageDraw.Draw(canvas, "RGBA")

        tb = self._load_file(g.textbox_img)
        if tb is not None:
            paste_rgba(canvas, tb, int(round((W - tb.width) / 2)), int(round(win_top + win_h - tb.height)))
        else:
            self._warn(f"no encuentro {g.textbox_img}: dibujo una caja de reemplazo")
            draw.rounded_rectangle((0, win_top, W, win_top + win_h), radius=12, fill=(255, 255, 255, 120))

        if label:
            nfont = self.font(g.name_font, g.name_size)
            tw, th = self._tw(nfont, label), self._line_h(nfont)
            bl, bt, br, bb = g.namebox_borders
            bw = g.namebox_width if g.namebox_width else tw + bl + br
            bh = g.namebox_height if g.namebox_height else th + bt + bb
            left = _pos(g.name_xpos, W) - g.name_xalign * bw
            top = win_top + _pos(g.name_ypos, win_h)
            nb = self._load_file(g.namebox_img)
            if nb is not None:
                paste_rgba(canvas, nine_slice(nb, (bw, bh), g.namebox_borders), int(round(left)), int(round(top)))
            else:
                self._warn(f"no encuentro {g.namebox_img}: dibujo una caja de reemplazo")
                draw.rounded_rectangle((left, top, left + bw, top + bh), radius=8, fill=(187, 85, 153, 235))
            inner_w, inner_h = bw - bl - br, bh - bt - bb
            tx = left + bl + (inner_w - tw) * g.name_xalign
            ty = top + bt + (inner_h - th) / 2
            self._text(draw, (tx, ty), label, nfont, g.name_color, g.name_outlines)

        font = self.font(g.font, g.text_size)
        x = _pos(g.dialogue_xpos, W)
        y = win_top + _pos(g.dialogue_ypos, win_h)
        step = self._line_h(font) + g.line_spacing
        for line in self.wrap(text, font, g.dialogue_width):
            self._text(draw, (x, y), line, font, g.text_color, g.outlines)
            y += step

    # ------------------------------------------------------------------ API
    def render(self, frame, prev=None, t: Optional[float] = None, quick: bool = False):
        """Imagen RGB 1280x720 del diálogo `frame`.

        prev, t: si se dan, anima la transición desde el diálogo anterior: `t` = segundos desde
        que empezó (None = ya terminó, estado de reposo).
        """
        self._cur = []
        st = frame.stage
        canvas = Image.new("RGBA", (W, H), (0, 0, 0, 255))
        if st.bg:
            self._draw_full(canvas, st.bg, "fondo")

        animate = prev is not None and t is not None
        ghosts = self._ghosts(frame, prev) if animate else []
        ghost_keys = {key for key, _actor, _tf in ghosts}
        items = []                                            # (zorder, seq, tipo, objeto, estado)
        for layer in st.layers():
            if isinstance(layer, Overlay):
                items.append((layer.zorder, layer.seq, "ov", layer, None))
            else:
                key = next(k for k, v in st.actors.items() if v is layer)
                if key in ghost_keys or (layer.hiding and not animate):
                    continue
                pa = prev.stage.actors.get(key) if prev is not None else None
                items.append((layer.zorder, layer.seq, "actor", layer,
                              self.actor_state(layer, pa, t if animate else None)))
        if animate:
            for _key, pa, tf in ghosts:
                items.append((pa.zorder, pa.seq, "actor", pa, self.lib.sample(tf, "hide", self.rest_state(pa), t)))
        for _z, _s, kind, obj, state in sorted(items, key=lambda i: (i[0], i[1])):
            if kind == "ov":
                self._draw_full(canvas, obj.image, "capa")
            else:
                self._draw_actor(canvas, obj, state, quick)

        self._draw_textbox(canvas, frame)
        self.warnings = list(self._cur)
        return canvas.convert("RGB")
