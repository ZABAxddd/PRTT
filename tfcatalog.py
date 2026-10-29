"""
tfcatalog.py - Lista de transforms por EFECTO, en vez de una variante por cada celda.

Tu transforms.rpy define cada efecto una vez y luego una variante por cantidad/celda:

    transform hop(x=640, z=0.80): ...        <- el efecto
    transform h21: hop(400)                  <- el efecto en la celda 1 de 2 personajes

El selector ya no enseña h11, h21, h22, h31... (50+ nombres repetitivos): enseña los EFECTOS
(`hop`, `hop focus`, `left in`, `thide`...) y al elegir uno PRTT escribe la variante que corresponde a
la celda donde está el personaje (hop + celda 1 de 2  ->  `h21`). Lo que no sea de ese estilo
(panic, thide, transforms propios del mod) se escribe con su nombre tal cual.

No depende de Tkinter.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional, Set
from i18n import _t

from rpy_parser import POS_RE, parse_position

# nombre real, etiqueta, prefijo de las variantes por celda, tipo, ayuda
_KNOWN = (
    ("tcommon", "tcommon", "t", "estándar", _t("Posición normal: entra con fundido desde arriba.")),
    ("tinstant", "tinstant", "i", "estándar", _t("Aparece al instante, sin animación.")),
    ("focus", "focus", "f", "estándar", _t("En foco: un poco más grande.")),
    ("hop", "hop", "h", "movimiento", _t("Pequeño salto.")),
    ("hopfocus", "hop focus", "hf", "movimiento", _t("Salto y en foco a la vez.")),
    ("sink", "sink", "s", "movimiento", _t("Se hunde hacia abajo.")),
    ("dip", "dip", "d", "movimiento", _t("Baja y vuelve a subir.")),
    ("panic", "panic", "", "bucle", _t("Se balancea sin parar (nerviosa).")),
    ("leftin", "left in", "l", "entrada", _t("Entra volando desde la izquierda.")),
    ("rightin", "right in", "r", "entrada", _t("Entra volando desde la derecha.")),
    ("thide", "thide", "", "salida", _t("Se desvanece al salir; PRTT agrega `hide` al guardar.")),
    ("lhide", "left hide", "", "salida", _t("Sale hacia la izquierda; PRTT agrega `hide` al guardar.")),
    ("rhide", "right hide", "", "salida", _t("Sale hacia la derecha; PRTT agrega `hide` al guardar.")),
)
PREFIX_TO_EFFECT = {p: n for n, _l, p, _k, _h in _KNOWN if p}


@dataclass(frozen=True)
class Effect:
    name: str            # transform real: "hopfocus"
    label: str           # lo que se muestra: "hop focus"
    prefix: str = ""     # prefijo de sus variantes por celda ("hf" -> hf21...); "" = sin variantes
    kind: str = "otro"
    help: str = ""
    known: bool = True   # sale de la lista base (False = un transform propio de tu mod)


def _defined(db) -> Set[str]:
    return set(getattr(db, "transforms", ())) | set(getattr(db, "transform_defs", {}))


def is_position_name(name: str) -> bool:
    """t11, h21, hf32, i41... (un efecto ya fijado a una celda)."""
    return bool(POS_RE.match(name)) and parse_position(name) is not None


def effects(db) -> List[Effect]:
    """Efectos para elegir: primero los de DDLC que tu transforms.rpy define, luego el resto de tus
    transforms (sin las variantes numéricas t11, t22...). Sin transforms.rpy, la lista base."""
    defined = _defined(db)
    out = [Effect(n, lab, pre, kind, hlp) for n, lab, pre, kind, hlp in _KNOWN
           if not defined or n in defined]
    base = {e.name for e in out} | {n for n, *_r in _KNOWN}
    extra = sorted(n for n in defined
                   if n not in base and not is_position_name(n) and not n.startswith("_"))
    out += [Effect(n, n, "", "propio", _t("Transform definido en tu transforms.rpy."), known=False) for n in extra]
    return out


def find(db, name: str) -> Optional[Effect]:
    return next((e for e in effects(db) if e.name == name), None)


def resolve(effect: Effect, count: int, slot: int, defined: Iterable[str] = ()) -> str:
    """Texto que va tras `at` para ese efecto en esa celda: `hop` en la celda 1 de 2 -> `h21`."""
    defined = set(defined)
    if effect.prefix and count and slot:
        cand = f"{effect.prefix}{count}{slot}"
        if cand in defined or not defined:
            return cand
    return effect.name


def effect_of(text: str, db) -> Optional[Effect]:
    """El efecto que representa un transform ya escrito (`h21` -> hop, `hop` -> hop, `t21, x` -> tcommon)."""
    first = (text or "").split(",")[0].strip().split("(")[0].strip()
    m = POS_RE.match(first)
    if m and parse_position(first) is not None:
        return find(db, PREFIX_TO_EFFECT.get(m.group(1), ""))
    return find(db, first)


def describe(text: str, db) -> str:
    """'hf21, shake' -> 'hop focus + shake' (para mostrar en etiquetas)."""
    from cells import split_top, call_name
    parts = split_top(text) if text else []
    shown = []
    for i, part in enumerate(parts):
        eff = effect_of(part, db) if (i == 0 or not is_position_name(call_name(part))) else None
        shown.append(eff.label if eff else part)
    return " + ".join(shown)
