"""
dialogue.py - Índice de diálogos: archivo -> label -> líneas de diálogo.

Es la base de la primera pantalla de PRTT: leer los scripts de diálogo de /game y poder
recorrerlos. No depende de Tkinter, así que se puede probar sin ventana.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from rpy_parser import ScriptDB


@dataclass
class DialogueLine:
    n: int            # nº dentro del label (1, 2, 3...)
    file: str
    line: int
    label: str
    who_var: str      # variable del script: s, m, y, n, mc, "" (narrador)
    who: str          # nombre para mostrar: Sayori, Monika...
    attrs: List[str]  # pose/expresión pedida en la línea: ["1a"]
    text: str         # texto con etiquetas de estilo quitadas
    raw_text: str     # texto original

    @property
    def where(self) -> str:
        return f"{self.file}:{self.line}"

    @property
    def speaker(self) -> str:
        return self.who or "(narrador)"


@dataclass
class LabelInfo:
    name: str
    file: str
    line: int
    lines: List[DialogueLine] = field(default_factory=list)


def build_index(db: ScriptDB) -> Dict[str, List[LabelInfo]]:
    """{archivo: [LabelInfo, ...]} solo con los labels que tienen diálogo."""
    labels: Dict[str, LabelInfo] = {}
    order: List[str] = []
    for ev in db.events:
        if ev.kind == "label":
            key = ev.data["name"]
            if key not in labels:
                labels[key] = LabelInfo(key, ev.file, ev.line)
                order.append(key)
        elif ev.kind == "say" and ev.label in labels:
            info = labels[ev.label]
            d = ev.data
            info.lines.append(DialogueLine(
                n=len(info.lines) + 1, file=ev.file, line=ev.line, label=ev.label,
                who_var=d["who"], who=d["who_name"] or d["who"], attrs=list(d["attrs"]),
                text=d["plain"], raw_text=d["text"],
            ))
    out: Dict[str, List[LabelInfo]] = {}
    for key in order:
        info = labels[key]
        if info.lines:
            out.setdefault(info.file, []).append(info)
    return out


def count_lines(index: Dict[str, List[LabelInfo]]) -> int:
    return sum(len(i.lines) for infos in index.values() for i in infos)


def search(index: Dict[str, List[LabelInfo]], query: str, limit: int = 5000) -> List[DialogueLine]:
    """Busca (sin distinguir mayúsculas) en el texto y en el nombre del hablante."""
    q = query.strip().lower()
    if not q:
        return []
    found: List[DialogueLine] = []
    for infos in index.values():
        for info in infos:
            for dl in info.lines:
                if q in dl.text.lower() or q in dl.who.lower():
                    found.append(dl)
                    if len(found) >= limit:
                        return found
    return found


def format_line(dl: DialogueLine) -> str:
    pose = f" [{' '.join(dl.attrs)}]" if dl.attrs else ""
    return f"{dl.n:>4}  {dl.speaker}{pose}: {dl.text}"
