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
    print(f"Carpeta leída: {len(db.files)} archivos .rpy · {n_labels} labels con diálogo · "
          f"{count_lines(index)} líneas de diálogo")
    for file, infos in index.items():
        print(f"\n{file}  ({sum(len(i.lines) for i in infos)} líneas)")
        for info in infos:
            print(f"   {info.name:<32} {len(info.lines):>5} líneas  (línea {info.line})")
    if db.errors:
        print(f"\nAvisos al leer ({len(db.errors)}):")
        for e in db.errors[:20]:
            print("   ", e)


def cli_pose(db: rpy_parser.ScriptDB, game_dir: str, pose: str, png: Optional[str] = None) -> int:
    from sprites import SpriteBook, SpriteError, describe
    book = SpriteBook(db, game_dir)
    tokens = pose.split()
    name = book.find_name(tokens[0], tokens[1:]) if tokens else None
    if name is None:
        print(f"No hay ninguna definición 'image {pose}'.", file=sys.stderr)
        similar = [n for n in db.images if n.split()[0] == tokens[0]][:15] if tokens else []
        if similar:
            print("Poses definidas para ese personaje: " + ", ".join(similar), file=sys.stderr)
        return 1
    d = book.get_def(name)
    if d is None:
        print(f"No se pudo leer la definición 'image {name}'.", file=sys.stderr)
        return 1
    print(describe(d))
    for layer in d.layers:
        print(f"   ({layer.x}, {layer.y})  {layer.path}  ->  {layer.file or 'NO ENCONTRADO'}")
    try:
        img, missing = book.compose(name)
    except SpriteError as exc:
        print(f"No se pudo armar la pose: {exc}", file=sys.stderr)
        return 1
    print(f"Pose armada: {img.size[0]}x{img.size[1]}")
    if missing:
        print("Capas que faltan: " + ", ".join(missing))
    if png:
        img.save(png)
        print(f"Guardada en {png}")
    return 0


def cli_catalog(db: rpy_parser.ScriptDB, game_dir: str, tag: str = "") -> int:
    """Qué poses/expresiones se leyeron de definitions.rpy y qué PNG faltan."""
    from sprites import SpriteBook
    import cells
    cat = cells.SpriteCatalog(db, SpriteBook(db, game_dir))
    defs = [f for f in db.files if f.replace("\\", "/").rsplit("/", 1)[-1] == "definitions.rpy"]
    print("definitions.rpy: " + (", ".join(defs) if defs else
                                 "no encontrado (las poses se leen de todos tus .rpy)"))
    tags = [tag] if tag else cat.tags()
    for t in tags:
        poses = cat.poses(t)
        if not poses:
            print(f"\n{t}: sin poses definidas", file=sys.stderr)
            continue
        print(f"\n{t}: {len(poses)} poses")
        for p in poses:
            ex = cat.exprs(t, p)
            print(f"   {p:<10} {cells.outfit_label(p):<10} {len(ex):>3} expr: {' '.join(e or '-' for e in ex)}")
        miss = cat.missing(t)
        if miss:
            print(f"   ! faltan {len(miss)} archivos PNG, p. ej.: {', '.join(miss[:4])}")
    return 0


def cli_music(db: rpy_parser.ScriptDB, game_dir: str) -> int:
    """Pistas de música de los .rpy: nombre (comentario o variable), archivo, duración y dónde se usan."""
    import audio
    tracks = audio.collect_tracks(db, game_dir)
    if not tracks:
        print("No encontré pistas: define algo como  define audio.t1 = \"bgm/1.ogg\"  # Título", file=sys.stderr)
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
    print(f"{len(tracks)} pistas")
    for t in tracks:
        audio.load_info(t)
        dur = audio.fmt_time(t.duration) if t.duration else "—"
        file = os.path.relpath(t.file, game_dir) if t.file else "NO ENCONTRADO"
        flag = "  (sin definir)" if t.kind == "missing" else ""
        loop = f"  loop {t.loop}" if t.loop is not None else ""
        print(f"{t.name:<36} {t.short:<10} {dur:>7}  {file}{loop}{flag}")
        for w in uses.get(t.ref, [])[:3]:
            print(f"      usada en {w}")
    return 0


