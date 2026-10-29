"""
sprites.py - Arma las poses de los sprites a partir de las definiciones `image` de los .rpy.

DDLC dibuja cada pose uniendo capas (cuerpo izquierdo, cuerpo derecho, cara...):

    image sayori 1a = im.Composite((960, 960), (0, 0), "sayori/1l.png", (0, 0), "sayori/1r.png", (0, 0), "sayori/a.png")

Este módulo lee esa expresión, localiza cada PNG en /game/images (o en /game) y los apila
con transparencia (Pillow). Pillow es opcional: sin él se muestra la lista de capas.
    pip install pillow
"""
from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple, TYPE_CHECKING
from i18n import _t

if TYPE_CHECKING:
    from PIL import Image as PILImage

try:  # Pillow es opcional
    from PIL import Image
    HAVE_PIL = True
except ImportError:  # pragma: no cover
    Image = None
    HAVE_PIL = False


class SpriteError(Exception):
    pass


@dataclass
class Layer:
    x: int
    y: int
    path: str                    # tal como está escrito en el script ("sayori/1l.png")
    file: Optional[str] = None   # ruta real en disco, o None si no se encontró


@dataclass
class SpriteDef:
    name: str                    # "sayori 1a"
    kind: str                    # "composite" | "single" | "tile" | "unsupported"
    expr: str
    size: Optional[Tuple[int, int]] = None
    layers: List[Layer] = field(default_factory=list)
    note: str = ""               # por qué es "unsupported"

    @property
    def missing(self) -> List[str]:
        return [l.path for l in self.layers if l.file is None]


