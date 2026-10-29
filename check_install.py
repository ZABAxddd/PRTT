"""check_install.py - Comprueba que la carpeta PRTT tiene todos los archivos que necesita (sin abrir ventanas).

    py -3 check_install.py

Sigue los `import` desde prtt.py y prtt_gui.py y avisa de los archivos .py que faltan en esta carpeta.
"""
import ast
import importlib.util
import os
import sys
try:
    from i18n import _t
except ImportError:                      # sin i18n.py: todo en español (y esta revisión avisará de que falta)
    def _t(text, *args):
        return text.format(*args) if args else text

HERE = os.path.dirname(os.path.abspath(__file__))
START = ("prtt.py", "prtt_gui.py")
OPTIONAL = {"PIL": _t("Pillow (py -3 -m pip install pillow): sin él no hay sprites ni previews"),
            "pygame": _t("pygame-ce (python -m pip install -r requirements-audio.txt): sin él no se puede escuchar la música en la ventana «Música»")}
IGNORED = {"numpy", "soundfile", "mutagen"}      # opcionales de audio.py (forma de onda / duración exacta): sin aviso


def imports_of(path):
    tree = ast.parse(open(path, encoding="utf-8-sig").read())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def main() -> int:
    seen, todo, missing, optional = set(), [s for s in START if os.path.isfile(os.path.join(HERE, s))], {}, set()
    for s in START:
        if not os.path.isfile(os.path.join(HERE, s)):
            missing[s[:-3]] = _t("(punto de entrada)")
    while todo:
        f = todo.pop()
        if f in seen:
            continue
        seen.add(f)
        for name in imports_of(os.path.join(HERE, f)):
            local = os.path.join(HERE, name + ".py")
            if os.path.isfile(local):
                todo.append(name + ".py")
            elif name in IGNORED:
                continue
            elif name in OPTIONAL and importlib.util.find_spec(name) is None:
                optional.add(name)
            elif name != "tkinter" and importlib.util.find_spec(name) is None:      # Tkinter se avisa aparte
                missing.setdefault(name, f)
    print(_t("Archivos de PRTT revisados: {0}", len(seen)))
    for name, who in sorted(missing.items()):
        print(_t("  FALTA  {0}.py   (lo importa {1})", name, who))
    for name in sorted(optional):
        print(_t("  AVISO  {0} no está instalado: {1}", name, OPTIONAL[name]))
    if not os.path.isdir(os.path.join(HERE, "lang")):
        print("  AVISO  falta la carpeta lang: PRTT funcionará solo en español (copia la carpeta lang completa).")
    try:
        import tkinter  # noqa: F401
    except ImportError:
        print(_t("  FALTA  Tkinter: reinstala Python marcando «tcl/tk and IDLE»."))
        return 1
    print(_t("TODO BIEN: la carpeta está completa.") if not missing else
          _t("Copia los {0} archivo(s) que faltan a esta carpeta y vuelve a ejecutar esto.", len(missing)))
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
