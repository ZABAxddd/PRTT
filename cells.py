"""
cells.py - Lógica de celdas del preview: quién ocupa cada celda, posiciones tXY y zorder.

No depende de Tkinter, así que se puede probar sin ventana.

Nomenclatura (igual que Ren'Py/DDLC):  t{X}{Y}
    X = cuántos personajes hay en escena (count)
    Y = celda del personaje, de izquierda a derecha (slot)

Reglas implementadas
  1. Detección previa: el CellLayout se construye desde el escenario del guion, así que ya
     sabe quién está colocado antes de añadir nada.
  2. Reasignación al cambiar la cantidad: cada personaje CONSERVA su celda (slot) y cambia X.
         1 -> 2 : t11 -> t21            1 -> 3 : t11 -> t31
         2 -> 3 : t21 -> t31, t22 -> t32
     Si al bajar la cantidad un personaje se queda fuera (slot > nuevo count), se mueve a la
     celda libre más cercana. Nunca se pierde un personaje salvo que se pida "Ninguno".
  3. Celda ocupada: put() reemplaza; swap() intercambia (o mueve si la otra está vacía).
     Si el personaje ya estaba en otra celda, put() lo intercambia/mueve en vez de duplicarlo
     (un tag solo puede aparecer una vez en pantalla).
  4. zorder: cada Placement lleva su zorder (mayor = más al frente, como en Ren'Py).
"""
from __future__ import annotations

import copy
import keyword
import os
import re
from dataclasses import dataclass, field, replace
from typing import Callable, Dict, List, Optional, Tuple

from rpy_parser import HIDE_TRANSFORMS, POS_RE, parse_position
from stage import Actor, Frame, Stage

try:  # Pillow es opcional (solo para las vistas previas)
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None

MAX_CELLS = 4


def pos_name(count: int, slot: int) -> str:
    return f"t{count}{slot}" if count and slot else ""


_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
_RESERVED = set(keyword.kwlist) | {"at", "as", "behind", "onlayer", "zorder", "with",
                                   "show", "hide", "scene", "transform", "image"}


def split_top(text: str) -> List[str]:
    """Parte `t21, hop(x=400, z=0.9)` por las comas de NIVEL SUPERIOR (las de dentro de paréntesis,
    corchetes o comillas no cuentan). No valida nada."""
    out: List[str] = []
    depth, quote, cur = 0, "", []
    i = 0
    while i < len(text or ""):
        ch = text[i]
        if quote:
            cur.append(ch)
            if ch == "\\" and i + 1 < len(text):
                i += 1
                cur.append(text[i])
            elif ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
            cur.append(ch)
        elif ch in "([{":
            depth += 1
            cur.append(ch)
        elif ch in ")]}":
            depth -= 1
            cur.append(ch)
        elif ch == "," and depth == 0:
            out.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
        i += 1
    out.append("".join(cur).strip())
    return out


_CALL_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\s*(\((.*)\))?$", re.S)


def parse_transform_list(text: str) -> List[str]:
    """'t21' -> ['t21'];  't21, hop' -> ['t21', 'hop'];  'hop(x=400, z=0.9)' -> ['hop(x=400, z=0.9)'].

    ValueError si no puede ir tras un `at`. Es la ÚNICA validación del transform: cualquier nombre con
    forma de identificador vale (t11, t45, hf32, mi_pos...), y también una llamada con argumentos entre
    paréntesis (modo avanzado). Lo que no se pueda escribir en un `at` se rechaza."""
    raw = (text or "").strip()
    if not raw:
        raise ValueError("escribe un transform (por ejemplo t21)")
    if "\n" in raw or "#" in raw:
        raise ValueError("el transform debe ir en una sola línea y sin comentarios")
    names = split_top(raw)
    for n in names:
        m = _CALL_RE.match(n)
        if not n or not m or m.group(1) in _RESERVED:
            raise ValueError(f"transform no válido: «{n}». Usa un nombre como t21 o mi_pos, o una llamada "
                             "como hop(x=400); varios, separados por comas.")
        if m.group(2) and split_top(m.group(3)) is None:      # (por si acaso)
            raise ValueError(f"paréntesis mal cerrados en «{n}»")
        depth = 0
        for ch in n:
            depth += (ch in "([{") - (ch in ")]}")
            if depth < 0:
                raise ValueError(f"paréntesis mal cerrados en «{n}»")
        if depth != 0:
            raise ValueError(f"paréntesis mal cerrados en «{n}»")
    return names


