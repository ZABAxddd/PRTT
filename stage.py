"""
stage.py - Emulador del "escenario": qué se ve en pantalla en cada diálogo.

Toma los eventos de rpy_parser y mantiene el estado: fondo, personajes (con su celda),
hablante y texto. build_timeline() devuelve un Frame por cada línea de diálogo, que es
justo lo que necesita la ventana: un fondo, N celdas (1-4) con su personaje y el texto.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from rpy_parser import Event, ScriptDB, parse_position


@dataclass
class Actor:
    tag: str                          # "sayori", "monika"...
    attrs: Tuple[str, ...] = ()       # ("1a",) -> pose/expresión
    zorder: int = 0                   # como en Ren'Py: 0 si no se indica
    count: int = 0                   # nº de personajes según su posición (0 = sin posición)
    slot: int = 0                     # celda 1..count
    pos_name: str = ""                # "t32", "hf21"... (puede ser "thide")
    place_name: str = ""              # última posición real (nunca thide): la que se dibuja
    motion: str = ""                  # movimiento de ESTE diálogo: appear, hop, focus, ...
    hiding: bool = False              # recibió thide y está esperando "hide"
    extra_transforms: Tuple[str, ...] = ()
    seq: int = 0                      # orden de aparición (desempata el zorder)

    @property
    def image(self) -> str:
        return " ".join((self.tag,) + self.attrs)


@dataclass
class Overlay:
    """Algo mostrado con `show` que NO es un personaje: dark, white, vignette, un CG..."""
    tag: str
    attrs: Tuple[str, ...] = ()
    zorder: int = 0
    seq: int = 0
    at: Tuple[str, ...] = ()

    @property
    def image(self) -> str:
        return " ".join((self.tag,) + self.attrs)


@dataclass
class Stage:
    bg: str = ""
    actors: Dict[str, Actor] = field(default_factory=dict)
    overlays: Dict[str, Overlay] = field(default_factory=dict)
    seq: int = 0
    speaker_var: str = ""
    speaker: str = ""
    text: str = ""
    music: str = ""

    # ------------------------------------------------------------------ consultas
    def layout_count(self) -> int:
        """Cuántas celdas hay que mostrar (1-4...): el mayor 'count' entre los personajes."""
        return max((a.count for a in self.actors.values()), default=0)

    def cells(self) -> List[Optional[Actor]]:
        """Lista de longitud layout_count(); cells()[0] es la celda 1 (izquierda). None = vacía."""
        n = self.layout_count()
        out: List[Optional[Actor]] = [None] * n
        for a in sorted(self.actors.values(), key=lambda x: x.zorder):
            if 1 <= a.slot <= n:
                out[a.slot - 1] = a
        return out

    def unplaced(self) -> List[Actor]:
        """Personajes mostrados sin posición (show monika 1a, sin 'at')."""
        return [a for a in self.actors.values() if a.count == 0]

    def clear_motions(self) -> None:
        for a in self.actors.values():
            a.motion = ""

    def layers(self) -> List[object]:
        """Overlays y personajes mezclados, de atrás hacia adelante (zorder y luego orden de aparición)."""
        items = list(self.actors.values()) + list(self.overlays.values())
        return sorted(items, key=lambda x: (x.zorder, x.seq))

    # ------------------------------------------------------------------ aplicar eventos
    def apply(self, ev: Event, db: ScriptDB) -> None:
        d = ev.data
        k = ev.kind
        if k == "scene":
            if not d.get("onlayer"):
                self.bg = d["name"]
                self.actors.clear()
                self.overlays.clear()
        elif k == "show":
            self._show(d, db)
        elif k == "hide":
            self.actors.pop(d["tag"], None)
            self.overlays.pop(d["tag"], None)
        elif k == "say":
            self._say(d, db)
        elif k == "play":
            if d["channel"] == "music":
                self.music = d["target"]
        elif k == "stop":
            if d["channel"] == "music":
                self.music = ""

    @staticmethod
    def _is_overlay(d: dict, db: ScriptDB) -> bool:
        """¿Este `show` es una capa (dark, white, vignette, CG...) y no un personaje?"""
        if d["positions"] or any(parse_position(n) for n in d["at"]):
            return False                               # lleva t11/h21... -> es un personaje
        chars = {c.image_tag for c in db.characters.values() if c.image_tag}
        if d["tag"] in chars:
            return False
        full = " ".join([d["tag"]] + list(d["attrs"]))
        return full in db.images or d["tag"] in db.images or d["tag"] in ("black", "white")

    def _show(self, d: dict, db: ScriptDB) -> None:
        tag = d["as"] or d["tag"]
        actor = self.actors.get(tag)
        if actor is None:
            if self._is_overlay(d, db):
                ov = self.overlays.get(tag)
                if ov is None:
                    self.seq += 1
                    ov = self.overlays[tag] = Overlay(tag=d["tag"], seq=self.seq)
                if d["attrs"]:
                    ov.attrs = tuple(d["attrs"])
                if d["zorder"] is not None:
                    ov.zorder = d["zorder"]
                ov.at = tuple(d["at"])
                return
            self.seq += 1
            actor = Actor(tag=d["tag"], seq=self.seq)
            self.actors[tag] = actor
        if d["attrs"]:
            actor.attrs = tuple(d["attrs"])
        if d["zorder"] is not None:
            actor.zorder = d["zorder"]
        extras = []
        for name in d["at"]:
            pos = parse_position(name)
            if pos is None:
                extras.append(name)
            elif pos.motion == "hide":
                actor.hiding = True
                actor.motion = "hide"
                actor.pos_name = pos.name
            else:
                actor.count, actor.slot = pos.count, pos.slot
                actor.motion, actor.pos_name = pos.motion, pos.name
                actor.place_name = pos.name
                actor.hiding = False
        if extras:
            actor.extra_transforms = tuple(extras)
            base = extras[0].split("(")[0].strip()
            if not actor.count and base in db.transform_defs:
                actor.pos_name = base              # transform propio definido en transforms.rpy: se dibuja ahí

    def _say(self, d: dict, db: ScriptDB) -> None:
        who = d["who"]
        if who and who != "extend":
            self.speaker_var = who
            self.speaker = d.get("who_name") or db.display_name(who)
        elif not who:
            self.speaker_var, self.speaker = "", ""
        self.text = d["plain"] if d.get("extend") is False else self.text + d["plain"]
        # "s 1a "texto"" cambia la pose del personaje si su Character tiene image='sayori'
        tag = db.image_tag_of(who) if who else ""
        if tag and d["attrs"] and tag in self.actors:
            self.actors[tag].attrs = tuple(d["attrs"])


@dataclass
class Frame:
    index: int
    event: Event                 # el diálogo
    stage: Stage                 # foto del escenario en este diálogo
    actions: List[Event]         # scene/show/hide/with/play... ocurridos desde el diálogo anterior

    def describe(self) -> str:
        s = self.stage
        cells = []
        for i, a in enumerate(s.cells(), 1):
            if a is None:
                cells.append(f"[{i}] ---")
            else:
                flag = f" ({a.pos_name}{'*' if a.motion not in ('', 'appear', 'instant') else ''})"
                cells.append(f"[{i}] {a.image}{flag}")
        extra = [a.image for a in s.unplaced()]
        head = f"bg={s.bg or '-'} | celdas={s.layout_count()}"
        body = "  ".join(cells) if cells else "(sin personajes)"
        if extra:
            body += f"  sin_posición: {', '.join(extra)}"
        who = s.speaker or "(narrador)"
        return f"{head}  {body}\n    {who}: {s.text}"


def build_timeline(db: ScriptDB, label: Optional[str] = None) -> List[Frame]:
    """Un Frame por cada línea de diálogo, en orden de archivo.

    label=None  -> todos los eventos de todos los archivos.
    label="x"   -> solo los eventos de ese label (hasta el siguiente label).
    (Seguir jump/call/menu/if llegará en pasos posteriores.)
    """
    if label is None:
        events = db.events
    else:
        a, b = db.label_range(label)
        events = db.events[a:b]
    stage = Stage()
    pending: List[Event] = []
    frames: List[Frame] = []
    for ev in events:
        stage.apply(ev, db)
        if ev.kind == "say":
            frames.append(Frame(len(frames), ev, copy.deepcopy(stage), list(pending)))
            pending = []
            stage.clear_motions()
        elif ev.kind in ("scene", "show", "hide", "with", "play", "stop", "pause", "window"):
            pending.append(ev)
    return frames