def cli_preview(db: rpy_parser.ScriptDB, game_dir: str, label: str, n: int,
                png: Optional[str] = None) -> int:
    from stage import build_timeline
    if label not in db.labels:
        print(f"El label '{label}' no existe.", file=sys.stderr)
        return 1
    frames = build_timeline(db, label)
    if not 0 <= n < len(frames):
        print(f"El label '{label}' tiene {len(frames)} diálogos (usa --n de 0 a {len(frames) - 1}).",
              file=sys.stderr)
        return 1
    try:
        import preview
        pv = preview.Preview(db, game_dir)
    except (ImportError, RuntimeError) as exc:
        print(f"No se pudo iniciar el preview: {exc}", file=sys.stderr)
        return 1
    img = pv.render(frames[n])
    out = png or f"preview_{label}_{n}.png"
    img.save(out)
    print(f"{frames[n].event.where}  ->  {out}")
    for w in pv.warnings:
        print("  ! " + w)
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="PRTT: lector de diálogos y escenas de scripts .rpy")
    ap.add_argument("--game", help="carpeta /game a leer (por defecto, la carpeta que contiene a PRTT)")
    ap.add_argument("--cli", action="store_true", help="modo consola, sin ventana")
    ap.add_argument("--file", help="(con --cli) mostrar los diálogos de este archivo .rpy")
    ap.add_argument("--label", help="(con --cli) mostrar los diálogos de este label")
    ap.add_argument("--buscar", help="(con --cli) buscar texto en todos los diálogos")
    ap.add_argument("--escena", metavar="LABEL", help="mostrar fondo/celdas/personajes por diálogo")
    ap.add_argument("--pose", metavar='"sayori 1a"', help="mostrar las capas de una pose y comprobar sus archivos")
    ap.add_argument("--png", metavar="ARCHIVO", help="(con --pose o --preview) guardar la imagen como PNG")
    ap.add_argument("--preview", metavar="LABEL", help="dibujar un diálogo como se vería en el juego (1280x720)")
    ap.add_argument("--n", type=int, default=0, help="(con --preview) nº de diálogo dentro del label, desde 0")
    ap.add_argument("--catalogo", nargs="?", const="", metavar="PERSONAJE",
                    help="poses y expresiones leídas de definitions.rpy (opcional: solo un personaje)")
    ap.add_argument("--musica", action="store_true", help="listar las pistas de música (comentario # Título o variable)")
    ap.add_argument("--conf", action="store_true", help="mostrar qué ajustes de la caja de texto se leyeron de tus .rpy")
    args = ap.parse_args(argv)

    game_dir = os.path.abspath(args.game) if args.game else default_game_dir()
    try:
        db = load_db(game_dir)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 1

    if not db.files:
        print(f"No encontré ningún archivo .rpy en: {game_dir}\n"
              "· PRTT debe estar dentro de /game  (por ejemplo /game/PRTT).\n"
              "· Si los scripts del juego siguen dentro de scripts.rpa, hay que extraerlos primero:\n"
              "  PRTT solo lee archivos .rpy.", file=sys.stderr)
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
            print(f"El label '{args.escena}' no existe.", file=sys.stderr)
            return 1
        for fr in build_timeline(db, args.escena):
            print(f"#{fr.index:03d} {fr.event.where}")
            for act in fr.actions:
                print(f"      · {act.raw}")
            print("    " + fr.describe())
        return 0

    if args.buscar:
        found = search(index, args.buscar)
        print(f"{len(found)} resultados")
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
            print("No se encontró ese archivo/label con diálogo.", file=sys.stderr)
            return 1
        return 0

    if args.cli:
        cli_summary(db, index)
        return 0

    try:
        import tkinter  # noqa: F401
        _ = tkinter
    except ImportError:
        print("Tkinter no está disponible en este Python; muestro el resumen en consola.\n"
              "(En Windows, reinstala Python marcando 'tcl/tk and IDLE'.)\n")
        cli_summary(db, index)
        return 0

    import prtt_gui
    prtt_gui.run(db, game_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
