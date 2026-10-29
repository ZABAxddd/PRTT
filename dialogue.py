"""
dialogue.py - Índice, búsqueda y edición lógica de líneas de diálogo.

No depende de Tkinter, así que se puede probar sin ventana.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Dict, Iterable, List, NamedTuple

from rpy_parser import TOKEN_RE, WORD_RE, ScriptDB, unquote


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


IDENT_RE = re.compile(r"^[A-Za-z_][\w.]*$")
RESERVED = {"with", "as", "at", "behind", "onlayer", "zorder", "if", "elif", "else", "menu", "label",
            "jump", "call", "show", "hide", "scene", "play", "stop", "define", "default", "python",
            "return", "pass", "init"}


@dataclass
class SayParts:
    speaker: str = ""
    temp: bool = False
    pose: str = ""
    text: str = ""
    tail: str = ""
    raw_text: str = ""
    orig_text: str = ""


def encode_text(text: str) -> str:
    """Literal de Ren'Py entre comillas dobles."""
    s = text.replace("\\", "\\\\").replace('"', '\\"').replace("\r\n", "\n").replace("\r", "\n")
    return '"' + s.replace("\n", "\\n").replace("\t", "\\t") + '"'


def encode_name(name: str) -> str:
    return encode_text(" ".join(name.split()))


def _is_quote(tok: str) -> bool:
    return bool(tok) and tok[0] in "\"'"


def parse_say(code: str) -> "SayParts | None":
    """Descompone una línea de diálogo; None si tiene una forma especial."""
    code = code.strip()
    if not code or code.endswith(":") or '"""' in code or "'''" in code:
        return None
    toks = list(TOKEN_RE.finditer(code))
    k = next((i for i, m in enumerate(toks) if _is_quote(m.group())), None)
    if k is None:
        return None
    p = SayParts()
    if k == 0:
        if len(toks) > 1 and _is_quote(toks[1].group()):
            p.speaker, p.temp = unquote(toks[0].group()), True
            k = 1
    else:
        head = [m.group() for m in toks[:k]]
        if not all(WORD_RE.match(t) for t in head):
            return None
        p.speaker, p.pose = head[0], " ".join(head[1:])
    tok = toks[k]
    if tok.group().startswith(('"""', "'''")):
        return None
    p.raw_text = tok.group()
    p.text = p.orig_text = unquote(p.raw_text)
    p.tail = code[tok.end():].strip()
    return p


def build_say(p: SayParts) -> str:
    """Reconstruye la línea del script a partir de sus campos."""
    if p.temp:
        head: List[str] = [encode_name(p.speaker)]
    else:
        head = [p.speaker] + p.pose.split() if p.speaker else []
    text = p.raw_text if (p.raw_text and p.text == p.orig_text) else encode_text(p.text)
    out = " ".join(head + [text])
    return out + (" " + p.tail if p.tail else "")


def same(a: SayParts, b: SayParts) -> bool:
    return (a.speaker == b.speaker and a.temp == b.temp and a.pose.split() == b.pose.split()
            and a.text == b.text and a.tail == b.tail)


def validate(p: SayParts, characters: Iterable[str]) -> str:
    """Devuelve un mensaje de error, o una cadena vacía si se puede escribir."""
    chars = set(characters)
    if not p.text.strip():
        return "Escribe el texto del diálogo."
    speaker = p.speaker.strip()
    if p.temp:
        if not speaker:
            return "Escribe el nombre temporal del hablante."
        if p.pose.strip():
            return "Un nombre temporal no admite pose: déjala en blanco."
        return ""
    if not speaker:
        return "El narrador no admite pose: déjala en blanco." if p.pose.strip() else ""
    if not IDENT_RE.match(speaker):
        return (f"«{speaker}» no es el nombre de un personaje del script. "
                "Si es un nombre suelto, marca «nombre temporal».")
    if speaker not in chars and speaker != "extend":
        return (f"«{speaker}» no está definido como Character en tus .rpy. "
                "Elígelo de la lista o marca «nombre temporal».")
    for tok in p.pose.split():
        if not WORD_RE.match(tok) or tok in RESERVED:
            return f"«{tok}» no vale como pose o expresión."
    return ""


class Choice(NamedTuple):
    kind: str
    value: str
    label: str


NARRATOR_LABEL = "(narrador)"
NEW_TEMP_LABEL = "＋ Nombre temporal…"


def speaker_label(db, var: str) -> str:
    name = db.display_name(var)
    return var if name == var else f"{name} ({var})"


def speaker_choices(db) -> List[Choice]:
    """Devuelve el narrador y los `Character` definidos."""
    return [Choice("narr", "", NARRATOR_LABEL)] + [
        Choice("var", var, speaker_label(db, var)) for var in db.characters]


def temp_names(db) -> List[str]:
    """Hablantes sin `Character`, en orden de aparición."""
    seen: List[str] = []
    for ev in db.events:
        if ev.kind == "say":
            who = ev.data["who"]
            if who and who != "extend" and who not in db.characters and who not in seen:
                seen.append(who)
    return seen


def quick_choices(db) -> List[Choice]:
    """Opciones para el selector rápido de hablante del preview."""
    out = speaker_choices(db)
    out += [Choice("temp", n, f"“{n}” (temporal)") for n in temp_names(db)]
    out.append(Choice("new", "", NEW_TEMP_LABEL))
    return out


def with_speaker(p: SayParts, kind: str, value: str) -> SayParts:
    """Cambia el hablante y elimina la pose anterior cuando corresponde."""
    if kind == "temp":
        return replace(p, speaker=value, temp=True, pose="")
    if kind == "narr":
        return replace(p, speaker="", temp=False, pose="")
    return replace(p, speaker=value, temp=False, pose="")