def call_name(expr: str) -> str:
    """'hop(x=400)' -> 'hop';  'hf21' -> 'hf21'."""
    m = _CALL_RE.match((expr or "").strip())
    return m.group(1) if m else (expr or "").strip()


@dataclass
class Placement:
    tag: str
    attrs: Tuple[str, ...] = ()
    zorder: int = 0
    transform: str = ""          # "" = el estándar de la celda (t{count}{slot}); si no, texto libre del `at`

    @property
    def image(self) -> str:
        return " ".join((self.tag,) + tuple(self.attrs))


@dataclass
class CellLayout:
    count: int = 0
    slots: Dict[int, Placement] = field(default_factory=dict)

    # ------------------------------------------------------------------ consultas
    def name(self, slot: int) -> str:
        return pos_name(self.count, slot)

    def at_of(self, slot: int) -> str:
        """Lo que va tras `at` para esa celda: el transform libre si lo hay, si no t{count}{slot}."""
        p = self.slots.get(slot)
        return (p.transform if p is not None and p.transform else self.name(slot))

    def _at(self, slot: int, p: Placement) -> str:
        return p.transform or self.name(slot)

    def _norm(self) -> None:
        """Un transform libre idéntico al estándar de su celda se guarda como "" (así dos
        layouts equivalentes son iguales y el historial no ve cambios falsos)."""
        for k, p in self.slots.items():
            if p.transform and p.transform == self.name(k):
                p.transform = ""

    def occupants(self) -> List[Placement]:
        return [self.slots[k] for k in sorted(self.slots)]

    def slot_of(self, tag: str) -> int:
        for k, p in self.slots.items():
            if p.tag == tag:
                return k
        return 0

    def copy(self) -> "CellLayout":
        return CellLayout(self.count, {k: replace(p) for k, p in self.slots.items()})

    # ------------------------------------------------------------------ cambiar la cantidad
    @staticmethod
    def _floating(p: Placement) -> bool:
        """Transform con nombre propio (mi_pos): no ocupa una celda tXY, así que no depende del count."""
        if not p.transform:
            return False
        pos = parse_position(split_top(p.transform)[0])
        return pos is None or pos.motion == "hide"

    def set_count(self, n: int) -> Tuple[int, List[str]]:
        """Cambia el nº de personajes en escena. Devuelve (count, [movimientos]).

        Si baja, los personajes de las celdas que desaparecen (los de la DERECHA) se quitan solos
        (4 -> 1 deja solo a quien está en la celda 1). No pide confirmación: se deshace con Ctrl+Z.
        Un transform libre atado a su celda (hf21, h21...) cambia su cifra de cantidad con ella;
        uno con un nombre propio (mi_pos) se queda como está.
        """
        n = max(0, min(int(n), MAX_CELLS))
        old_count = self.count
        before = {k: (p.image, self.at_of(k)) for k, p in self.slots.items()}
        moves: List[str] = []
        if n == 0:
            self.slots.clear()
            self.count = 0
            return 0, [f"{img}: {old} -> fuera" for img, old in before.values()]
        for k in sorted(k for k in self.slots if k > n and not self._floating(self.slots[k])):
            moves.append(f"{self.slots[k].image}: {before[k][1]} -> fuera")
            del self.slots[k]
        self.count = n
        for k, p in self.slots.items():
            names = split_top(p.transform) if p.transform else []
            m = POS_RE.match(names[0]) if names else None
            if m and int(m.group(2)) == old_count and int(m.group(3)) == k and old_count != n:
                names[0] = f"{m.group(1)}{n}{k}"
                p.transform = ", ".join(names)
        self._norm()
        after = {p.tag: self.at_of(k) for k, p in self.slots.items()}
        for img, old in before.values():
            tag = img.split()[0]
            if after.get(tag) and after[tag] != old:
                moves.append(f"{img}: {old} -> {after[tag]}")
        return n, moves

    # ------------------------------------------------------------------ editar celdas
    def _check(self, slot: int) -> None:
        if not 1 <= slot <= self.count:
            raise ValueError(f"celda {slot} fuera de rango (hay {self.count})")

    def put(self, slot: int, p: Placement) -> str:
        """Coloca `p` en `slot`. Reemplaza al ocupante; si `p` ya estaba en otra celda, la libera
        (o intercambia con el ocupante). Devuelve un texto para mostrar al usuario."""
        self._check(slot)
        old = self.slots.get(slot)
        j = self.slot_of(p.tag)
        if j and j != slot:
            if old is not None:
                old.transform = ""                       # cambia de celda: vuelve al estándar
                self.slots[j] = old
                msg = f"{p.image} -> {self._at(slot, p)} · {old.image} -> {self.name(j)} (intercambio)"
            else:
                del self.slots[j]
                msg = f"{p.image}: {self.name(j)} -> {self._at(slot, p)}"
        elif old is not None and old.tag != p.tag:
            msg = f"{old.image} reemplazado por {p.image} en {self._at(slot, p)}"
        else:
            msg = f"{p.image} en {self._at(slot, p)}"
        self.slots[slot] = p
        self._norm()
        return msg

    def swap(self, a: int, b: int) -> str:
        """Intercambia las celdas a y b (si b está vacía, mueve el personaje)."""
        self._check(a)
        self._check(b)
        pa, pb = self.slots.get(a), self.slots.get(b)
        for k, p in ((a, pb), (b, pa)):
            if p is None:
                self.slots.pop(k, None)
            else:
                p.transform = ""                         # cambió de celda: vuelve al estándar
                self.slots[k] = p
        if pa is not None and pb is not None:
            return f"{pa.image} <-> {pb.image}"
        who = pa or pb
        return f"{who.image}: {self.name(a if pa else b)} -> {self.name(b if pa else a)}" if who else ""

    def remove(self, slot: int) -> str:
        p = self.slots.pop(slot, None)
        return f"{p.image} quitado" if p else ""

    def drag(self, src: int, dst: int) -> str:
        """Arrastrar la celda `src` a `dst`. Si `dst` está vacía, el personaje se mueve. Si está ocupada,
        quien estaba allí se desplaza a la celda vacía MÁS PRÓXIMA (a igual distancia, la del lado de
        donde viene el arrastre) en vez de pisarse o intercambiarse a ciegas."""
        self._check(src)
        self._check(dst)
        mover = self.slots.get(src)
        if mover is None:
            raise ValueError(f"la celda {self.name(src)} está vacía")
        if src == dst:
            return ""
        occupant = self.slots.get(dst)
        del self.slots[src]
        mover.transform = ""                              # cambió de celda: vuelve al estándar
        self.slots[dst] = mover
        if occupant is None:
            self._norm()
            return f"{mover.image}: {self.name(src)} -> {self.name(dst)}"
        free = [k for k in range(1, self.count + 1) if k not in self.slots]   # al menos `src`
        side = 1 if src > dst else -1
        to = min(free, key=lambda f: (abs(f - dst), 0 if (f - dst) * side > 0 else 1))
        occupant.transform = ""
        self.slots[to] = occupant
        self._norm()
        return f"{mover.image} -> {self.name(dst)} · {occupant.image} se desplaza a {self.name(to)}"

    def _first_free(self) -> int:
        for k in range(1, MAX_CELLS + 1):
            if k not in self.slots:
                return k
        raise ValueError("no hay ninguna celda libre: elige una celda concreta")

    def place_at(self, transform: str, p: Placement, slot: Optional[int] = None,
                 reflow: bool = True) -> str:
        """Coloca `p` con el transform que el usuario ESCRIBA (`at ...`). Sin lista cerrada:
        vale t11, t45, hf32, mi_pos o varios separados por comas ("t21, hop").

        reflow=True y el transform es una posición con cantidad/celda coherentes (t21, hf32...
        con cantidad <= MAX_CELLS): se reajustan los demás personajes (t11 -> t21...) y `p` va a
        la celda que dice el nombre. En cualquier otro caso (reflow=False, o un nombre que no
        es posición) el transform se aplica TAL CUAL en `slot` (o la celda del nombre, o la que
        ya ocupaba `p`, o la primera libre) y no se toca a nadie más.
        Solo se rechaza lo que no se puede escribir tras un `at` (ValueError)."""
        names = parse_transform_list(transform)
        pos = parse_position(names[0])
        if pos is not None and pos.motion == "hide":
            pos = None
        notes: List[str] = []
        if reflow and pos is not None and pos.count <= MAX_CELLS:
            n, k = pos.count, pos.slot
            prefix = POS_RE.match(names[0]).group(1)
            if n != self.count:
                eff, moves = self.set_count(n)
                notes += moves
                if eff != n:
                    notes.append(f"no se puede usar {names[0]}: hay {eff} personajes colocados")
                    k = min(k, eff)
            names[0] = f"{prefix}{self.count}{k}"
            target = k
        else:
            target = slot
            if target is None:
                target = (pos.slot if pos is not None and pos.slot <= MAX_CELLS
                          else (self.slot_of(p.tag) or self._first_free()))
            if not 1 <= target <= MAX_CELLS:
                raise ValueError(f"celda {target} fuera de rango (1..{MAX_CELLS})")
            if target > self.count:
                _eff, moves = self.set_count(target)
                notes += moves
        placed = replace(p, transform=", ".join(names))
        notes.append(self.put(target, placed))
        return "  ·  ".join(notes)

    def set_zorder(self, slot: int, z: int) -> None:
        if slot in self.slots:
            self.slots[slot].zorder = int(z)


