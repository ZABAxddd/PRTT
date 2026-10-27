"""
history.py - Deshacer / rehacer y control de "cambios sin guardar" para las celdas editadas.

El estado editable es un dict {clave_de_diálogo: cells.CellLayout}. Cada acción guarda una
"foto" del dict ANTES de aplicarse; deshacer/rehacer intercambian fotos. Además se recuerda la
foto de lo que ya está escrito en los .rpy (`saved`): hay cambios pendientes mientras el estado
actual sea distinto de esa foto, así que deshacer hasta el punto guardado deja de contar como
"sin guardar", y deshacer DESPUÉS de guardar sí cuenta (el guardado siguiente quitará/ajustará
el bloque del script).

No depende de Tkinter. Los valores del dict solo necesitan `.copy()` y `==`.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

Edits = Dict[object, object]


class EditHistory:
    def __init__(self, limit: int = 300) -> None:
        self.limit = limit
        self._undo: List[Tuple[str, Edits]] = []
        self._redo: List[Tuple[str, Edits]] = []
        self.saved: Edits = {}

    # ------------------------------------------------------------------ fotos
    @staticmethod
    def snapshot(edits: Edits) -> Edits:
        return {k: v.copy() for k, v in edits.items()}

    # ------------------------------------------------------------------ historial
    def record(self, before: Edits, after: Edits, desc: str) -> bool:
        """Anota una acción ya aplicada. `before` es la foto previa. False si no cambió nada."""
        if before == after:
            return False
        self._undo.append((desc, before))
        if len(self._undo) > self.limit:
            del self._undo[0]
        self._redo.clear()
        return True

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self, current: Edits) -> Optional[Tuple[str, Edits]]:
        """(descripción, estado a restaurar) o None. El estado devuelto pasa a ser del llamador."""
        if not self._undo:
            return None
        desc, before = self._undo.pop()
        self._redo.append((desc, self.snapshot(current)))
        return desc, before

    def redo(self, current: Edits) -> Optional[Tuple[str, Edits]]:
        if not self._redo:
            return None
        desc, after = self._redo.pop()
        self._undo.append((desc, self.snapshot(current)))
        return desc, after

    # ------------------------------------------------------------------ guardado
    def dirty(self, edits: Edits) -> bool:
        return edits != self.saved

    def pending(self, edits: Edits) -> int:
        """Cuántos diálogos difieren de lo escrito en los scripts."""
        return sum(1 for k in set(edits) | set(self.saved) if edits.get(k) != self.saved.get(k))

    def mark_saved(self, key: object, layout) -> None:
        """Ese diálogo ya está escrito en el script tal como está en `layout` (None = sin bloque)."""
        if layout is None:
            self.saved.pop(key, None)
        else:
            self.saved[key] = layout.copy()
