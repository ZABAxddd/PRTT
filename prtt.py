"""
prtt.py - Punto de entrada de PRTT (DDLC Possing in Real Time Tool).

Instalación: copia la carpeta PRTT dentro de /game  ->  /game/PRTT
La herramienta lee automáticamente la carpeta /game (la carpeta que contiene a PRTT)
y se ignora a sí misma al leer.

Uso:
    python prtt.py                       abre la ventana del lector de diálogos
    python prtt.py --cli                 resumen en consola (sin ventana)
    python prtt.py --cli --file script-ch1.rpy      diálogos de un archivo
    python prtt.py --cli --label ch1_main           diálogos de un label
    python prtt.py --cli --buscar "texto"           buscar en todos los diálogos
    python prtt.py --escena ch1_main     estado de escena (fondo/celdas) por diálogo
    python prtt.py --catalogo [sayori]   poses y expresiones leídas de definitions.rpy
    python prtt.py --musica              pistas de música (título de comentario o variable) y qué las usa
    python prtt.py --game RUTA           leer otra carpeta en vez del /game padre
"""
import argparse
import os
import sys
from typing import Dict, List, Optional, Sequence

def _tool_dir() -> str:
    if not getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(__file__))
    executable_dir = os.path.dirname(os.path.abspath(sys.executable))
    if sys.platform == "darwin":
        app_bundle = os.path.dirname(os.path.dirname(executable_dir))
        if app_bundle.endswith(".app"):
            return os.path.dirname(app_bundle)
    return executable_dir


TOOL_DIR = _tool_dir()
sys.path.insert(0, TOOL_DIR)
from i18n import _t  # noqa: E402

import rpy_parser  # noqa: E402
from dialogue import LabelInfo, build_index, count_lines, format_line, search  # noqa: E402

ScriptIndex = Dict[str, List[LabelInfo]]


def default_game_dir() -> str:
    """/game/PRTT -> /game"""
    return os.path.dirname(TOOL_DIR)


def load_db(game_dir: str) -> rpy_parser.ScriptDB:
    return rpy_parser.load(game_dir, exclude_dirs=(TOOL_DIR,))


def cli_summary(db: rpy_parser.ScriptDB, index: ScriptIndex) -> None:
    n_labels = sum(len(v) for v in index.values())
    print(_t("Carpeta leída: {0} archivos .rpy · {1} labels con diálogo · {2} líneas de diálogo", len(db.files), n_labels, count_lines(index)))
    for file, infos in index.items():
        print(_t("\n{0}  ({1} líneas)", file, sum(len(i.lines) for i in infos)))
        for info in infos:
            print(_t("   {0:<32} {1:>5} líneas  (línea {2})", info.name, len(info.lines), info.line))
    if db.readonly:
        print(_t("Scripts leídos de .rpyc (solo lectura): {0}", len(db.readonly)))
    if db.errors:
        print(_t("\nAvisos al leer ({0}):", len(db.errors)))
        for e in db.errors[:20]:
            print("   ", e)


def cli_pose(db: rpy_parser.ScriptDB, game_dir: str, pose: str, png: Optional[str] = None) -> int:
    from sprites import SpriteBook, SpriteError, describe
    book = SpriteBook(db, game_dir)
    tokens = pose.split()
    name = book.find_name(tokens[0], tokens[1:]) if tokens else None
    if name is None:
        print(_t("No hay ninguna definición 'image {0}'.", pose), file=sys.stderr)
        similar = [n for n in db.images if n.split()[0] == tokens[0]][:15] if tokens else []
        if similar:
            print(_t("Poses definidas para ese personaje: ") + ", ".join(similar), file=sys.stderr)
        return 1
    d = book.get_def(name)
    if d is None:
        print(_t("No se pudo leer la definición 'image {0}'.", name), file=sys.stderr)
        return 1
    print(describe(d))
    for layer in d.layers:
        print(f"   ({layer.x}, {layer.y})  {layer.path}  ->  {layer.file or _t("NO ENCONTRADO")}")
    try:
        img, missing = book.compose(name)
    except SpriteError as exc:
        print(_t("No se pudo armar la pose: {0}", exc), file=sys.stderr)
        return 1
    print(_t("Pose armada: {0}x{1}", img.size[0], img.size[1]))
    if missing:
        print(_t("Capas que faltan: ") + ", ".join(missing))
    if png:
        img.save(png)
        print(_t("Guardada en {0}", png))
    return 0