# --------------------------------------------------------------------------------------
# Conversión desde/hacia el escenario
# --------------------------------------------------------------------------------------
def _stage_transform(a: Actor, std: str) -> str:
    """Transform libre de un personaje colocado, o "" si es justo el estándar de su celda.
    Conserva lo que el guion dice (hf21, `at t21, hop`...) para no reescribirlo como t21."""
    first = a.pos_name if (a.pos_name and (a.pos_name not in HIDE_TRANSFORMS or a.hiding)) else std
    joined = ", ".join([first] + list(a.extra_transforms))
    return "" if joined == std else joined


def layout_from_stage(stage: Stage) -> CellLayout:
    """Detección previa: quién está colocado ya en las celdas del escenario del guion.

    Los personajes sin posición tXY pero con un transform propio (`show x 1a at mi_pos`) también
    entran, en la primera celda libre, para que un guardado no los pierda."""
    n = stage.layout_count()
    lay = CellLayout(count=n)
    ordered = sorted(stage.actors.values(), key=lambda x: (x.zorder, x.seq))
    for a in ordered:
        if 1 <= a.slot <= n:
            lay.slots[a.slot] = Placement(a.tag, tuple(a.attrs), a.zorder,
                                          _stage_transform(a, lay.name(a.slot)))
    for a in ordered:
        if a.count == 0 and a.extra_transforms:
            free = [k for k in range(1, MAX_CELLS + 1) if k not in lay.slots]
            if free:
                lay.slots[free[0]] = Placement(a.tag, tuple(a.attrs), a.zorder,
                                               ", ".join(a.extra_transforms))
    return lay


