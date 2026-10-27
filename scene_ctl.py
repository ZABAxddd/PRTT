"""
scene_ctl.py - Une la detección de fondos/transiciones y la escritura de `scene`: lo que usa la ventana del
selector de escenarios. Sin Tkinter: se prueba con archivos reales.

    ctx = build_context(db, frame, find_image)       qué escena manda aquí, fondos y transiciones disponibles
    ctl = SceneController(game_dir)
    ok, msg, full = ctl.apply(db, frame, "replace" | "add" | "delete", bg, effect, reshow, mtimes)
    ctl.undo()

effect: scene_edit.KEEP (conservar la actual) · None (sin transición) · nombre de la transición ("dissolve").
`full` = True si se escribió una definición nueva (hay que releer TODO el proyecto, no solo los números de línea).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import scene_edit as se
import scenes
import transitions
from scene_edit import KEEP, SceneError, SceneRef, SceneWriter


@dataclass
class Context:
    where: str                                   # "label · archivo:línea" del diálogo elegido
    current: Optional[SceneRef]
    previous: Optional[SceneRef]
    backgrounds: List[scenes.Background]
    transitions: List[transitions.Spec]
    notes: List[str] = field(default_factory=list)

    @property
    def current_bg(self) -> str:
        return self.current.name if self.current else ""

    @property
    def previous_bg(self) -> str:
        return self.previous.name if self.previous else ""


def build_context(db, frame, find_image: Optional[Callable[[str], object]] = None,
                  include_all: bool = False) -> Context:
    cur, prev = se.find_scenes(db, frame)
    notes = []
    if cur is None:
        notes.append("Este diálogo no tiene ninguna instrucción `scene` antes dentro de su label: "
                     "solo se puede AGREGAR una.")
    return Context(f"{frame.event.label} · {frame.event.where}", cur, prev,
                   scenes.collect_backgrounds(db, include_all), transitions.collect_transitions(db, find_image), notes)


class SceneController:
    def __init__(self, game_dir: str) -> None:
        self.writer = SceneWriter(game_dir)

    def can_undo(self) -> bool:
        return self.writer.can_undo()

    def undo(self) -> Tuple[bool, str]:
        return self.writer.undo()

    def apply(self, db, frame, op: str, bg: Optional[scenes.Background], effect=KEEP, reshow: bool = True,
              mtimes: Optional[Dict[str, int]] = None) -> Tuple[bool, str, bool]:
        mtimes = dict(mtimes or {})
        cur, prev = se.find_scenes(db, frame)
        steps: List[se.Step] = []
        touched: set = set()

        def step(rel: str, fn) -> None:
            steps.append((rel, fn, None if rel in touched else mtimes.get(rel)))
            touched.add(rel)

        try:
            if effect is not KEEP and effect is not None and not isinstance(effect, str):
                raise SceneError("transición no válida")
            if op in ("replace", "delete") and cur is None:
                raise SceneError("No hay ninguna instrucción `scene` que mande en este diálogo.")
            if op in ("replace", "add") and bg is None:
                raise SceneError("Elige un fondo.")
            if op == "replace":
                step(cur.file, lambda L, e: se.replace_scene(L, cur, bg.name, effect))
            elif op == "add":
                eff = (cur.transition if cur else None) if effect is KEEP else effect
                where = se.first_action_line(frame) or frame.event.line
                lines = se.reshow_lines(frame) if reshow else []
                step(frame.event.file, lambda L, e: se.add_scene(L, where, bg.name, eff, lines, e))
            elif op == "delete":
                step(cur.file, lambda L, e: se.delete_scene(L, cur))
            else:
                raise SceneError(f"operación desconocida: {op}")
            full = False
            if op != "delete" and bg.new:                               # color nuevo: se define con los otros fondos
                site, _line = se.definition_site(db)
                if not site:
                    raise SceneError("No encuentro ningún `image` en tus .rpy donde escribir la definición del color.")
                text = scenes.definition_line(bg)
                step(site, lambda L, e: se.add_bg_definition(L, text, e))
                full = True
        except SceneError as exc:
            return False, str(exc), False
        ok, msg = self.writer.apply(steps)
        if not ok:
            return False, msg, False
        if op == "add" and any(e.kind == "scene" and not e.data.get("onlayer") for e in frame.actions):
            msg += (" Ojo: este diálogo ya tenía un `scene` justo antes; el anterior solo se verá un instante. "
                    "Si lo que querías es cambiar su fondo, usa «Reemplazar».")
        if op == "delete":
            back = f" Vuelve a mandar «{prev.name}» ({prev.file}:{prev.line})." if prev else \
                " No queda ninguna escena anterior en este label."
            msg += back
        return True, msg, full