# --------------------------------------------------------------------------------------
# Lectura de la expresión
# --------------------------------------------------------------------------------------
def _dotted(node: ast.AST) -> str:
    parts: List[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def parse_image_expr(name: str, expr: str) -> SpriteDef:
    expr = expr.strip()

    def bad(note: str) -> SpriteDef:
        return SpriteDef(name, "unsupported", expr, note=note)

    if expr == "<ATL>":
        return bad(_t("definición ATL (image x:), aún no soportada"))
    try:
        node = ast.parse(expr, mode="eval").body
    except SyntaxError:
        return bad(_t("no se pudo leer la expresión"))

    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return SpriteDef(name, "single", expr, layers=[Layer(0, 0, node.value)])

    if isinstance(node, ast.Call):
        fname = _dotted(node.func)
        short = fname.split(".")[-1]
        if short == "Image" and node.args and isinstance(node.args[0], ast.Constant):
            return SpriteDef(name, "single", expr, layers=[Layer(0, 0, str(node.args[0].value))])
        if short in ("LiveTile", "Tile") and node.args and isinstance(node.args[0], ast.Constant) \
                and isinstance(node.args[0].value, str):
            return SpriteDef(name, "tile", expr, layers=[Layer(0, 0, str(node.args[0].value))])
        if short in ("Composite", "LiveComposite"):
            args = node.args
            if len(args) < 3 or len(args) % 2 == 0:
                return bad(_t("{0}: nº de argumentos inesperado", short))
            try:
                size = ast.literal_eval(args[0])
                size = (int(size[0]), int(size[1]))
            except (ValueError, TypeError, IndexError):
                return bad(_t("{0}: tamaño ilegible", short))
            layers: List[Layer] = []
            for i in range(1, len(args), 2):
                try:
                    pos = ast.literal_eval(args[i])
                    px, py = int(pos[0]), int(pos[1])
                except (ValueError, TypeError, IndexError):
                    return bad(_t("{0}: posición de capa ilegible", short))
                img = args[i + 1]
                if not (isinstance(img, ast.Constant) and isinstance(img.value, str)):
                    return bad(_t("{0}: capa que no es un archivo simple (¿im.Scale/Flip?)", short))
                layers.append(Layer(px, py, img.value))
            return SpriteDef(name, "composite", expr, size=size, layers=layers)
        return bad(_t("{0} aún no soportada", fname or 'expresión'))
    return bad(_t("expresión aún no soportada"))


# --------------------------------------------------------------------------------------
# Libro de sprites
# --------------------------------------------------------------------------------------
class SpriteBook:
    def __init__(self, db, game_dir: str) -> None:
        self.db = db
        self.game_dir = os.path.abspath(game_dir)
        self.search_dirs = [os.path.join(self.game_dir, "images"), self.game_dir]
        self._defs: Dict[str, SpriteDef] = {}
        self._by_tokens: Dict[frozenset, str] = {}
        for name in db.images:
            self._by_tokens.setdefault(frozenset(name.split()), name)
        self._cache: Dict[str, Tuple[object, List[str]]] = {}

    # -------------------------------------------------------------- definiciones
    def find_name(self, tag: str, attrs: Sequence[str]) -> Optional[str]:
        """Nombre de imagen definida para `tag attrs` (acepta atributos en otro orden)."""
        tokens = [tag] + list(attrs)
        name = " ".join(tokens)
        if name in self.db.images:
            return name
        return self._by_tokens.get(frozenset(tokens))

    def get_def(self, name: str) -> Optional[SpriteDef]:
        if name not in self.db.images:
            return None
        d = self._defs.get(name)
        if d is None:
            d = parse_image_expr(name, self.db.images[name])
            for layer in d.layers:
                layer.file = self.resolve_file(layer.path)
            self._defs[name] = d
        return d

    def resolve_file(self, path: str) -> Optional[str]:
        path = path.replace("\\", "/")
        for base in self.search_dirs:
            cand = os.path.join(base, *path.split("/"))
            if os.path.isfile(cand):
                return cand
        return None

    # -------------------------------------------------------------- composición
    def compose(self, name: str) -> Tuple["PILImage.Image", List[str]]:
        """Devuelve (imagen_PIL_RGBA, [capas_que_faltan]). Lanza SpriteError si no se puede."""
        if not HAVE_PIL:
            raise SpriteError(_t("Falta Pillow: instala con  pip install pillow"))
        if name in self._cache:
            return self._cache[name]
        d = self.get_def(name)
        if d is None:
            raise SpriteError(_t("No hay definición 'image {0}'", name))
        if d.kind == "unsupported":
            raise SpriteError(d.note)
        found = [l for l in d.layers if l.file]
        if not found:
            raise SpriteError(_t("No encuentro los archivos de imagen (¿están extraídos de images.rpa?)"))
        opened = []
        for l in found:
            with Image.open(l.file) as im:
                opened.append((l, im.convert("RGBA")))
        size = d.size or opened[0][1].size
        canvas = Image.new("RGBA", size, (0, 0, 0, 0))
        for l, im in opened:
            tmp = Image.new("RGBA", size, (0, 0, 0, 0))
            tmp.paste(im, (l.x, l.y))
            canvas = Image.alpha_composite(canvas, tmp)
        result = (canvas, d.missing)
        self._cache[name] = result
        return result


# --------------------------------------------------------------------------------------
# Vista del panel lateral: qué mostrar para un diálogo (sin depender de Tkinter)
# --------------------------------------------------------------------------------------
@dataclass
class CellView:
    slot: int                  # 1..N (0 = sin posición)
    title: str                 # "[1] sayori 1a  (t21)"
    image_name: str = ""       # nombre de la pose, "" si la celda está vacía
    speaking: bool = False
    image: object = None       # PIL.Image o None
    info: str = ""             # texto de ayuda: capas, errores...


def build_cell_views(frame, db, book: SpriteBook) -> List[CellView]:
    stage = frame.stage
    speaker_tag = db.image_tag_of(frame.event.data["who"]) if frame.event.data["who"] else ""
    views: List[CellView] = []

    def make(slot: int, actor) -> CellView:
        pos = f"  ({actor.pos_name})" if actor.pos_name else ""
        label = f"[{slot}] " if slot else _t("[sin posición] ")
        v = CellView(slot, f"{label}{actor.image}{pos}", actor.image,
                     speaking=bool(speaker_tag) and actor.tag == speaker_tag)
        name = book.find_name(actor.tag, actor.attrs)
        if name is None:
            v.info = _t("Sin definición 'image {0}'", actor.image)
            return v
        d = book.get_def(name)
        v.info = describe(d)
        try:
            v.image, _missing = book.compose(name)
        except SpriteError as exc:
            v.info += f"\n! {exc}"
        return v

    cells = stage.cells()
    for i, actor in enumerate(cells, 1):
        if actor is None:
            views.append(CellView(i, _t("[{0}] vacía", i)))
        else:
            views.append(make(i, actor))
    for actor in stage.unplaced():
        views.append(make(0, actor))
    return views


def describe(d: Optional[SpriteDef]) -> str:
    if d is None:
        return ""
    if d.kind == "unsupported":
        return f"{d.name}: {d.note}"
    names = []
    for l in d.layers:
        mark = "" if l.file else _t(" (FALTA)")
        names.append(f"{l.path}{mark}")
    head = f"{d.name}: {'composición' if d.kind == 'composite' else 'imagen'}"
    if d.size:
        head += f" {d.size[0]}x{d.size[1]}"
    return head + _t("\n  capas: ") + ", ".join(names)