def cli_catalog(db: rpy_parser.ScriptDB, game_dir: str, tag: str = "") -> int:
    """Qué poses/expresiones se leyeron de definitions.rpy y qué PNG faltan."""
    from sprites import SpriteBook
    import cells
    cat = cells.SpriteCatalog(db, SpriteBook(db, game_dir))
    defs = [f for f in db.files if f.replace("\\", "/").rsplit("/", 1)[-1] == "definitions.rpy"]
    print("definitions.rpy: " + (", ".join(defs) if defs else
                                 _t("no encontrado (las poses se leen de todos tus .rpy)")))
    tags = [tag] if tag else cat.tags()
    for t in tags:
        poses = cat.poses(t)
        if not poses:
            print(_t("\n{0}: sin poses definidas", t), file=sys.stderr)
            continue
        print(f"\n{t}: {len(poses)} poses")
        for p in poses:
            ex = cat.exprs(t, p)
            print(f"   {p:<10} {cells.outfit_label(p):<10} {len(ex):>3} expr: {' '.join(e or '-' for e in ex)}")
        miss = cat.missing(t)
        if miss:
            print(_t("   ! faltan {0} archivos PNG, p. ej.: {1}", len(miss), ', '.join(miss[:4])))
    return 0


def cli_diag(db: rpy_parser.ScriptDB, game_dir: str) -> int:
    """Qué se leyó de los .rpy, .rpyc y .rpa, y por qué algo pudo quedarse fuera."""
    import rpa_reader
    n_loose = len([f for f in db.files if f not in db.readonly])
    print(_t("Carpeta: {0}", game_dir))
    print(_t("Scripts .rpy sueltos: {0} · scripts compilados leídos (.rpyc/.rpa): {1}", n_loose, len(db.readonly)))
    vault = rpa_reader.get_vault(game_dir)
    print(_t(".rpa abiertos: {0} ({1} archivos dentro)", len(vault.archives), len(vault.names())))
    for arc in vault.archives:
        print(f"   {os.path.basename(arc.path)}  {arc.version}  {len(arc.index)}")
    print(_t("Definiciones: {0} personajes · {1} imágenes · {2} transforms · {3} define/default · {4} styles",
             len(db.characters), len(db.images), len(db.transform_defs), len(db.defines), len(db.styles)))
    print(_t("Labels: {0}", len(db.labels)))
    if db.readonly:
        print(_t("\nScripts compilados leídos:"))
        for rel in sorted(db.readonly):
            n_lab = sum(1 for e in db.events if e.file == rel and e.kind == "label")
            n_say = sum(1 for e in db.events if e.file == rel and e.kind == "say")
            print(f"   {rel:<40} {n_lab:>4} labels {n_say:>6} diálogos   <- {db.compiled_from.get(rel, '')}")
    if db.errors:
        print(_t("\nAvisos ({0}):", len(db.errors)))
        for e in db.errors:
            print("   ", e)
    else:
        print(_t("\nSin avisos."))
    return 0