def hide_list(stage: Stage, lay: CellLayout) -> List[str]:
    """Claves de los personajes que el layout original tenía en celdas y el editado ya no."""
    base = {p.tag for p in layout_from_stage(stage).slots.values()}
    want = {p.tag for p in lay.slots.values()}
    return [k for k, a in stage.actors.items() if a.tag in base and a.tag not in want]


def _target(lay: CellLayout, slot: int, p: Placement):
    """(nombre para dibujar, transforms extra, count, slot) con que se muestra `p` en `slot`."""
    if not p.transform:
        return lay.name(slot), (), lay.count, slot
    names = [x for x in split_top(p.transform) if x]
    pos = parse_position(names[0]) if names else None
    if pos is not None and pos.motion != "hide":
        return names[0], tuple(names[1:]), pos.count, pos.slot
    return names[0], tuple(names[1:]), 0, 0               # nombre propio: no es una celda tXY


def apply_layout(frame: Frame, lay: CellLayout,
                 has_hide_event: Optional[Callable[[str], bool]] = None) -> Frame:
    """Copia del frame con los personajes colocados según `lay` (el original no se toca).

    Los personajes que no cambiaron de celda conservan su movimiento/posición del guion
    (así se sigue animando su entrada); los demás quedan en reposo en su transform.
    """
    stage = copy.deepcopy(frame.stage)
    for key in hide_list(frame.stage, lay):
        stage.actors.pop(key, None)
    for slot, p in lay.slots.items():
        actor = next((a for a in stage.actors.values() if a.tag == p.tag), None)
        name, extras, count, cell = _target(lay, slot, p)
        if actor is None:
            same = False
            stage.seq += 1
            actor = Actor(tag=p.tag, seq=stage.seq)
            stage.actors[p.tag] = actor
        elif p.transform:
            same = (actor.count, actor.slot, actor.pos_name, tuple(actor.extra_transforms)) == \
                   (count, cell, name, extras)
        else:
            same = actor.count == count and actor.slot == cell
        actor.attrs = tuple(p.attrs)
        actor.zorder = p.zorder
        if not same:
            actor.count, actor.slot = count, cell
            actor.pos_name = actor.place_name = name
            actor.extra_transforms = extras
            actor.motion = ""
            actor.hiding = bool(has_hide_event and has_hide_event(lay.at_of(slot)))
        elif has_hide_event and has_hide_event(lay.at_of(slot)):
            actor.hiding = True
    return Frame(frame.index, frame.event, stage, frame.actions)


