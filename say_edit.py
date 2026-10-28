"""
say_edit.py - Descompone y reconstruye UNA línea de diálogo para el editor por campos.

Una línea de diálogo de Ren'Py tiene esta forma:

    personaje [pose ...] "texto" [with ...]        s 1a "Hola"
    "texto"                                         (narrador)
    "Nombre temporal" "texto"                       (hablante no definido: sin Character ni pose)

parse_say() la separa en SayParts (hablante, pose, texto...) y build_say() la vuelve a escribir.
El texto se enseña DECODIFICADO (saltos de línea y comillas reales) y se vuelve a codificar al
escribir; si no lo tocas se conserva el literal original, así que una edición de solo el hablante
no reescribe el texto.

No depende de Tkinter.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Iterable, List, NamedTuple

from rpy_parser import TOKEN_RE, WORD_RE, unquote

IDENT_RE = re.compile(r"^[A-Za-z_][\w.]*$")
RESERVED = {"with", "as", "at", "behind", "onlayer", "zorder", "if", "elif", "else", "menu", "label",
            "jump", "call", "show", "hide", "scene", "play", "stop", "define", "default", "python",
            "return", "pass", "init"}


@dataclass
class SayParts:
    speaker: str = ""        # variable del script (s, m...) o, si temp, el nombre temporal
    temp: bool = False       # True: "Nombre" "texto" (hablante no definido)
    pose: str = ""           # "1a" (atributos separados por espacios); vacío = no cambia la pose
    text: str = ""           # texto decodificado
    tail: str = ""           # lo que sigue al texto: with dissolve, (what_style=...)
    raw_text: str = ""       # literal original del texto, con sus comillas ("" si la línea es nueva)
    orig_text: str = ""      # texto decodificado original (para saber si se tocó)


# --------------------------------------------------------------------------------------
# Codificar / decodificar
# --------------------------------------------------------------------------------------
def encode_text(text: str) -> str:
    """Literal de Ren'Py entre comillas dobles."""
    s = text.replace("\\", "\\\\").replace('"', '\\"').replace("\r\n", "\n").replace("\r", "\n")
    return '"' + s.replace("\n", "\\n").replace("\t", "\\t") + '"'


def encode_name(name: str) -> str:
    return encode_text(" ".join(name.split()))          # un nombre no lleva saltos de línea


def _is_quote(tok: str) -> bool:
    return bool(tok) and tok[0] in "\"'"


def parse_say(code: str) -> "SayParts | None":
    """Descompone la línea. None si no tiene la forma esperada (menú, bloque, varias líneas...)."""
    code = code.strip()
    if not code or code.endswith(":") or '"""' in code or "'''" in code:
        return None                                  # vacío, menú/bloque o texto con comillas triples
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
    """Línea del script para estas partes."""
    if p.temp:
        head: List[str] = [encode_name(p.speaker)]
    else:
        head = [p.speaker] + p.pose.split() if p.speaker else []      # el narrador no lleva pose
    text = p.raw_text if (p.raw_text and p.text == p.orig_text) else encode_text(p.text)
    out = " ".join(head + [text])
    return out + (" " + p.tail if p.tail else "")


def same(a: SayParts, b: SayParts) -> bool:
    """¿Dicen lo mismo? (ignora espacios de más entre palabras de la cabecera)."""
    return (a.speaker == b.speaker and a.temp == b.temp and a.pose.split() == b.pose.split()
            and a.text == b.text and a.tail == b.tail)


# --------------------------------------------------------------------------------------
# Validación
# --------------------------------------------------------------------------------------
def validate(p: SayParts, characters: Iterable[str]) -> str:
    """Mensaje de error, o "" si se puede escribir."""
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


# --------------------------------------------------------------------------------------
# Listas de hablantes (para selectores)
# --------------------------------------------------------------------------------------
class Choice(NamedTuple):
    kind: str      # "narr" | "var" | "temp" | "new"
    value: str     # variable, nombre temporal o ""
    label: str     # lo que se muestra


NARRATOR_LABEL = "(narrador)"
NEW_TEMP_LABEL = "＋ Nombre temporal…"


def speaker_label(db, var: str) -> str:
    name = db.display_name(var)
    return var if name == var else f"{name} ({var})"


def speaker_choices(db) -> List[Choice]:
    """(narrador) y los Character definidos."""
    return [Choice("narr", "", NARRATOR_LABEL)] + [
        Choice("var", var, speaker_label(db, var)) for var in db.characters]


def temp_names(db) -> List[str]:
    """Hablantes que el script usa sin tener Character (p. ej. \"???\"), en orden de aparición."""
    seen: List[str] = []
    for ev in db.events:
        if ev.kind == "say":
            who = ev.data["who"]
            if who and who != "extend" and who not in db.characters and who not in seen:
                seen.append(who)
    return seen


def quick_choices(db) -> List[Choice]:
    """Para el desplegable del panel Textbox: narrador, personajes, temporales ya usados y «nuevo»."""
    out = speaker_choices(db)
    out += [Choice("temp", n, f"“{n}” (temporal)") for n in temp_names(db)]
    out.append(Choice("new", "", NEW_TEMP_LABEL))
    return out


def with_speaker(p: SayParts, kind: str, value: str) -> SayParts:
    """Mismas partes pero con otro hablante. La pose se vacía: era de otro personaje."""
    if kind == "temp":
        return replace(p, speaker=value, temp=True, pose="")
    if kind == "narr":
        return replace(p, speaker="", temp=False, pose="")
    return replace(p, speaker=value, temp=False, pose="")