def cli_transform(db: rpy_parser.ScriptDB, name: str) -> int:
    """Cómo quedó leído un transform (lo que usan las celdas): parámetros y cuerpo ATL."""
    td = db.transform_defs.get(name)
    if td is None:
        print(_t("No hay ningún transform '{0}'. Definidos: {1}", name, ", ".join(sorted(db.transform_defs)[:40]) or "-"),
              file=sys.stderr)
        return 1
    print(f"transform {td.name}({td.params}):")
    for rel, text in td.body:
        print("    " * (1 + rel // 4) + text)
    return 0


def cli_music(db: rpy_parser.ScriptDB, game_dir: str) -> int:
    """Pistas de música de los .rpy: nombre (comentario o variable), archivo, duración y dónde se usan."""
    import audio
    import rpa_reader
    tracks = audio.collect_tracks(db, game_dir)
    if not tracks:
        print(_t("No encontré pistas: define algo como  define audio.t1 = \"bgm/1.ogg\"  # Título"), file=sys.stderr)
        return 1
    used: Dict[str, List[str]] = {}
    for ev in db.events:
        if ev.kind == "play" and ev.data.get("channel") == "music":
            used.setdefault(ev.data["target"].strip("\"'"), []).append(ev.where)
    book = audio.TrackBook(tracks)
    uses: Dict[str, List[str]] = {}
    for key, places in used.items():
        t = book.find(key)
        if t is not None:
            uses.setdefault(t.ref, []).extend(places)
    print(_t("{0} pistas", len(tracks)))
    for t in tracks:
        audio.load_info(t)
        dur = audio.fmt_time(t.duration) if t.duration else "—"
        file = (t.file if rpa_reader.is_virtual(t.file) else os.path.relpath(t.file, game_dir)) if t.file else _t("NO ENCONTRADO")
        flag = _t("  (sin definir)") if t.kind == "missing" else ""
        loop = f"  loop {t.loop}" if t.loop is not None else ""
        print(f"{t.name:<36} {t.short:<10} {dur:>7}  {file}{loop}{flag}")
        for w in uses.get(t.ref, [])[:3]:
            print(_t("      usada en {0}", w))
    return 0


def cli_preview(db: rpy_parser.ScriptDB, game_dir: str, label: str, n: int,
                png: Optional[str] = None) -> int:
    from stage import build_timeline
    if label not in db.labels:
        print(_t("El label '{0}' no existe.", label), file=sys.stderr)
        return 1
    frames = build_timeline(db, label)
    if not 0 <= n < len(frames):
        print(_t("El label '{0}' tiene {1} diálogos (usa --n de 0 a {2}).", label, len(frames), len(frames) - 1),
              file=sys.stderr)
        return 1
    try:
        import preview
        pv = preview.Preview(db, game_dir)
    except (ImportError, RuntimeError) as exc:
        print(_t("No se pudo iniciar el preview: {0}", exc), file=sys.stderr)
        return 1
    img = pv.render(frames[n])
    out = png or f"preview_{label}_{n}.png"
    img.save(out)
    print(f"{frames[n].event.where}  ->  {out}")
    for w in pv.warnings:
        print("  ! " + w)
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=_t("PRTT: lector de diálogos y escenas de scripts .rpy"))
    ap.add_argument("--game", help=_t("carpeta /game a leer (por defecto, la carpeta que contiene a PRTT)"))
    ap.add_argument("--cli", action="store_true", help=_t("modo consola, sin ventana"))
    ap.add_argument("--file", help=_t("(con --cli) mostrar los diálogos de este archivo .rpy"))
    ap.add_argument("--label", help=_t("(con --cli) mostrar los diálogos de este label"))
    ap.add_argument("--buscar", help=_t("(con --cli) buscar texto en todos los diálogos"))
    ap.add_argument("--escena", metavar="LABEL", help=_t("mostrar fondo/celdas/personajes por diálogo"))
    ap.add_argument("--pose", metavar='"sayori 1a"', help=_t("mostrar las capas de una pose y comprobar sus archivos"))
    ap.add_argument("--png", metavar=_t("ARCHIVO"), help=_t("(con --pose o --preview) guardar la imagen como PNG"))
    ap.add_argument("--preview", metavar="LABEL", help=_t("dibujar un diálogo como se vería en el juego (1280x720)"))
    ap.add_argument("--n", type=int, default=0, help=_t("(con --preview) nº de diálogo dentro del label, desde 0"))
    ap.add_argument("--catalogo", nargs="?", const="", metavar=_t("PERSONAJE"),
                    help=_t("poses y expresiones leídas de definitions.rpy (opcional: solo un personaje)"))
    ap.add_argument("--musica", action="store_true", help=_t("listar las pistas de música (comentario # Título o variable)"))
    ap.add_argument("--diag", action="store_true", help=_t("diagnóstico: qué se leyó de .rpy/.rpyc/.rpa y qué avisos hubo"))
    ap.add_argument("--transform", metavar="t11", help=_t("mostrar cómo se leyó un transform (parámetros y cuerpo ATL)"))
    ap.add_argument("--conf", action="store_true", help=_t("mostrar qué ajustes de la caja de texto se leyeron de tus .rpy"))
    args = ap.parse_args(argv)

    game_dir = os.path.abspath(args.game) if args.game else default_game_dir()
    try:
        db = load_db(game_dir)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 1

    if not db.files:
        print(_t("No encontré ningún script (.rpy ni .rpyc) en: {0}\n· PRTT debe estar dentro de /game  (por ejemplo /game/PRTT).\n· Los scripts pueden estar sueltos o dentro de un .rpa: PRTT lee los .rpy y, si no los hay, los .rpyc (solo lectura).", game_dir), file=sys.stderr)
        return 1

    index = build_index(db)

    if args.pose:
        return cli_pose(db, game_dir, args.pose, args.png)

    if args.catalogo is not None:
        return cli_catalog(db, game_dir, args.catalogo)

    if args.musica:
        return cli_music(db, game_dir)

    if args.conf:
        from guiconf import load_gui
        print("\n".join(load_gui(db).describe()))
        return 0

    if args.preview:
        return cli_preview(db, game_dir, args.preview, args.n, args.png)

    if args.escena:
        from stage import build_timeline
        if args.escena not in db.labels:
            print(_t("El label '{0}' no existe.", args.escena), file=sys.stderr)
            return 1
        for fr in build_timeline(db, args.escena):
            print(f"#{fr.index:03d} {fr.event.where}")
            for act in fr.actions:
                print(f"      · {act.raw}")
            print("    " + fr.describe())
        return 0

    if args.buscar:
        found = search(index, args.buscar)
        print(_t("{0} resultados", len(found)))
        for dl in found:
            print(f"{dl.where}  [{dl.label}]  {dl.speaker}: {dl.text}")
        return 0

    if args.file or args.label:
        shown = False
        for file, infos in index.items():
            for info in infos:
                if (args.file and file != args.file.replace("\\", "/")) or \
                   (args.label and info.name != args.label):
                    continue
                print(f"== {info.name}  ({file}:{info.line}) ==")
                for dl in info.lines:
                    print(format_line(dl))
                shown = True
        if not shown:
            print(_t("No se encontró ese archivo/label con diálogo."), file=sys.stderr)
            return 1
        return 0

    if args.cli:
        cli_summary(db, index)
        return 0

    try:
        import tkinter  # noqa: F401
        _ = tkinter
    except ImportError:
        print(_t("Tkinter no está disponible en este Python; muestro el resumen en consola.\n(En Windows, reinstala Python marcando 'tcl/tk and IDLE'.)\n"))
        cli_summary(db, index)
        return 0

    import prtt_gui
    prtt_gui.run(db, game_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