def to_rpy(lay: CellLayout) -> str:
    """Líneas `show` listas para pegar en el script, con la estructura exacta:

        show [personaje] [pose][expresión] zorder [valor] at [transform]
        show sayori 1a zorder 1 at t11
    """
    return "\n".join(f"show {lay.slots[k].image} zorder {lay.slots[k].zorder} at {lay.at_of(k)}"
                     for k in sorted(lay.slots))


# --------------------------------------------------------------------------------------
# Catálogo para el selector: personaje -> poses (cuerpo+vestimenta) -> expresiones
# --------------------------------------------------------------------------------------
BASE_CHARACTERS = ("sayori", "yuri", "natsuki", "monika")

# Capas de cuerpo: 1l 1r 2l 2r (escolar) · 1bl 1br 2bl 2br (casual)  ->  pose "1", "2", "1b", "2b"
# Capas de cuerpo: 1l 1r 2l 2r (escolar) · 1bl 1br 2bl 2br (casual). Las caras son a, b, c... (o 1t, 2ta...)
_BODY_RE = re.compile(r"^\d+[a-z]*?[lr]$")
_POSE_RE = re.compile(r"^\d+[a-z]*$")
_TOKEN_RE = re.compile(r"^(\d+)([a-z]*)$")
OUTFITS = {"": "Escolar", "b": "Casual"}


def outfit_label(pose: str) -> str:
    """'1' -> Escolar, '1b' -> Casual, otra letra -> 'Variante x', imagen suelta -> 'Especial'."""
    m = _TOKEN_RE.match(pose)
    if not m:
        return "Especial"
    return OUTFITS.get(m.group(2), f"Variante {m.group(2)}")


def character_tags(db) -> List[str]:
    """Tags de personajes con imágenes definidas: primero los 4 base, luego los de tu mod."""
    seen: List[str] = []
    for name in db.images:
        t = name.split()[0]
        if t not in seen:
            seen.append(t)
    chars = {c.image_tag for c in db.characters.values() if c.image_tag}
    found = [t for t in seen if t in chars] or [t for t in seen if t != "bg"]
    base = [t for t in BASE_CHARACTERS if t in seen]      # los 4 base, aunque no tengan Character
    return base + [t for t in found if t not in base]


def transform_choices(db) -> List[str]:
    """Posiciones t11, t21, t22, t31... disponibles. Si tu transforms.rpy las define, solo esas."""
    defined = set(getattr(db, "transforms", ())) | set(getattr(db, "transform_defs", {}))
    allowed = [f"t{n}{k}" for n in range(1, MAX_CELLS + 1) for k in range(1, n + 1)]
    return [t for t in allowed if t in defined] or allowed


@dataclass
class Combo:
    """Una `image tag pose+expresión` ya analizada."""
    name: str
    attrs: Tuple[str, ...]
    layers: List[object]                  # sprites.Layer, en el orden en que se apilan
    face: List[object] = field(default_factory=list)   # las capas que son la cara
    size: Optional[Tuple[int, int]] = None


class SpriteCatalog:
    """Lee las definiciones `image` (db.images, que sale de definitions.rpy) y las separa en
    personaje -> pose (cuerpo + vestimenta) -> expresión (cara).

    Cómo se separa `image sayori 1ba = im.Composite(..., "sayori/1bl.png", "sayori/1br.png", "sayori/a.png")`:
      · la expresión es la capa que NO es de cuerpo (a.png) -> "a"
      · la pose es el resto del nombre -> "1b" (vestimenta casual)
    La pose sale del NOMBRE y no de las capas, porque en DDLC la pose 2 usa 1l+2r y la 3 usa 2l+1r.
    `sayori 1b` (cara b.png) = pose 1 + expresión b; `sayori 1ba` = pose 1b + expresión a.
    """

    def __init__(self, db, book) -> None:
        self.db = db
        self.book = book
        self._tags = character_tags(db)
        self._combos: Dict[str, Dict[Tuple[str, str], Combo]] = {t: {} for t in self._tags}
        self._img: Dict[Tuple[str, str], object] = {}
        for name in db.images:
            parts = name.split()
            if len(parts) < 2 or parts[0] not in self._combos:
                continue
            pose, expr, combo = self._analyze(name, tuple(parts[1:]))
            self._combos[parts[0]].setdefault((pose, expr), combo)
        for table in self._combos.values():       # `sayori 1` es un alias de `sayori 1a`: se oculta
            with_expr = {p for p, e in table if e}
            for key in [k for k in table if not k[1] and k[0] in with_expr]:
                del table[key]

    # ------------------------------------------------------------------ análisis
    def _analyze(self, name: str, attrs: Tuple[str, ...]):
        d = self.book.get_def(name)
        layers = list(d.layers) if d is not None and d.kind in ("composite", "single", "tile") else []
        size = d.size if d is not None else None
        stems = [(l, os.path.splitext(os.path.basename(l.path))[0]) for l in layers]
        nonbody = [(l, st) for l, st in stems if not _BODY_RE.match(st)]
        token = attrs[0] if len(attrs) == 1 else ""
        if token[:1].isdigit():
            for l, st in sorted(nonbody, key=lambda x: -len(x[1])):
                rest = token[:-len(st)] if st else ""
                if st and token.endswith(st) and _POSE_RE.match(rest):
                    return rest, st, Combo(name, attrs, layers, [l], size)
            m = _TOKEN_RE.match(token)
            if m:
                digits, letters = m.groups()
                if not letters:
                    pose, expr = digits, ""
                elif len(letters) >= 2 and letters[0] == "b":
                    pose, expr = digits + "b", letters[1:]
                else:
                    pose, expr = digits, letters
                face = [l for l, _s in nonbody] if expr else []
                return pose, expr, Combo(name, attrs, layers, face, size)
        return " ".join(attrs), "", Combo(name, attrs, layers, [], size)    # imagen suelta (stab_1...)

    # ------------------------------------------------------------------ consultas
    def tags(self) -> List[str]:
        return list(self._tags)

    def poses(self, tag: str) -> List[str]:
        def order(p):                                    # escolar (1, 2...) y luego casual (1b, 2b...)
            m = _TOKEN_RE.match(p)
            return (0, m.group(2), int(m.group(1)), p) if m else (1, "", 0, p)
        return sorted({p for p, _e in self._combos.get(tag, {})}, key=order)

    def exprs(self, tag: str, pose: str) -> List[str]:
        return sorted((e for p, e in self._combos.get(tag, {}) if p == pose), key=lambda e: (len(e), e))

    def combo(self, tag: str, pose: str, expr: str) -> Optional[Combo]:
        return self._combos.get(tag, {}).get((pose, expr))

    def attrs(self, tag: str, pose: str, expr: str) -> Optional[Tuple[str, ...]]:
        c = self.combo(tag, pose, expr)
        return c.attrs if c else None

    def locate(self, tag: str, attrs) -> Optional[Tuple[str, str]]:
        """(pose, expresión) de una pose ya colocada."""
        for key, c in self._combos.get(tag, {}).items():
            if c.attrs == tuple(attrs):
                return key
        return None

    def default_preview(self, tag: str) -> Optional[Tuple[str, ...]]:
        """Sprite de portada: pose «1» con la expresión «a» (p. ej. `sayori 1a`)."""
        poses = self.poses(tag)
        if not poses:
            return None
        pose = "1" if "1" in poses else poses[0]
        exprs = self.exprs(tag, pose)
        return self.attrs(tag, pose, "a" if "a" in exprs else exprs[0]) if exprs else None

    def missing(self, tag: str) -> List[str]:
        """Archivos PNG referenciados por las definiciones de `tag` que no existen en disco."""
        out: List[str] = []
        for c in self._combos.get(tag, {}).values():
            for l in c.layers:
                if l.file is None and l.path not in out:
                    out.append(l.path)
        return out

    # ------------------------------------------------------------------ imágenes (PIL, recortadas al contenido)
    def _render(self, c: Optional[Combo], which: str):
        if c is None or Image is None:
            return None
        key = (c.name, which)
        if key in self._img:
            return self._img[key]
        face_ids = {id(l) for l in c.face}
        if which == "face":
            sel = [l for l in c.layers if id(l) in face_ids]
        elif which == "body":
            sel = [l for l in c.layers if id(l) not in face_ids]
        else:
            sel = list(c.layers)
        opened = []
        for l in sel:
            if l.file:
                try:
                    with Image.open(l.file) as im:
                        opened.append((l, im.convert("RGBA")))
                except OSError:
                    pass
        img = None
        if opened:
            size = c.size or opened[0][1].size
            canvas = Image.new("RGBA", size, (0, 0, 0, 0))
            for l, im in opened:
                tmp = Image.new("RGBA", size, (0, 0, 0, 0))
                tmp.paste(im, (l.x, l.y))
                canvas = Image.alpha_composite(canvas, tmp)
            bbox = canvas.getchannel("A").getbbox()
            img = canvas.crop(bbox) if bbox else canvas
        self._img[key] = img
        return img

    def face_image(self, tag: str, pose: str, expr: str):
        """El .png de la cara (recortado). None si no hay capa de cara o falta el archivo."""
        return self._render(self.combo(tag, pose, expr), "face")

    def sprite_image(self, tag: str, pose: str, expr: str):
        """La pose completa (cuerpo + cara), recortada."""
        return self._render(self.combo(tag, pose, expr), "full")

    def image(self, tag: str, attrs):
        """Sprite completo de `tag attrs` (para miniaturas de portada)."""
        key = self.locate(tag, attrs)
        return self.sprite_image(tag, *key) if key else None
