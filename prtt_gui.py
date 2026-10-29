"""
prtt_gui.py - Ventana del lector de diálogos (Tkinter, viene con Python en Windows).

Izquierda: archivos .rpy y sus labels (con nº de líneas de diálogo).
Centro:    las líneas de diálogo del archivo o label elegido (o resultados de la búsqueda).
Derecha:   la pose de cada personaje en escena para la línea seleccionada (sprites armados
           con las capas de las definiciones `image`), en celdas 1..N.
Abajo:     el texto completo de la línea seleccionada.
"""
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from typing import Callable, Dict, List, Optional, Sequence, Tuple
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
import i18n
from i18n import _t

import audio
import cells
import rpy_parser
import dialogue
import tfcatalog
import scene_edit
import scene_gui
import audio_edit
import audio_gui
import rpy_writer
import transitions
from guiconf import fit_window
from dialogue import DialogueLine, build_index, count_lines, search
from sprites import HAVE_PIL, SpriteBook, build_cell_views
from stage import Frame, build_timeline

try:
    from PIL import Image, ImageTk
    HAVE_TKIMG = True
except ImportError:
    HAVE_TKIMG = False

if getattr(sys, "frozen", False):
    _executable_dir = os.path.dirname(os.path.abspath(sys.executable))
    _app_bundle = os.path.dirname(os.path.dirname(_executable_dir))
    TOOL_DIR = (os.path.dirname(_app_bundle)
                if sys.platform == "darwin" and _app_bundle.endswith(".app")
                else _executable_dir)
else:
    TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
AUTOSAVE_DEFAULT = True        # auto-guardado activado al abrir
AUTOSAVE_DELAY_MS = 4000       # se guarda tras este tiempo SIN nuevos cambios
POSE_PICKER_MIN_SIZE = (950, 560)
AT_EFFECT_CARD_SIZE = (144, 81)
AT_EFFECT_CARD_MIN_WIDTH = 158
AT_EFFECT_PREVIEW_SIZE = (360, 203)
SETTINGS_FILE = os.path.join(TOOL_DIR, "prtt_settings.json")   # solo preferencias de PRTT, nunca el estado


Edits = Dict[object, object]


class EditHistory:
    def __init__(self, limit: int = 300) -> None:
        self.limit = limit
        self._undo: List[Tuple[str, Edits]] = []
        self._redo: List[Tuple[str, Edits]] = []
        self.saved: Edits = {}

    @staticmethod
    def snapshot(edits: Edits) -> Edits:
        return {key: value.copy() for key, value in edits.items()}

    def record(self, before: Edits, after: Edits, desc: str) -> bool:
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

    def dirty(self, edits: Edits) -> bool:
        return edits != self.saved

    def pending(self, edits: Edits) -> int:
        return sum(1 for key in set(edits) | set(self.saved)
                   if edits.get(key) != self.saved.get(key))

    def mark_saved(self, key: object, layout) -> None:
        if layout is None:
            self.saved.pop(key, None)
        else:
            self.saved[key] = layout.copy()


def _load_settings() -> dict:
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_settings(data: dict) -> None:
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
    except OSError:
        pass                     # carpeta de solo lectura: la opción vale solo para esta sesión


class _ScrollableFrame(ttk.Frame):
    def __init__(self, master, *, fit_width=False, **kwargs):
        super().__init__(master)
        self.fit_width = fit_width
        self.canvas = tk.Canvas(self, highlightthickness=0)
        self.vscroll = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.hscroll = ttk.Scrollbar(self, orient="horizontal", command=self.canvas.xview)
        self.content = ttk.Frame(self.canvas, **kwargs)
        self._window = self.canvas.create_window((0, 0), window=self.content, anchor="nw")
        self.content.bind("<Configure>", self._content_changed)
        self.canvas.bind("<Configure>", self._resize_content)
        self.canvas.configure(yscrollcommand=self._set_vertical_scroll,
                              xscrollcommand=self._set_horizontal_scroll)
        self.canvas.bind("<Enter>", lambda _e: self.canvas.focus_set())
        self.canvas.bind("<MouseWheel>", self._wheel)
        self.canvas.bind("<Shift-MouseWheel>", self._wheel_horizontal)
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.canvas.grid(row=0, column=0, sticky="nsew")

    def _content_changed(self, _event=None) -> None:
        self._resize_content()

    def _resize_content(self, event=None) -> None:
        width = event.width if event is not None else self.canvas.winfo_width()
        height = event.height if event is not None else self.canvas.winfo_height()
        self.canvas.itemconfigure(
            self._window,
            width=width if self.fit_width else max(width, self.content.winfo_reqwidth()),
            height=max(height, self.content.winfo_reqheight()),
        )
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _set_vertical_scroll(self, first, last) -> None:
        self.vscroll.set(first, last)
        if float(first) <= 0 and float(last) >= 1:
            if self.vscroll.winfo_manager():
                self.vscroll.grid_remove()
        elif self.vscroll.winfo_manager() != "grid":
            self.vscroll.grid(row=0, column=1, sticky="ns")

    def _set_horizontal_scroll(self, first, last) -> None:
        self.hscroll.set(first, last)
        if float(first) <= 0 and float(last) >= 1:
            if self.hscroll.winfo_manager():
                self.hscroll.grid_remove()
        elif self.hscroll.winfo_manager() != "grid":
            self.hscroll.grid(row=1, column=0, sticky="ew")

    def _wheel(self, event) -> None:
        self.canvas.yview_scroll(-int(event.delta / 120), "units")

    def _wheel_horizontal(self, event) -> None:
        self.canvas.xview_scroll(-int(event.delta / 120), "units")


class DialogueBrowser(tk.Tk):
    def __init__(self, db: rpy_parser.ScriptDB, game_dir: str):
        super().__init__()
        self.db = db
        self.index = build_index(db)
        self.book = SpriteBook(db, game_dir)
        self._timelines = {}     # label -> [Frame]
        self._photos = []        # referencias a las imágenes (Tk las borra si no se guardan)
        self.game_dir = game_dir
        self._pv = None          # motor de preview (se crea al usarlo la primera vez)
        self._pv_frames = []     # diálogos del label mostrado en la pestaña Preview
        self._pv_i = -1
        self._pv_img = None      # última imagen 1280x720 renderizada
        self._pv_photo = None
        self._pv_job = None      # after() de la animación en curso
        self._pv_t0 = 0.0
        self._pv_audio_player: Optional[audio.Player] = None
        self._pv_audio_job: Optional[str] = None
        self._pv_audio_tracks: Optional[audio.TrackBook] = None
        self._pv_music_commands: Dict[int, Optional[rpy_parser.Event]] = {}
        self._pv_music_command_key: Optional[Tuple[str, int, str]] = None
        self._pv_music_track: Optional[str] = None
        self._index_music_commands()
        self._pv_dirty = False
        self._pv_edits = {}      # (label, nº de diálogo) -> cells.CellLayout editado (clave estable al insertar líneas)
        self._catalog = None     # cells.SpriteCatalog (se crea al abrir el selector)
        self._pv_zw = []         # cuadritos de zorder dibujados sobre las celdas
        self._pv_sel = None      # celda seleccionada (1..N) o None
        self._pv_rects = []      # celdas dibujadas: (k, nombre, x0, y0, x1, y1) en px de pantalla
        self._pv_press_info = None     # (celda, x, y) donde se pulsó el ratón
        self._pv_drag_src = None       # celda que se está arrastrando
        self._pv_click_job = None
        self._pv_active_menu = None
        self._pv_geom = (0, 0, 1.0)   # (x, y, escala) de la imagen dentro del canvas
        self._settings = _load_settings()
        # Comentarios «# PRTT» en las líneas que PRTT escribe: APAGADO salvo que el usuario lo active.
        self._write_comments = tk.BooleanVar(value=bool(self._settings.get("write_comments", False)))
        self.writer = rpy_writer.ScriptWriter(game_dir, comments=self._write_comments.get())
        self.hist = EditHistory()                         # deshacer / rehacer / cambios sin guardar
        self._scene_controller = scene_edit.SceneController(game_dir)
        self._scene_key = None                            # (label, nº) del diálogo con el que se abrió el selector
        self._audio_controller = audio_edit.AudioController(game_dir)
        self._audio_key = None                            # (label, nº) del diálogo con el que se abrió «Música»
        self._mtimes = {}                  # archivo -> fecha de modificación cuando PRTT lo leyó
        self._autosave_job = None
        self._modal = 0                    # >0 mientras hay un diálogo modal abierto
        self._pv_cells_on = tk.BooleanVar(value=True)           # mostrar / ocultar las celdas
        self._autosave = tk.BooleanVar(value=AUTOSAVE_DEFAULT)
        
        self._title_base = _t("PRTT - Lector de diálogos  |  {0}", game_dir)
        self.title(self._title_base)
        
        # Carga segura del icono usando la ruta absoluta del directorio de la herramienta
        try:
            icon_path = os.path.join(TOOL_DIR, "logo.ico")
            if os.path.exists(icon_path):
                self.iconbitmap(icon_path)
        except Exception:
            pass
            
        width, height = 1500, 760
        self.geometry(f"{width}x{height}")

        width, height = fit_window(
            self, width, height, min_size=(min(760, width), min(440, height)),
            margin=(10, 30),
        )
        self.minsize(min(760, width), min(440, height))
        self._rows = {}          # iid de la derecha -> DialogueLine
        self._groups = {}        # iid de la izquierda -> lista de DialogueLine
        self._build_ui()
        self._fill_tree()
        self._stat_files()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Control-s>", lambda e: (self.save(), "break")[1])
        # Ctrl+Z / Ctrl+Y / Ctrl+Mayús+Z en cualquier pestaña (bind_all: también con el foco en
        # un árbol, un botón...). Un solo manejador mira la tecla y Mayús: así Bloq Mayús no
        # convierte Ctrl+Z en "rehacer".
        self.bind_all("<Control-KeyPress>", self._on_ctrl_key)
        self._update_ops()

    # ------------------------------------------------------------------ interfaz
    def _build_ui(self) -> None:
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True)
        page = ttk.Frame(self.nb)
        self.page_pv = ttk.Frame(self.nb)
        self.nb.add(page, text=_t("Diálogos"))
        self.nb.add(self.page_pv, text="Preview")
        self._build_preview(self.page_pv)
        self.nb.bind("<<NotebookTabChanged>>", self._pv_on_tab)
        self._build_lang_button()

        top = ttk.Frame(page, padding=(8, 6))
        top.pack(fill="x")
        ttk.Label(top, text=_t("Buscar:")).pack(side="left")
        self.query = tk.StringVar()
        entry = ttk.Entry(top, textvariable=self.query, width=40)
        entry.pack(side="left", padx=6)
        entry.bind("<Return>", lambda e: self._on_search())
        ttk.Button(top, text=_t("Buscar"), command=self._on_search).pack(side="left")
        ttk.Button(top, text=_t("Limpiar"), command=self._clear_search).pack(side="left", padx=4)
        self.info = ttk.Label(top, text="")
        self.info.pack(side="right")
        ttk.Button(top, text=_t("✎ Editar diálogo (F2)"), command=self._edit_dialogues).pack(side="left", padx=(12, 0))
        ttk.Button(top, text=_t("📍 Ir a línea"), command=self._open_selected_dialogue_in_editor).pack(side="left", padx=(6, 0))
        ttk.Button(top, text=_t("🎬 Escenarios…"), command=self._open_scenes).pack(side="left", padx=(6, 0))
        ttk.Button(top, text=_t("🎵 Música…"), command=self._open_audio).pack(side="left", padx=(6, 0))

        panes = ttk.PanedWindow(page, orient="horizontal")
        panes.pack(fill="both", expand=True, padx=8)

        left = ttk.Frame(panes)
        self.tree = ttk.Treeview(left, show="tree", selectmode="browse")
        sb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")          # la barra se empaqueta ANTES: si no, queda cortada
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._on_select_group)
        panes.add(left, weight=1)

        right = ttk.Frame(panes)
        cols = ("n", "label", "who", "text", "where")
        self.rows = ttk.Treeview(right, columns=cols, show="headings", selectmode="extended")
        for col, title, width in (("n", "#", 45), ("label", "Label", 130), ("who", _t("Personaje"), 110),
                                  ("text", _t("Texto"), 520), ("where", _t("Código · archivo:línea"), 170)):
            self.rows.heading(col, text=title)
            self.rows.column(col, width=width, minwidth=40, anchor="w", stretch=(col == "text"))
        sb2 = ttk.Scrollbar(right, orient="vertical", command=self.rows.yview)
        hsb = ttk.Scrollbar(right, orient="horizontal", command=self.rows.xview)
        self.rows.configure(yscrollcommand=sb2.set, xscrollcommand=hsb.set)
        # Las barras se empaquetan primero: las columnas suman más ancho que el panel y,
        # si la lista va antes, empuja la barra fuera de la ventana.
        sb2.pack(side="right", fill="y")
        hsb.pack(side="bottom", fill="x")
        self.rows.pack(side="left", fill="both", expand=True)
        self.rows.bind("<<TreeviewSelect>>", self._on_select_row)
        self.rows.bind("<Double-1>", lambda e: self._edit_dialogues()
                       if self.rows.identify_region(e.x, e.y) == "cell" else None)
        self.bind("<F2>", lambda e: self._edit_dialogues())
        panes.add(right, weight=4)

        side = ttk.Frame(panes, padding=(6, 0))
        self.side_title = ttk.Label(side, text=_t("Pose en escena"), font=("TkDefaultFont", 10, "bold"))
        self.side_title.pack(anchor="w", pady=(0, 4))
        self.cells_frame = ttk.Frame(side)
        self.cells_frame.pack(fill="x")
        self.side_info = tk.Text(side, height=9, wrap="word", state="disabled")
        self.side_info.pack(fill="both", expand=True, pady=(6, 0))
        panes.add(side, weight=3)

        self.detail = tk.Text(page, height=5, wrap="word", state="disabled")
        self.detail.pack(fill="x", padx=8, pady=(6, 8))

    def _dialogue_path(self, file_name: str) -> Optional[str]:
        if not file_name:
            return None
        p = os.path.normpath(file_name)
        if os.path.isabs(p):
            return p if os.path.exists(p) else None
        for base in (self.game_dir, os.getcwd(), os.path.dirname(os.path.abspath(__file__))):
            cand = os.path.normpath(os.path.join(base, p))
            if os.path.exists(cand):
                return cand
        return os.path.normpath(os.path.join(self.game_dir, p))

    def _editor_command(self, path: str, line: int):
        editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or os.environ.get("GIT_EDITOR")
        if editor:
            parts = shlex.split(editor, posix=os.name != "nt")
            if parts:
                exe = os.path.basename(parts[0]).lower()
                if "code" in exe or "codium" in exe:
                    return parts + ["--goto", f"{path}:{line}"]
                if "notepad++" in exe:
                    return parts + [f"-n{line}", path]
                if "subl" in exe or "sublime" in exe:
                    return parts + [f"{path}:{line}"]
                return parts + [path]
        for name, args in (
            ("code", ["--goto", f"{path}:{line}"]),
            ("code-insiders", ["--goto", f"{path}:{line}"]),
            ("codium", ["--goto", f"{path}:{line}"]),
            ("notepad++.exe", [f"-n{line}", path]),
            ("notepad++", [f"-n{line}", path]),
            ("subl", [f"{path}:{line}"]),
            ("sublime_text", [f"{path}:{line}"]),
            ("cursor", ["--goto", f"{path}:{line}"]),
        ):
            exe = shutil.which(name) or (shutil.which(name + ".exe") if os.name == "nt" else None)
            if exe:
                return [exe] + args
        return None

    def _open_dialogue_in_editor(self, dl: Optional[DialogueLine]) -> None:
        if dl is None:
            return
        path = self._dialogue_path(dl.file)
        if not path or not os.path.exists(path):
            messagebox.showerror(_t("Ir a línea"), _t("No encontré el archivo del diálogo:\n{0}", dl.file), parent=self)
            return
        cmd = self._editor_command(path, dl.line)
        if cmd is None:
            try:
                os.startfile(path)
            except (AttributeError, OSError):
                try:
                    subprocess.Popen(["notepad.exe", path])
                except OSError:
                    messagebox.showerror(_t("Ir a línea"), _t("No pude abrir el archivo:\n{0}", path), parent=self)
            return
        try:
            subprocess.Popen(cmd)
        except OSError as exc:
            messagebox.showerror(_t("Ir a línea"), _t("No pude abrir el editor preferido:\n{0}", exc), parent=self)

    def _open_selected_dialogue_in_editor(self) -> None:
        dls = sorted((self._rows[i] for i in self.rows.selection() if i in self._rows), key=lambda d: (d.file, d.line))
        if not dls:
            messagebox.showinfo(_t("Ir a línea"), _t("Elige primero un diálogo en la lista."), parent=self)
            return
        self._open_dialogue_in_editor(dls[0])

    def _open_current_dialogue_in_editor(self) -> None:
        frame = self._pv_frame()
        dl = self._dl_for_frame(frame) if frame is not None else None
        if dl is None:
            messagebox.showinfo(_t("Ir a línea"), _t("Elige primero un diálogo en la pestaña «Diálogos»."), parent=self)
            return
        self._open_dialogue_in_editor(dl)

    # ------------------------------------------------------------------ idioma
    def _build_lang_button(self) -> None:
        """Botón 🌐 en la esquina superior derecha (sobre la fila de pestañas) para elegir el idioma.

        Los idiomas salen de la carpeta lang/ (un .json por idioma). El cambio se guarda en
        prtt_settings.json y se aplica al reiniciar PRTT."""
        langs = i18n.available()
        self._lang_var = tk.StringVar(value=i18n.current())
        menu = tk.Menu(self, tearoff=False)
        for code, name in langs.items():
            menu.add_radiobutton(label=name, value=code, variable=self._lang_var,
                                 command=lambda c=code: self._change_language(c))
        self._lang_menu = menu
        self._lang_btn = ttk.Menubutton(self, text="🌐 " + langs.get(i18n.current(), i18n.current()), menu=menu)
        self._lang_btn.place(relx=1.0, x=-4, y=1, anchor="ne")
        self._lang_btn.lift()

    def _change_language(self, code: str) -> None:
        if code == i18n.current():
            return
        if not i18n.set_language(code):
            self._lang_var.set(i18n.current())
            return
        self._settings["lang"] = code                     # que un _save_settings posterior no lo pise
        _save_settings(self._settings)
        if messagebox.askyesno(_t("Idioma"),
                               _t("Idioma cambiado. PRTT debe reiniciarse para aplicarlo.\n¿Reiniciar ahora?"),
                               parent=self):
            self._restart()
        else:
            self._lang_var.set(i18n.current())            # el menú sigue marcando el idioma activo
            self._pv_status.config(text=_t("El idioma se aplicará la próxima vez que abras PRTT."))

    def _restart(self) -> None:
        """Cierra PRTT (preguntando por los cambios sin guardar) y lo vuelve a abrir con los mismos argumentos."""
        self._on_close()
        try:
            if self.winfo_exists():
                return                                    # se canceló el cierre: PRTT sigue abierto
        except tk.TclError:
            pass
        frozen = getattr(sys, "frozen", False)
        args = [sys.executable] + (sys.argv[1:] if frozen else sys.argv)
        try:
            subprocess.Popen(args, cwd=os.getcwd())
        except OSError as exc:
            print(_t("No se pudo reiniciar PRTT: ciérralo y ábrelo de nuevo.") + f" ({exc})", file=sys.stderr)

    # ------------------------------------------------------------------ pestaña Preview
    def _build_preview(self, page) -> None:
        bar = ttk.Frame(page, padding=(8, 6))
        bar.pack(fill="x")
        ttk.Button(bar, text=_t("◀ Anterior"), command=lambda: self._pv_step(-1)).pack(side="left")
        ttk.Button(bar, text=_t("Siguiente ▶"), command=lambda: self._pv_step(1)).pack(side="left", padx=4)
        ttk.Button(bar, text=_t("📍 Ir a línea"), command=self._open_current_dialogue_in_editor).pack(side="left", padx=(8, 0))
        self._pv_anim = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text=_t("Animar"), variable=self._pv_anim).pack(side="left", padx=8)
        self._pv_music_enabled = tk.BooleanVar(value=False)
        self._pv_music_check = ttk.Checkbutton(
            bar, text=_t("♫ Reproducir música del guion"), variable=self._pv_music_enabled,
            command=self._pv_music_toggle,
        )
        self._pv_music_check.pack(side="left", padx=4)
        ttk.Button(bar, text=_t("Guardar PNG"), command=self._pv_save).pack(side="left")
        ttk.Button(bar, text=_t("Ajustes leídos"), command=self._pv_settings).pack(side="left", padx=4)
        ttk.Button(bar, text=_t("Copiar script"), command=self._pv_copy).pack(side="left")
        ttk.Button(bar, text=_t("🎬 Escenarios…"), command=self._open_scenes).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text=_t("🎵 Música…"), command=self._open_audio).pack(side="left", padx=(4, 0))
        self._pv_status = ttk.Label(bar, text=_t("Elige un diálogo en la pestaña «Diálogos»."))
        self._pv_status.pack(side="right")
        ops = ttk.Frame(page, padding=(8, 0, 8, 4))
        ops.pack(fill="x")
        self._btn_save = ttk.Button(ops, text=_t("Guardar en .rpy  (Ctrl+S)"), command=self.save)
        self._btn_save.pack(side="left")
        self._btn_undo = ttk.Button(ops, text=_t("↶ Deshacer (Ctrl+Z)"), command=self._undo)
        self._btn_undo.pack(side="left", padx=(8, 2))
        self._btn_redo = ttk.Button(ops, text=_t("↷ Rehacer (Ctrl+Y)"), command=self._redo)
        self._btn_redo.pack(side="left")
        ttk.Checkbutton(ops, text=_t("Auto-guardar"), variable=self._autosave,
                        command=self._schedule_autosave).pack(side="left", padx=(12, 4))
        ttk.Checkbutton(ops, text=_t("Comentarios # PRTT en el script"), variable=self._write_comments,
                        command=self._on_comments_toggle).pack(side="left", padx=(0, 4))
        ttk.Checkbutton(ops, text=_t("Mostrar celdas"), variable=self._pv_cells_on,
                        command=self._pv_toggle_cells).pack(side="left", padx=8)
        ttk.Button(ops, text=_t("Recargar .rpy"), command=self._reload_from_disk).pack(side="left")
        self._save_status = ttk.Label(ops, text="")
        self._save_status.pack(side="right")
        # Cantidad de personajes: dropdown compacto junto a los botones (0 = ninguno). Al bajarla se
        # quitan solos los de la derecha, sin preguntar: Ctrl+Z lo devuelve.
        self._pv_count = tk.IntVar(value=0)
        ttk.Label(ops, text=_t("Personajes:")).pack(side="left", padx=(12, 2))
        self._pv_count_box = ttk.Combobox(ops, textvariable=self._pv_count, state="readonly", width=2,
                                          values=list(range(0, cells.MAX_CELLS + 1)))
        self._pv_count_box.pack(side="left", padx=(0, 8))
        self._pv_count_box.bind("<<ComboboxSelected>>", lambda e: self._pv_count_changed())
        self._pv_canvas = tk.Canvas(page, bg="black", highlightthickness=0)
        self._pv_canvas.pack(fill="both", expand=True, padx=8)
        # Panel del Textbox: qué dice la línea que se ve, selección rápida del hablante y «Editar línea».
        tb = ttk.LabelFrame(page, text="Textbox", padding=(8, 4))
        tb.pack(fill="x", padx=8, pady=(4, 0))
        ttk.Button(tb, text=_t("✎ Editar línea  (F2 · doble clic)"), command=self._edit_current_line).pack(side="right")
        self._tb_speaker = tk.StringVar()
        self._tb_choices = []
        self._tb_box = ttk.Combobox(tb, textvariable=self._tb_speaker, state="readonly", width=30)
        self._tb_box.pack(side="right", padx=(6, 8))
        self._tb_box.bind("<<ComboboxSelected>>", lambda e: self._tb_quick_speaker())
        ttk.Label(tb, text=_t("Hablante rápido:")).pack(side="right")
        self._tb_info = ttk.Label(tb, text="", anchor="w")
        self._tb_info.pack(side="left", fill="x", expand=True)
        self._pv_warn = ttk.Label(page, text="", foreground="#a05000", wraplength=1100, justify="left")
        self._pv_warn.pack(fill="x", padx=8, pady=(4, 8))
        self._pv_canvas.bind("<Configure>", lambda e: self._pv_redraw())
        self._pv_canvas.bind("<Button-1>", self._pv_press)
        self._pv_canvas.bind("<Button-3>", self._pv_right_click)
        self._pv_canvas.bind("<B1-Motion>", self._pv_motion)
        self._pv_canvas.bind("<ButtonRelease-1>", self._pv_release)
        self._pv_canvas.bind("<Double-Button-1>", self._pv_double)
        self._pv_canvas.bind("<Motion>", self._pv_hover)
        self.bind("<Right>", lambda e: self._pv_key(1))
        self.bind("<Return>", lambda e: self._pv_key(1))
        self.bind("<Left>", lambda e: self._pv_key(-1))

    def _pv_engine(self):
        if self._pv is None:
            if not HAVE_TKIMG:
                self._pv_status.config(text=_t("Falta Pillow:  py -3 -m pip install pillow"))
                return None
            try:
                import preview
                self._pv = preview.Preview(self.db, self.game_dir, self.book)
            except Exception as exc:      # no tumbar la ventana si el preview falla
                self._pv_status.config(text=_t("No se pudo iniciar el preview: {0}", exc))
                return None
        return self._pv

    def _pv_visible(self) -> bool:
        return self.nb.select() == str(self.page_pv)

    def _pv_set(self, dl: DialogueLine) -> None:
        """Llamado al elegir una línea en la pestaña Diálogos."""
        frame = self._frame_for(dl)
        if frame is None:
            return
        self._pv_frames = self._timelines[dl.label]
        self._pv_i = frame.index
        self._pv_dirty = True

    def _pv_on_tab(self, _e=None) -> None:
        if self._pv_visible() and self._pv_dirty and self._pv_i >= 0:
            self._pv_dirty = False
            self._pv_show(animate=False)

    def _pv_key(self, d: int) -> None:
        if isinstance(self.focus_get(), (tk.Spinbox, tk.Entry, ttk.Entry)):
            return
        if self._pv_visible():
            self._pv_step(d)

    def _pv_step(self, d: int) -> None:
        j = self._pv_i + d
        if 0 <= j < len(self._pv_frames):
            self._pv_i = j
            self._pv_show(animate=self._pv_anim.get() and d > 0)

    def _pv_show(self, animate: bool) -> None:
        if self._pv_job is not None:
            self.after_cancel(self._pv_job)
            self._pv_job = None
        if not (0 <= self._pv_i < len(self._pv_frames)):
            return
        orig = self._pv_frames[self._pv_i]
        eng = self._pv_engine()
        if eng is None:
            self._pv_sync_music(orig)
            return
        frame = self._pv_eff(orig)
        prev = self._pv_eff(self._pv_frames[self._pv_i - 1]) if self._pv_i > 0 else None
        self._pv_sync_count(orig)
        self._pv_status.config(
            text=f"{orig.event.label} · {self._pv_i + 1}/{len(self._pv_frames)} · {orig.event.where}")
        self._tb_refresh(orig)
        bg_transition = eng.background_transition(frame, prev) if animate and prev is not None else None
        if bg_transition is not None and bg_transition.approx:
            note = bg_transition.note or _t("no puedo reproducirla exactamente fuera de Ren'Py")
            self._pv_status.config(
                text=_t("{0} · {1}/{2} · {3} · transición «{4}» no reproducida: {5}", orig.event.label, self._pv_i + 1, len(self._pv_frames), orig.event.where, bg_transition.name, note))
            bg_transition = None
        dur = eng.duration(frame, prev) if (animate and prev is not None) else 0.0
        if bg_transition is not None:
            dur = max(dur, bg_transition.duration)
        if dur > 0:
            old_img = eng.render(prev).convert("RGBA") if bg_transition is not None else None
            self._pv_t0 = time.monotonic()
            self._pv_tick(frame, prev, dur, bg_transition, old_img)
        else:
            self._pv_draw(eng.render(frame))
        self._pv_sync_music(orig)

    def _index_music_commands(self) -> None:
        latest: Optional[rpy_parser.Event] = None
        commands: Dict[int, Optional[rpy_parser.Event]] = {}
        for event in self.db.events:
            if event.kind in ("play", "stop") and event.data.get("channel") == "music":
                latest = event
            elif event.kind == "say":
                commands[id(event)] = latest
        self._pv_music_commands = commands

    def _pv_music_toggle(self) -> None:
        if not self._pv_music_enabled.get():
            if self._pv_audio_job is not None:
                self.after_cancel(self._pv_audio_job)
                self._pv_audio_job = None
            if self._pv_audio_player is not None:
                self._pv_audio_player.close()
                self._pv_audio_player = None
            self._pv_music_command_key = None
            self._pv_music_track = None
            return

        if self._pv_audio_player is not None:
            self._pv_audio_player.close()
        self._pv_audio_player = audio.Player()
        if not self._pv_audio_player.ok:
            reason = self._pv_audio_player.why
            self._pv_audio_player.close()
            self._pv_audio_player = None
            self._pv_music_enabled.set(False)
            self._pv_status.config(text=reason)
            return
        self._pv_music_command_key = None
        self._pv_music_track = None
        frame = self._pv_frame()
        if frame is None:
            self._pv_music_enabled.set(False)
            self._pv_audio_player.close()
            self._pv_audio_player = None
            self._pv_status.config(text=_t("Elige un diálogo para escuchar la música del guion."))
            return
        self._pv_sync_music(frame, force=True)
        self._pv_music_tick()

    @staticmethod
    def _pv_music_arg(args: Sequence[str], name: str, default: float = 0.0) -> float:
        for index, value in enumerate(args[:-1]):
            if value == name:
                try:
                    return max(0.0, float(args[index + 1]))
                except ValueError:
                    return default
        return default

    def _pv_sync_music(self, frame: Frame, force: bool = False) -> None:
        if not self._pv_music_enabled.get() or self._pv_audio_player is None:
            return
        command = self._pv_music_commands.get(id(frame.event))
        command_key = (command.file, command.line, command.raw) if command is not None else None
        if command_key == self._pv_music_command_key and not force:
            return
        self._pv_music_command_key = command_key
        player = self._pv_audio_player
        if command is None or command.kind == "stop":
            fadeout = self._pv_music_arg(command.data.get("args", []), "fadeout") if command else 0.0
            player.fade_out(fadeout)
            self._pv_music_track = None
            if command is None:
                self._pv_status.config(text=_t("No hay una instrucción de música anterior a este diálogo."))
            return

        if command.data.get("queued"):
            self._pv_status.config(text=_t("La cola de música de Ren'Py no se reproduce en la vista previa."))
            return
        if self._pv_audio_tracks is None:
            tracks = audio.collect_tracks(self.db, self.game_dir)
            self._pv_audio_tracks = audio.TrackBook(tracks)
        target = command.data.get("target", "")
        track = self._pv_audio_tracks.find(target)
        if track is None or not track.file:
            player.stop()
            self._pv_music_track = None
            self._pv_status.config(text=_t("No se encontró el archivo de la música «{0}».", target))
            return

        audio.load_info(track)
        args = command.data.get("args", [])
        start = track.start or 0.0
        loop = "noloop" not in args
        fadein = self._pv_music_arg(args, "fadein")
        envelope: Optional[Callable[[float], Optional[float]]] = (
            lambda elapsed: audio.fade_in_gain(elapsed, fadein)
        ) if fadein else None
        error = player.play(track.file, start, loop, track.loop, track.duration, envelope)
        if error:
            self._pv_music_track = None
            self._pv_status.config(text=error)
            return
        self._pv_music_track = track.ref
        self._pv_status.config(text=_t("Música del guion: {0}", track.name))

    def _pv_music_tick(self) -> None:
        self._pv_audio_job = None
        if not self._pv_music_enabled.get() or self._pv_audio_player is None:
            return
        self._pv_audio_player.tick()
        self._pv_audio_job = self.after(40, self._pv_music_tick)

    def _pv_tick(self, frame, prev, dur: float, bg_transition=None, old_img=None) -> None:
        t = time.monotonic() - self._pv_t0
        if t >= dur:
            self._pv_job = None
            self._pv_draw(self._pv.render(frame))
            return
        img = self._pv.render(frame, prev, t, quick=True)
        if bg_transition is not None and old_img is not None:
            img = transitions.render(bg_transition, old_img, img.convert("RGBA"), t)
        self._pv_draw(img, quick=True)
        self._pv_job = self.after(15, lambda: self._pv_tick(frame, prev, dur, bg_transition, old_img))

    def _pv_draw(self, img, quick: bool = False) -> None:
        self._pv_img = img
        self._pv_warn.config(text="  ·  ".join(self._pv.warnings))
        self._pv_redraw(quick)

    def _pv_redraw(self, quick: bool = False) -> None:
        if self._pv_img is None:
            return
        cw = max(self._pv_canvas.winfo_width(), 2)
        ch = max(self._pv_canvas.winfo_height(), 2)
        s = min(cw / 1280, ch / 720)
        size = (max(int(1280 * s), 1), max(int(720 * s), 1))
        res = getattr(Image, "Resampling", Image)
        im = self._pv_img
        if size != im.size:
            im = im.resize(size, res.BILINEAR if quick else res.LANCZOS)
        self._pv_photo = ImageTk.PhotoImage(im)
        self._pv_canvas.delete("all")
        self._pv_canvas.create_image(cw // 2, ch // 2, image=self._pv_photo)
        self._pv_geom = (cw // 2 - size[0] // 2, ch // 2 - size[1] // 2, s)
        if not quick:                      # durante la animación no se dibujan las celdas
            self._pv_draw_cells()

    # ------------------------------------------------------------------ celdas de personajes
    def _pv_frame(self) -> Optional[Frame]:
        return self._pv_frames[self._pv_i] if 0 <= self._pv_i < len(self._pv_frames) else None

    def _pv_layout(self, create: bool = False):
        """Layout del diálogo actual: el editado, o el que trae el guion (detección previa).
        Solo se guarda como edición (create=True) cuando el usuario cambia algo; así los diálogos
        sin tocar conservan sus animaciones originales."""
        frame = self._pv_frame()
        if frame is None:
            return None
        key = self._ekey(frame)
        lay = self._pv_edits.get(key)
        if lay is None:
            lay = cells.layout_from_stage(frame.stage)
            if create:
                self._pv_edits[key] = lay
        return lay

    def _pv_eff(self, frame):
        lay = self._pv_edits.get(self._ekey(frame))
        eng = self._pv_engine()
        return cells.apply_layout(frame, lay, lambda at: eng.lib.has_event(at, "hide")) \
            if lay is not None and eng is not None else frame

    def _pv_rerender(self, msg: str = "") -> None:
        if self._pv_job is not None:
            self.after_cancel(self._pv_job)
            self._pv_job = None
        frame = self._pv_frame()
        if self._pv is None or frame is None:
            return
        self._pv_draw(self._pv.render(self._pv_eff(frame)))
        if msg:
            self._pv_status.config(text=msg)

    def _pv_sync_count(self, frame) -> None:
        """Al cambiar de diálogo: el selector toma lo editado antes, o los que ya trae el guion."""
        lay = self._pv_edits.get(self._ekey(frame))
        if lay is None:
            lay = cells.layout_from_stage(frame.stage)
        self._pv_count.set(max(0, min(lay.count, cells.MAX_CELLS)))
        self._pv_sel = None

    def _pv_count_changed(self) -> None:
        lay = self._pv_layout()
        if lay is None:
            return
        n = self._pv_count.get()
        if n == lay.count:
            return
        before = self._hist_begin()                  # sin alertas: Ctrl+Z deshace la remoción
        lay = self._pv_layout(create=True)
        eff, moves = lay.set_count(n)
        msg = "  ·  ".join(moves) if moves else _t("{0} personaje(s) en escena", eff)
        self._hist_end(before, msg)
        self._pv_count.set(eff)
        self._pv_sel = None
        self._pv_rerender(msg)

    def _pv_draw_cells(self) -> None:
        c = self._pv_canvas
        c.delete("cell")
        for w in self._pv_zw:
            w.destroy()
        self._pv_zw = []
        self._pv_rects = []
        if not self._pv_cells_on.get():          # celdas ocultas: ni bordes, ni textos, ni cuadritos Z
            return
        lay = self._pv_layout()
        eng = self._pv
        if lay is None or lay.count <= 0 or eng is None or self._pv_img is None:
            return
        n = lay.count
        ox, oy, s = self._pv_geom
        small = ("TkDefaultFont", max(8, int(13 * s)), "bold")
        for k, name, x0, y0, x1, y1 in eng.cell_rects(n):
            self._pv_rects.append((k, name, x0, y0, x1, y1))
            chosen = (self._pv_sel == k)
            r = (ox + x0 * s, oy + y0 * s, ox + x1 * s, oy + y1 * s)
            color, w = ("#ffd400", 3) if chosen else ("#ffffff", 2)
            # Solo un borde sólido (sin relleno, sin trama, sin guiones). El halo oscuro por debajo
            # hace que el borde se vea igual sobre fondos claros y oscuros.
            c.create_rectangle(*r, outline="#000000", width=w + 2, tags="cell")
            c.create_rectangle(*r, outline=color, width=w, tags="cell")
            p = lay.slots.get(k)
            self._pv_label(r[0] + 6, r[1] + 4, lay.at_of(k) if p is not None else name, small, anchor="nw")
            label = p.image if p is not None else _t("+ elegir personaje")
            self._pv_label((r[0] + r[2]) / 2, (r[1] + r[3]) / 2, label, small,
                           width=max(40, r[2] - r[0] - 12))
            if p is not None:
                self._pv_add_z(k, p.zorder, r)

    def _pv_label(self, x, y, text: str, font, **kw) -> None:
        """Texto blanco con sombra negra de 1 px (legible sobre cualquier sprite)."""
        c = self._pv_canvas
        c.create_text(x + 1, y + 1, text=text, fill="#000000", font=font, tags="cell", **kw)
        c.create_text(x, y, text=text, fill="#ffffff", font=font, tags="cell", **kw)

    def _pv_toggle_cells(self) -> None:
        self._pv_sel = None
        self._pv_draw_cells()
        self._pv_status.config(text=_t("Celdas visibles: clic en una celda para editarla.")
                               if self._pv_cells_on.get() else _t("Celdas ocultas."))

    def _pv_add_z(self, slot: int, z: int, r) -> None:
        """Cuadrito numérico de zorder en la esquina superior derecha de la celda."""
        fr = tk.Frame(self._pv_canvas, bg="#202020", padx=2, pady=1)
        tk.Label(fr, text="Z", fg="#ffffff", bg="#202020", font=("TkDefaultFont", 8, "bold")).pack(side="left")
        var = tk.StringVar(value=str(z))
        sp = tk.Spinbox(fr, from_=-99, to=99, width=3, textvariable=var, justify="center",
                        command=lambda: self._pv_set_z(slot, var))
        sp.pack(side="left")
        sp.bind("<Return>", lambda e: (self._pv_set_z(slot, var), "break")[1])
        sp.bind("<FocusOut>", lambda e: self._pv_set_z(slot, var))
        self._pv_canvas.create_window(r[2] - 4, r[1] + 4, anchor="ne", window=fr, tags="cell")
        self._pv_zw.append(fr)

    def _pv_set_z(self, slot: int, var) -> None:
        try:
            z = max(-99, min(99, int(var.get())))
        except ValueError:
            var.set("0")
            z = 0
        cur = self._pv_layout()
        if cur is None or slot not in cur.slots or cur.slots[slot].zorder == z:
            return
        before = self._hist_begin()
        lay = self._pv_layout(create=True)
        lay.set_zorder(slot, z)
        self._hist_end(before, f"{lay.slots[slot].image}: zorder {z}")
        # se redibuja después: este evento viene del propio cuadrito, que se destruye al redibujar
        self.after_idle(lambda: self._pv_rerender(f"{lay.slots[slot].image}: zorder {z}"))

    def _pv_cell_at(self, event) -> Optional[int]:
        """Nº de la celda bajo el puntero, o None."""
        ox, oy, sc = self._pv_geom
        if sc <= 0:
            return None
        ix, iy = (event.x - ox) / sc, (event.y - oy) / sc
        for k, _name, x0, y0, x1, y1 in self._pv_rects:
            if x0 <= ix <= x1 and y0 <= iy <= y1:
                return k
        return None

    def _pv_press(self, event) -> None:
        self._pv_canvas.focus_set()          # saca el foco de los cuadritos Z (para que ← → funcionen)
        self._pv_drag_src = None
        if self._pv_in_textbox(event):
            self._pv_press_info = None
            return
        k = self._pv_cell_at(event)
        self._pv_press_info = (k, event.x, event.y) if k is not None else None

    def _pv_motion(self, event) -> None:
        info = self._pv_press_info
        if info is None:
            return
        k, x0, y0 = info
        if self._pv_drag_src is None:
            lay = self._pv_layout()
            if abs(event.x - x0) + abs(event.y - y0) < 8 or lay is None or k not in lay.slots:
                return                                   # aún es un clic (o la celda está vacía)
            self._pv_drag_src = k
            self._pv_canvas.config(cursor="fleur")
        self._pv_draw_drag(event)

    def _pv_draw_drag(self, event) -> None:
        """Resalta la celda de destino y muestra el personaje que se lleva bajo el puntero."""
        c = self._pv_canvas
        c.delete("drag")
        lay = self._pv_layout()
        if lay is None or self._pv_drag_src not in lay.slots:
            return
        dst = self._pv_cell_at(event)
        ox, oy, sc = self._pv_geom
        if dst is not None and dst != self._pv_drag_src:
            for k, _n, x0, y0, x1, y1 in self._pv_rects:
                if k == dst:
                    c.create_rectangle(ox + x0 * sc, oy + y0 * sc, ox + x1 * sc, oy + y1 * sc,
                                       outline="#00e0ff", width=4, tags="drag")
        label = lay.slots[self._pv_drag_src].image
        c.create_text(event.x + 1, event.y - 13, text=label, fill="#000000", tags="drag",
                      font=("TkDefaultFont", 11, "bold"))
        c.create_text(event.x, event.y - 14, text=label, fill="#00e0ff", tags="drag",
                      font=("TkDefaultFont", 11, "bold"))

    def _pv_release(self, event) -> None:
        info, src = self._pv_press_info, self._pv_drag_src
        self._pv_press_info = self._pv_drag_src = None
        self._pv_canvas.delete("drag")
        self._pv_canvas.config(cursor="")
        if info is None:
            return
        if src is None:                                  # no hubo arrastre: era un clic normal
            if self._pv_click_job is not None:
                self.after_cancel(self._pv_click_job)
            self._pv_select_cell(info[0])
            self._pv_click_job = self.after(
                self._pv_double_click_delay(),
                lambda slot=info[0], click_event=event: self._finish_pv_single_click(slot, click_event))
            return
        dst = self._pv_cell_at(event)
        if dst is None or dst == src:
            self._pv_status.config(text=_t("Arrastre cancelado"))
            return
        self._pv_edit(lambda L: L.drag(src, dst))        # el ocupante de `dst` va a la vacía más próxima

    @staticmethod
    def _pv_double_click_delay() -> int:
        if sys.platform == "win32":
            import ctypes
            return int(ctypes.windll.user32.GetDoubleClickTime()) + 50
        return 550

    def _pv_select_cell(self, k: int) -> None:
        name = next((n for kk, n, *_ in self._pv_rects if kk == k), str(k))
        self._pv_sel = k
        self._pv_draw_cells()
        self._pv_status.config(text=_t("Celda {0} seleccionada  ·  arrástrala para moverla", name))

    def _finish_pv_single_click(self, k: int, event) -> None:
        self._pv_click_job = None
        self._pv_menu(k, event)

    def _pv_menu(self, k: int, event) -> None:
        lay = self._pv_layout()
        m = tk.Menu(self, tearoff=0)
        if self._pv_active_menu is not None:
            self._pv_active_menu.unpost()
        self._pv_active_menu = m
        m.add_command(label=_t("Seleccionar personaje y pose…"),
                      command=lambda: self._pv_menu_action(lambda: self._pv_pick(k)))
        if lay is not None and k in lay.slots:
            m.add_command(label=_t("Editar efecto at…"),
                          command=lambda: self._pv_menu_action(
                              lambda: self._pv_pick(k, initial_tab=_t("Efectos"))))
        for j in range(1, lay.count + 1) if lay is not None else ():
            if j == k:
                continue
            other = lay.slots.get(j)
            txt = (_t("Intercambiar con {0} ({1})", lay.name(j), other.image) if other is not None
                   else _t("Mover a {0} (vacía)", lay.name(j)))
            m.add_command(label=txt, command=lambda j=j: self._pv_menu_action(
                lambda: self._pv_edit(lambda L: L.swap(k, j))))
        if lay is not None and k in lay.slots:
            m.add_separator()
            m.add_command(label=_t("Quitar…"),
                          command=lambda: self._pv_menu_action(lambda: self._pv_remove(k)))
        m.post(event.x_root, event.y_root)

    def _pv_right_click(self, event):
        if self._pv_in_textbox(event):
            return None
        k = self._pv_cell_at(event)
        if k is None:
            return None
        if self._pv_click_job is not None:
            self.after_cancel(self._pv_click_job)
            self._pv_click_job = None
        self._pv_press_info = self._pv_drag_src = None
        self._pv_canvas.delete("drag")
        self._pv_canvas.config(cursor="")
        self._pv_menu(k, event)
        return "break"

    def _pv_remove(self, slot: int) -> None:
        lay = self._pv_layout()
        if lay is None or slot not in lay.slots:
            return
        placement = lay.slots[slot]
        dialog = tk.Toplevel(self)
        dialog.title(_t("Quitar personaje"))
        dialog.transient(self)
        dialog.resizable(False, False)
        dialog.result = None
        ttk.Label(
            dialog,
            text=_t("¿Qué quieres quitar de {0}?", placement.image),
            padding=(18, 16, 18, 8),
        ).pack(fill="x")
        ttk.Label(
            dialog,
            text=_t("Puedes eliminar la función y mantener al personaje, o eliminar al personaje de la escena."),
            padding=(18, 0, 18, 12),
            wraplength=380,
            justify="left",
        ).pack(fill="x")
        buttons = ttk.Frame(dialog, padding=(12, 0, 12, 12))
        buttons.pack(fill="x")

        def finish(choice: Optional[str]) -> None:
            dialog.result = choice
            dialog.destroy()

        ttk.Button(buttons, text=_t("Eliminar función"), command=lambda: finish("effect")).pack(side="left")
        ttk.Button(buttons, text=_t("Eliminar personaje de escena"),
                   command=lambda: finish("actor")).pack(side="left", padx=8)
        ttk.Button(buttons, text=_t("Cancelar"), command=lambda: finish(None)).pack(side="right")
        dialog.protocol("WM_DELETE_WINDOW", lambda: finish(None))
        dialog.bind("<Escape>", lambda _event: finish(None))
        dialog.grab_set()
        self._modal += 1
        try:
            self.wait_window(dialog)
        finally:
            self._modal -= 1

        if dialog.result == "effect":
            self._pv_edit(lambda layout: self._pv_clear_effect(layout, slot))
        elif dialog.result == "actor":
            self._pv_edit(lambda layout: layout.remove(slot))

    def _pv_clear_effect(self, layout, slot: int) -> str:
        placement = layout.slots[slot]
        engine = self._pv_engine()
        if engine is None:
            raise ValueError(_t("No se pudo cargar el catálogo de transforms para quitar el efecto."))
        transforms = cells.split_top(placement.transform)
        hide_transforms = {
            transform for transform in transforms
            if engine.lib.has_event(transform, "hide")
        }
        if hide_transforms:
            transforms = [transform for transform in transforms if transform not in hide_transforms]
            placement.transform = ", ".join(transforms)
        else:
            placement.transform = ""
        return _t("Efecto quitado: {0}", placement.image)

    def _pv_menu_action(self, action) -> None:
        if self._pv_active_menu is not None:
            self._pv_active_menu.unpost()
            self._pv_active_menu = None
        action()

    def _pv_edit(self, fn) -> None:
        before = self._hist_begin()
        lay = self._pv_layout(create=True)
        if lay is None:
            return
        try:
            msg = fn(lay)
        except ValueError as exc:
            self._hist_abort(before)                  # la edición falló: no queda nada a medias
            msg = str(exc)
        else:
            self._hist_end(before, msg or _t("edición de celdas"))
        self._pv_sel = None
        self._pv_rerender(msg)

    def _pv_catalog(self):
        if self._catalog is None:
            self._catalog = cells.SpriteCatalog(self.db, self.book)
        return self._catalog

    def _pv_pick(self, k: int, initial_tab: str = "Poses") -> None:
        lay = self._pv_layout()
        if lay is None:
            return
        cat = self._pv_catalog()
        if not cat.tags():
            messagebox.showinfo(_t("Sin personajes"), _t("No encontré ninguna definición «image personaje pose» en tus .rpy."))
            return
        known = set(getattr(self.db, "transforms", ())) | set(getattr(self.db, "transform_defs", {}))
        cur = self._pv_frame()
        dlg = _PosePicker(self, cat, [], current=lay.slots.get(k),
                          default_transform=lay.at_of(k) if k in lay.slots else lay.name(k),
                          known=known, title=_t("Celda {0}", lay.name(k)), db=self.db,
                          engine=self._pv_engine(), count=lay.count, slot=k,
                          bg=cur.stage.bg if cur is not None else "")
        if initial_tab == _t("Efectos"):
            dlg.tfnb.select(dlg.effects_page)
            dlg._on_tf_tab_changed()
        self._modal += 1
        try:
            self.wait_window(dlg)
        finally:
            self._modal -= 1
        if dlg.result is not None:
            self._pv_edit(lambda L: L.place_at(dlg.transform, dlg.result, slot=k, reflow=dlg.reflow))

    def _pv_copy(self) -> None:
        lay = self._pv_layout()
        text = cells.to_rpy(lay) if lay is not None else ""
        if not text:
            self._pv_status.config(text=_t("No hay personajes en las celdas."))
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self._pv_status.config(text=_t("Copiado:  ") + text.replace("\n", "  ·  "))

    def _pv_save(self) -> None:
        if self._pv_img is None:
            return
        path = filedialog.asksaveasfilename(defaultextension=".png", filetypes=[("PNG", "*.png")],
                                            initialfile="preview.png")
        if path:
            self._pv_img.save(path)

    def _pv_settings(self) -> None:
        eng = self._pv_engine()
        if eng is None:
            return
        win = tk.Toplevel(self)
        win.title(_t("Ajustes de la caja de texto leídos de tus .rpy"))
        win.transient(self)
        area = ttk.Frame(win)
        area.pack(fill="both", expand=True)
        txt = tk.Text(area, width=120, height=28, wrap="none", font=("Courier New", 9))
        yscroll = ttk.Scrollbar(area, orient="vertical", command=txt.yview)
        xscroll = ttk.Scrollbar(area, orient="horizontal", command=txt.xview)
        txt.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        yscroll.pack(side="right", fill="y")
        xscroll.pack(side="bottom", fill="x")
        txt.pack(fill="both", expand=True)
        txt.insert("1.0", "\n".join(eng.gui.describe()))
        txt.config(state="disabled")
        fit_window(win, parent=self, min_size=(480, 300))

    # ------------------------------------------------------------------ escenarios (fondos y transiciones)
    def _scene_frame(self):
        """El diálogo con el que se abrió el selector, releído de los scripts actuales."""
        if self._scene_key is None:
            return None
        label, idx = self._scene_key
        frames = self._timeline(label) if label in self.db.labels else []
        return frames[idx] if 0 <= idx < len(frames) else None

    def _scene_find_image(self, path: str):
        """Máscara de un ImageDissolve de tus .rpy (para dibujar su transición); None si no se encuentra."""
        real = self.book.resolve_file(path)
        if not real or not HAVE_TKIMG:
            return None
        try:
            with Image.open(real) as im:
                return im.convert("L")
        except (OSError, ValueError):
            return None

    def _scene_context(self, include_all: bool = False):
        frame = self._scene_frame()
        if frame is None:
            raise RuntimeError(_t("No encuentro el diálogo: pulsa «Recargar .rpy»."))
        return scene_edit.build_context(self.db, frame, self._scene_find_image, include_all)

    def _scene_apply(self, op: str, bg, effect, reshow: bool):
        frame = self._scene_frame()
        if frame is None:
            return False, _t("No encuentro el diálogo: pulsa «Recargar .rpy».")
        ok, msg, full = self._scene_controller.apply(self.db, frame, op, bg, effect, reshow, self._mtimes)
        if ok:
            self._scene_reloaded(full)
        return ok, msg

    def _scene_undo(self):
        ok, msg = self._scene_controller.undo()
        if ok:
            self._scene_reloaded(True)            # puede haberse quitado una definición: se relee todo
        return ok, msg

    def _scene_reloaded(self, full: bool) -> None:
        """Tras escribir un `scene`: releer los .rpy (tus ediciones de poses pendientes se conservan) y redibujar."""
        self._reload_db(full=full)
        if self._pv_visible():
            self._pv_show(animate=False)
        else:
            self._pv_dirty = True

    def _open_scenes(self) -> None:
        frame = self._pv_frame()
        if frame is None:
            messagebox.showinfo(_t("Escenarios"), _t("Elige primero un diálogo en la pestaña «Diálogos»."), parent=self)
            return
        self._scene_key = self._ekey(frame)
        dlg = scene_gui.SceneBrowser(self, self._pv_engine, self._scene_context, self._scene_apply,
                                     self._scene_undo, self._scene_controller.can_undo)
        self._modal += 1                           # sin auto-guardado mientras la ventana está abierta
        try:
            self.wait_window(dlg)
        finally:
            self._modal -= 1
        self._scene_key = None

    # ------------------------------------------------------------------ música (play music / stop music)
    def _audio_frame(self):
        """El diálogo con el que se abrió la ventana de música, releído de los scripts actuales."""
        if self._audio_key is None:
            return None
        label, idx = self._audio_key
        frames = self._timeline(label) if label in self.db.labels else []
        return frames[idx] if 0 <= idx < len(frames) else None

    def _audio_context(self):
        frame = self._audio_frame()
        if frame is None:
            raise RuntimeError(_t("No encuentro el diálogo: pulsa «Recargar .rpy»."))
        return audio_edit.build_context(self.db, frame, self.game_dir)

    def _audio_apply(self, op: str, track, opts):
        frame = self._audio_frame()
        if frame is None:
            return False, _t("No encuentro el diálogo: pulsa «Recargar .rpy».")
        ok, msg = self._audio_controller.apply(self.db, frame, op, track, opts, self._mtimes)
        if ok:
            self._audio_reloaded()
        return ok, msg

    def _audio_undo(self):
        ok, msg = self._audio_controller.undo()
        if ok:
            self._audio_reloaded()
        return ok, msg

    def _audio_reloaded(self) -> None:
        """Tras escribir música: releer los .rpy (cambian los nº de línea; tus poses pendientes se conservan)."""
        self._reload_db(full=False)
        if self._pv_visible():
            self._pv_show(animate=False)
        else:
            self._pv_dirty = True

    def _open_audio(self) -> None:
        frame = self._pv_frame()
        if frame is None:
            messagebox.showinfo(_t("Música"), _t("Elige primero un diálogo en la pestaña «Diálogos»."), parent=self)
            return
        self._audio_key = self._ekey(frame)
        if self._pv_audio_player is not None:
            self._pv_audio_player.stop()
            self._pv_music_command_key = None
        dlg = audio_gui.AudioBrowser(self, self._audio_context, self._audio_apply, self._audio_undo,
                                     self._audio_controller.can_undo)
        self._modal += 1                           # sin auto-guardado mientras la ventana está abierta
        try:
            self.wait_window(dlg)
        finally:
            self._modal -= 1
        self._audio_key = None
        frame = self._pv_frame()
        if self._pv_music_enabled.get() and frame is not None:
            self._pv_sync_music(frame, force=True)

    # ------------------------------------------------------------------ edición, guardado y deshacer
    @staticmethod
    def _ekey(frame):
        """Clave de un diálogo para las ediciones. No usa nº de línea: sigue valiendo aunque al
        guardar se inserten líneas en el .rpy."""
        return (frame.event.label, frame.index)

    def _timeline(self, label: str):
        frames = self._timelines.get(label)
        if frames is None:
            frames = self._timelines[label] = build_timeline(self.db, label)
        return frames

    def _hist_begin(self):
        return EditHistory.snapshot(self._pv_edits)

    def _hist_end(self, before, desc: str) -> None:
        if self.hist.record(before, self._pv_edits, desc):
            self._on_changed()

    def _hist_abort(self, before) -> None:
        self._pv_edits = before

    def _is_dirty(self) -> bool:
        return self.hist.dirty(self._pv_edits)

    def _on_changed(self) -> None:
        self._update_ops()
        self._schedule_autosave()

    def _update_ops(self, note: str = "") -> None:
        dirty = self._is_dirty()
        self.title(("* " if dirty else "") + self._title_base)
        self._btn_save.state(["!disabled"] if dirty else ["disabled"])
        self._btn_undo.state(["!disabled"] if self.hist.can_undo() else ["disabled"])
        self._btn_redo.state(["!disabled"] if self.hist.can_redo() else ["disabled"])
        n = self.hist.pending(self._pv_edits)
        self._save_status.config(text=note or (
            _t("● {0} diálogo(s) sin guardar", n) if dirty else _t("Sin cambios pendientes")))

    # --- deshacer / rehacer
    def _editing_text(self) -> bool:
        """¿El foco está en un campo de texto editable? Ahí Ctrl+Z/Y son del campo, no del historial."""
        try:
            w = self.focus_get()
            if w is None:
                return False
            if w.winfo_class() in ("Entry", "TEntry", "Spinbox", "TSpinbox", "TCombobox", "Text"):
                return str(w.cget("state")) not in ("disabled", "readonly")
        except (KeyError, tk.TclError):      # Tk a veces falla al buscar el foco (popdown de un Combobox)
            pass
        return False

    def _on_ctrl_key(self, e):
        key = (e.keysym or "").lower()
        if sys.platform.startswith("win") and e.keycode in (89, 90):
            key = "y" if e.keycode == 89 else "z"      # teclados no latinos: la tecla física Z / Y
        if key not in ("z", "y"):
            return None
        if self._modal or self.grab_current() not in (None, self):
            return None                                # hay un diálogo modal abierto
        if self._editing_text():
            return None
        if key == "y" or (e.state & 0x1):              # Ctrl+Y  ·  Ctrl+Mayús+Z
            self._redo()
        else:
            self._undo()
        return "break"

    def _undo(self) -> None:
        self._history_step(self.hist.undo, _t("Deshecho"), _t("No hay nada que deshacer."))

    def _redo(self) -> None:
        self._history_step(self.hist.redo, _t("Rehecho"), _t("No hay nada que rehacer."))

    def _history_step(self, op, verb: str, empty: str) -> None:
        res = op(self._pv_edits)
        if res is None:
            self._pv_status.config(text=empty)
            return
        desc, restored = res
        changed = sorted(k for k in set(self._pv_edits) | set(restored)
                         if self._pv_edits.get(k) != restored.get(k))
        self._pv_edits = restored
        frame = self._pv_frame()
        if changed and (frame is None or self._ekey(frame) not in changed):
            label, idx = changed[0]                     # lleva a ver el diálogo que se ha deshecho
            frames = self._timeline(label) if label in self.db.labels else []
            if 0 <= idx < len(frames):
                self._pv_frames, self._pv_i = frames, idx
        if not self._pv_visible() and 0 <= self._pv_i < len(self._pv_frames):
            self.nb.select(self.page_pv)               # así se ve lo que se ha deshecho
        self._pv_show(animate=False)
        self._pv_status.config(text=f"{verb}: {desc}")
        self._on_changed()

    # --- guardar en los .rpy
    def _stat_files(self) -> None:
        self._mtimes = {}
        for rel in self.db.files:
            try:
                self._mtimes[rel] = os.stat(os.path.join(self.game_dir, *rel.split("/"))).st_mtime_ns
            except OSError:
                pass

    def _on_comments_toggle(self) -> None:
        on = bool(self._write_comments.get())
        self.writer.comments = on
        self._settings["write_comments"] = on
        _save_settings(self._settings)
        self._pv_status.config(text=(_t("Se añadirá «# PRTT» al final de las líneas que PRTT escriba.")
                                     if on else _t("PRTT no escribirá comentarios en el script.")))

    def _collect_changes(self):
        changes, errors = [], []
        saved = self.hist.saved
        for key in sorted(set(self._pv_edits) | set(saved)):
            lay = self._pv_edits.get(key)
            if lay == saved.get(key):
                continue
            label, idx = key
            frames = self._timeline(label) if label in self.db.labels else []
            if not 0 <= idx < len(frames):
                errors.append(_t("{0} #{1}: no encuentro ese diálogo en los scripts.", label, idx + 1))
                continue
            # make_change recoge la ventana de instrucciones del diálogo (para reemplazar sus show/hide
            # en su sitio) y quiénes ya estaban en escena antes (para decidir hide o borrar)
            changes.append(rpy_writer.make_change(key, frames[idx], lay, frames[idx - 1] if idx else None, self.db))
        return changes, errors

    def save(self, auto: bool = False) -> bool:
        """Escribe las ediciones pendientes en los .rpy. True si todo quedó guardado."""
        if self._autosave_job is not None:
            self.after_cancel(self._autosave_job)
            self._autosave_job = None
        self.writer.comments = bool(self._write_comments.get())
        changes, errors = self._collect_changes()
        wrote = False
        if changes:
            for r in self.writer.apply(changes, self._mtimes):
                if r.ok:
                    self.hist.mark_saved(r.key, self._pv_edits.get(r.key))
                    wrote = True
                else:
                    errors.append(r.msg)
        if wrote:
            self._reload_db(full=False)          # los números de línea cambiaron: se vuelve a leer
        if not changes and not errors:
            self._update_ops()
            return True
        if errors:
            self._update_ops(note=_t("No se pudo guardar todo"))
            if auto:
                self._pv_status.config(text=_t("Auto-guardado: ") + errors[0])
            else:
                messagebox.showerror(_t("Guardar en los .rpy"), "\n\n".join(errors[:6]), parent=self)
            return False
        self._update_ops(note=(_t("Auto-guardado ") if auto else _t("Guardado ")) + time.strftime("%H:%M:%S"))
        return True

    # --- releer los scripts
    def _reload_db(self, full: bool) -> None:
        """Vuelve a leer /game. full=False (tras guardar): solo se actualizan los números de línea.
        full=True (botón «Recargar .rpy»): se reconstruye todo, por si editaste los scripts fuera."""
        try:
            db = rpy_parser.load(self.game_dir, exclude_dirs=(TOOL_DIR,))
        except Exception as exc:                  # no tumbar la ventana
            self._pv_status.config(text=_t("No se pudo releer la carpeta: {0}", exc))
            return
        cur = self._pv_frame()
        cur_key = self._ekey(cur) if cur is not None else None
        had_engine = self._pv is not None
        new_index = build_index(db)
        self.db = db
        self._index_music_commands()
        self._pv_audio_tracks = None
        self._timelines.clear()
        self._stat_files()
        if full:
            self.book = SpriteBook(db, self.game_dir)
            self._pv = None
            self._catalog = None
            self.index = new_index
            self._rebuild_tree()
        else:
            self._patch_lines(new_index)
        if cur_key is not None and cur_key[0] in db.labels:
            self._pv_frames = self._timeline(cur_key[0])
            self._pv_i = min(cur_key[1], len(self._pv_frames) - 1)
        else:
            self._pv_frames, self._pv_i = [], -1
        frame = self._pv_frame()
        if frame is not None:
            self._pv_sync_music(frame, force=full)
        elif self._pv_audio_player is not None:
            self._pv_audio_player.stop()
        if full:
            if had_engine:
                self._pv_engine()
            if self._pv_visible():
                self._pv_show(animate=False)
            else:
                self._pv_dirty = True

    def _patch_lines(self, new_index) -> None:
        """Pone al día, en los objetos que ya muestra la lista, los nº de línea que cambiaron."""
        fresh = {i.name: i for infos in new_index.values() for i in infos}
        for infos in self.index.values():
            for info in infos:
                new = fresh.get(info.name)
                if new is None:
                    continue
                info.line = new.line
                for dl in info.lines:
                    if dl.n <= len(new.lines):
                        nl = new.lines[dl.n - 1]
                        dl.line, dl.who_var, dl.who, dl.attrs = nl.line, nl.who_var, nl.who, list(nl.attrs)
                        dl.text, dl.raw_text = nl.text, nl.raw_text      # por si se editó el diálogo
        for iid, dl in self._rows.items():
            self.rows.set(iid, "who", dl.speaker)
            self.rows.set(iid, "text", dl.text.replace("\n", " ⏎ "))
            self.rows.set(iid, "where", dl.where)

    def _rebuild_tree(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self._groups.clear()
        self.rows.delete(*self.rows.get_children())
        self._rows.clear()
        self._fill_tree()

    def _reload_from_disk(self) -> None:
        self._reload_db(full=True)
        self._pv_status.config(text=_t("Scripts recargados desde el disco (tus ediciones pendientes se conservan)."))
        self._update_ops()

    # --- auto-guardado (en segundo plano, con after(): no bloquea la ventana ni usa hilos)
    def _schedule_autosave(self) -> None:
        if self._autosave_job is not None:
            self.after_cancel(self._autosave_job)
            self._autosave_job = None
        if self._autosave.get() and self._is_dirty():
            self._autosave_job = self.after(AUTOSAVE_DELAY_MS, self._autosave_fire)

    def _autosave_fire(self) -> None:
        self._autosave_job = None
        if not self._autosave.get() or not self._is_dirty():
            return
        if self._modal or self._pv_job is not None:       # no guardar a mitad de un diálogo/animación
            self._autosave_job = self.after(1000, self._autosave_fire)
            return
        self.save(auto=True)

    # --- cerrar la ventana
    def _on_close(self) -> None:
        if self._is_dirty():
            dlg = _AskSave(self, self.hist.pending(self._pv_edits))
            self.wait_window(dlg)
            if dlg.choice == "cancel":
                return
            if dlg.choice == "save" and not self.save():
                return                                     # no se pudo guardar: la ventana sigue abierta
        for job in (self._autosave_job, self._pv_job):
            if job is not None:
                self.after_cancel(job)
        if self._pv_audio_job is not None:
            self.after_cancel(self._pv_audio_job)
            self._pv_audio_job = None
        if self._pv_audio_player is not None:
            self._pv_audio_player.close()
            self._pv_audio_player = None
        self.destroy()

    def _fill_tree(self) -> None:
        total = count_lines(self.index)
        n_labels = sum(len(v) for v in self.index.values())
        self.info.config(text=_t("{0} archivos · {1} labels · {2} líneas de diálogo", len(self.index), n_labels, total))
        for file, infos in self.index.items():
            all_lines = [dl for i in infos for dl in i.lines]
            fid = self.tree.insert("", "end", text=f"{file}  ({len(all_lines)})", open=False)
            self._groups[fid] = all_lines
            for info in infos:
                lid = self.tree.insert(fid, "end", text=f"{info.name}  ({len(info.lines)})")
                self._groups[lid] = info.lines

    # ------------------------------------------------------------------ eventos
    def _show(self, lines) -> None:
        self.rows.delete(*self.rows.get_children())
        self._rows.clear()
        for w in self.cells_frame.winfo_children():
            w.destroy()
        self._set_text(self.side_info, "")
        for dl in lines:
            iid = self.rows.insert("", "end", values=(
                dl.n, dl.label, dl.speaker, dl.text.replace("\n", " ⏎ "), dl.where))
            self._rows[iid] = dl
        self._set_detail("")
        first = self.rows.get_children()
        if first:                      # selecciona la primera línea para ver su pose enseguida
            self.rows.selection_set(first[0])

    def _on_select_group(self, _event=None) -> None:
        sel = self.tree.selection()
        if sel and sel[0] in self._groups:
            self._show(self._groups[sel[0]])

    def _on_select_row(self, _event=None) -> None:
        sel = self.rows.selection()
        if sel and sel[0] in self._rows:
            dl: DialogueLine = self._rows[sel[0]]
            pose = f"   pose: {' '.join(dl.attrs)}" if dl.attrs else ""
            self._set_detail(_t("{0}{1}   ·   código: {2}\n\n{3}", dl.speaker, pose, dl.where, dl.text))
            self._render_poses(dl)
            self._pv_set(dl)

    # ------------------------------------------------------------------ editar el texto de los diálogos
    def _edit_dialogues(self) -> None:
        """F2 / doble clic / «Editar»: en Preview edita la línea que se ve; en Diálogos, la elegida
        (una sola -> editor por campos; varias -> editor de texto)."""
        if self._modal:
            return
        if self._pv_visible():
            self._edit_current_line()
            return
        dls = sorted((self._rows[i] for i in self.rows.selection() if i in self._rows),
                     key=lambda d: (d.file, d.line))
        if not dls:
            messagebox.showinfo(_t("Editar diálogo"), _t("Elige primero una o varias líneas de diálogo."), parent=self)
            return
        if len({d.file for d in dls}) > 1:
            messagebox.showinfo(_t("Editar diálogo"), _t("Elige líneas de un solo archivo a la vez."), parent=self)
            return
        if len(dls) == 1:
            self._edit_line(dls[0])
        else:
            self._edit_raw(dls)

    def _edit_current_line(self) -> None:
        """Edita la línea del diálogo que se ve en la pestaña Preview."""
        if self._modal:
            return
        frame = self._pv_frame()
        dl = self._dl_for_frame(frame) if frame is not None else None
        if dl is None:
            messagebox.showinfo(_t("Editar línea"), _t("Elige primero un diálogo en la pestaña «Diálogos»."), parent=self)
            return
        self._edit_line(dl)

    def _dl_for_frame(self, frame):
        """La DialogueLine del índice que corresponde a un Frame del preview."""
        ev = frame.event
        for infos in self.index.values():
            for info in infos:
                if info.name != ev.label:
                    continue
                if frame.index < len(info.lines):
                    dl = info.lines[frame.index]
                    if dl.file == ev.file and dl.line == ev.line:
                        return dl
                return next((d for d in info.lines if d.file == ev.file and d.line == ev.line), None)
        return None

    def _read_code(self, dl) -> Optional[str]:
        try:
            return self.writer.read_statements(dl.file, [dl.line])[0][0]
        except ValueError as exc:
            messagebox.showerror(_t("Editar diálogo"), str(exc), parent=self)
            return None

    def _check_say(self, parts):
        """(error, línea) para unas SayParts: primero las reglas del editor, luego las del escritor."""
        why = dialogue.validate(parts, self.db.characters)
        if why:
            return why, ""
        code = dialogue.build_say(parts)
        return (rpy_writer.validate_say(code) or ""), code

    def _edit_line(self, dl) -> None:
        """Editor por campos de UNA línea: hablante · pose (opcional) · texto."""
        old = self._read_code(dl)
        if old is None:
            return
        parts = dialogue.parse_say(old)
        if parts is None:
            messagebox.showinfo(
                _t("Editar línea"), _t("Esta línea no tiene la forma  personaje [pose] \"texto\" ; la abro en el editor de texto."), parent=self)
            self._edit_raw([dl], [old])
            return
        dlg = _SayEditor(self, dl, parts, dialogue.speaker_choices(self.db),
                         dialogue.temp_names(self.db), self._check_say)
        self._modal += 1
        try:
            self.wait_window(dlg)
        finally:
            self._modal -= 1
        if dlg.result is None:
            return
        new = dialogue.parse_say(dlg.result)
        if new is not None and dialogue.same(new, parts):
            self._pv_status.config(text=_t("Diálogo sin cambios."))
            return                                   # no se reescribe una línea que dice lo mismo
        self._apply_dialogue_edits([dl], [old], [dlg.result])

    def _edit_raw(self, dls, olds=None) -> None:
        """Editor de texto: las líneas tal como están en el script (una o varias)."""
        if olds is None:
            try:
                stmts = self.writer.read_statements(dls[0].file, [d.line for d in dls])
            except ValueError as exc:
                messagebox.showerror(_t("Editar diálogo"), str(exc), parent=self)
                return
            olds = [code for code, _c in stmts]
        dlg = _DialogueEditor(self, dls, olds, rpy_writer.validate_say)
        self._modal += 1
        try:
            self.wait_window(dlg)
        finally:
            self._modal -= 1
        if dlg.result is not None:
            self._apply_dialogue_edits(dls, olds, dlg.result)

    # --- textbox del preview: doble clic y selección rápida de hablante
    def _pv_textbox_span(self):
        """(y0, y1) del textbox + caja del nombre, en px de la imagen 1280x720."""
        if self._pv is None:
            return None
        g = self._pv.gui
        h = float(g.textbox_height)
        top = float(g.textbox_yalign) * (720 - h)
        return top - 40, top + h

    def _pv_in_textbox(self, event) -> bool:
        span = self._pv_textbox_span()
        ox, oy, sc = self._pv_geom
        if span is None or sc <= 0 or self._pv_img is None:
            return False
        ix, iy = (event.x - ox) / sc, (event.y - oy) / sc
        return 0 <= ix <= 1280 and span[0] <= iy <= span[1]

    def _pv_double(self, event):
        if self._pv_in_textbox(event):
            self._edit_current_line()
            return "break"
        k = self._pv_cell_at(event)
        if k is not None:
            if self._pv_click_job is not None:
                self.after_cancel(self._pv_click_job)
                self._pv_click_job = None
            self._pv_press_info = self._pv_drag_src = None
            self._pv_canvas.delete("drag")
            self._pv_canvas.config(cursor="")
            if self._pv_active_menu is not None:
                self._pv_active_menu.unpost()
                self._pv_active_menu = None
            name = next((n for slot, n, *_ in self._pv_rects if slot == k), str(k))
            self._pv_sel = k
            self._pv_draw_cells()
            self._pv_status.config(text=_t("Abriendo selección de personaje y pose para {0}", name))
            self._pv_pick(k)
            return "break"
        return None

    def _pv_hover(self, event) -> None:
        if self._pv_press_info is None and self._pv_drag_src is None:
            self._pv_canvas.config(cursor="hand2" if self._pv_in_textbox(event) else "")

    def _tb_refresh(self, frame=None) -> None:
        """Panel Textbox: resumen de la línea y el hablante actual en el desplegable."""
        frame = frame or self._pv_frame()
        self._tb_choices = dialogue.quick_choices(self.db)
        self._tb_box.config(values=[c.label for c in self._tb_choices])
        if frame is None:
            self._tb_info.config(text="")
            self._tb_speaker.set("")
            return
        d = frame.event.data
        who = d["who"]
        if not who:
            cur = dialogue.NARRATOR_LABEL
        elif who in self.db.characters:
            cur = dialogue.speaker_label(self.db, who)
        elif who == "extend":
            cur = ""
        else:
            cur = _t("“{0}” (temporal)", who)
        self._tb_speaker.set(cur)
        pose = f" [{' '.join(d['attrs'])}]" if d["attrs"] else ""
        text = d["plain"].replace("\n", " ⏎ ")
        if len(text) > 90:
            text = text[:87] + "…"
        name = d.get("who_name") or who or _t("(narrador)")
        temp = _t(" (temporal)") if who and who != "extend" and who not in self.db.characters else ""
        line = _t(" · código: {0}:{1}", frame.event.file, frame.event.line) if frame is not None else ""
        self._tb_info.config(text=f"{name}{temp}{pose}:  {text}{line}")

    def _tb_quick_speaker(self) -> None:
        """Cambia el hablante de la línea elegida desde el desplegable del Textbox."""
        if self._modal:
            return
        ch = next((c for c in self._tb_choices if c.label == self._tb_speaker.get()), None)
        frame = self._pv_frame()
        dl = self._dl_for_frame(frame) if frame is not None else None
        if ch is None or dl is None:
            self._tb_refresh()
            return
        if ch.kind == "new":
            self._modal += 1
            try:
                name = simpledialog.askstring(_t("Nombre temporal"), _t("Nombre que verá el jugador (p. ej. ???):"),
                                              parent=self)
            finally:
                self._modal -= 1
            name = " ".join((name or "").split())
            if not name:
                self._tb_refresh()
                return
            ch = dialogue.Choice("temp", name, name)
        self._quick_apply(dl, ch)
        self._tb_refresh()

    def _quick_apply(self, dl, ch) -> None:
        old = self._read_code(dl)
        if old is None:
            return
        parts = dialogue.parse_say(old)
        if parts is None:
            messagebox.showinfo(_t("Editar línea"), _t("Esta línea tiene una forma especial: usa «Editar línea»."),
                                parent=self)
            return
        if ((ch.kind == "var" and not parts.temp and parts.speaker == ch.value)
                or (ch.kind == "narr" and not parts.temp and not parts.speaker)
                or (ch.kind == "temp" and parts.temp and parts.speaker == ch.value)):
            return                                     # ya habla ese: no se toca (ni se pierde su pose)
        new = dialogue.with_speaker(parts, ch.kind, ch.value)
        why, code = self._check_say(new)
        if why:
            messagebox.showerror(_t("Hablante"), why, parent=self)
            return
        self._apply_dialogue_edits([dl], [old], [code])

    def _apply_dialogue_edits(self, dls, olds, news) -> bool:
        """Sobrescribe en el .rpy las líneas cuyo texto cambió y sincroniza lista, índice y preview."""
        edits = [rpy_writer.DialogueEdit((dl.label, dl.n), dl.file, dl.line, old, new.strip())
                 for dl, old, new in zip(dls, olds, news) if new.strip() != old.strip()]
        if not edits:
            self._pv_status.config(text=_t("Diálogo sin cambios."))
            return True
        results = self.writer.apply_dialogues(edits, self._mtimes)
        errors = [r.msg for r in results if not r.ok]
        if any(r.ok for r in results):
            self._reload_db(full=False)            # texto y nº de línea de lo escrito
            if self._pv_visible():
                self._pv_show(animate=False)
            else:
                self._pv_dirty = True
            self._on_select_row()
            self._update_ops()
        if errors:
            messagebox.showerror(_t("Editar diálogo"), "\n\n".join(errors[:6]), parent=self)
            return False
        self._pv_status.config(text=_t("{0} diálogo(s) actualizado(s) en el script · {1}", len(edits), time.strftime('%H:%M:%S')))
        return True

    # ------------------------------------------------------------------ panel de poses
    def _frame_for(self, dl: DialogueLine):
        frames = self._timeline(dl.label)
        i = dl.n - 1
        if i < len(frames) and frames[i].event.file == dl.file and frames[i].event.line == dl.line:
            return frames[i]
        return next((f for f in frames if f.event.file == dl.file and f.event.line == dl.line), None)

    def _render_poses(self, dl: DialogueLine) -> None:
        for w in self.cells_frame.winfo_children():
            w.destroy()
        self._photos = []
        frame = self._frame_for(dl)
        if frame is None:
            self.side_title.config(text=_t("Pose en escena (no disponible)"))
            self._set_text(self.side_info, "")
            return
        views = build_cell_views(frame, self.db, self.book)
        stage = frame.stage
        n = max(len(views), 1)
        size = max(90, min(340, 460 // n))
        self.side_title.config(
            text=_t("Fondo: {0}   ·   {1} celda(s)   ·   habla: {2}", stage.bg or '-', len(views), stage.speaker or _t("(narrador)")))
        infos = []
        if not HAVE_PIL:
            infos.append(_t("Para ver los sprites instala Pillow:  py -3 -m pip install pillow"))
        for col, v in enumerate(views):
            cell = ttk.Frame(self.cells_frame, padding=2)
            cell.grid(row=0, column=col, sticky="n", padx=3)
            tk.Label(cell, text=("🗣 " if v.speaking else "") + v.title, wraplength=size,
                     font=("TkDefaultFont", 9, "bold" if v.speaking else "normal")).pack()
            if v.image is not None and HAVE_TKIMG:
                res = getattr(Image, "Resampling", Image).LANCZOS
                photo = ImageTk.PhotoImage(v.image.resize((size, size), res))
                self._photos.append(photo)
                tk.Label(cell, image=photo, borderwidth=1, relief="solid").pack()
            else:
                cv = tk.Canvas(cell, width=size, height=size, highlightthickness=0)
                cv.create_rectangle(2, 2, size - 2, size - 2, dash=(4, 3))
                cv.create_text(size // 2, size // 2, text=_t("vacía") if not v.image_name else _t("sin imagen"))
                cv.pack()
            if v.info:
                infos.append(v.info)
        self._set_text(self.side_info, "\n\n".join(infos))

    def _set_text(self, widget, text: str) -> None:
        widget.config(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.config(state="disabled")

    def _on_search(self) -> None:
        q = self.query.get()
        if not q.strip():
            return self._clear_search()
        found = search(self.index, q)
        self.tree.selection_remove(self.tree.selection())
        self._show(found)
        self.info.config(text=_t("{0} resultados para «{1}»", len(found), q.strip()))

    def _clear_search(self) -> None:
        self.query.set("")
        self.rows.delete(*self.rows.get_children())
        self._rows.clear()
        self._set_detail("")
        total = count_lines(self.index)
        n_labels = sum(len(v) for v in self.index.values())
        self.info.config(text=_t("{0} archivos · {1} labels · {2} líneas de diálogo", len(self.index), n_labels, total))

    def _set_detail(self, text: str) -> None:
        self.detail.config(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", text)
        self.detail.config(state="disabled")


class _AskSave(tk.Toplevel):
    """Aviso al cerrar con cambios sin guardar. `choice` = "save" | "discard" | "cancel"."""

    def __init__(self, master, n: int):
        super().__init__(master)
        self.title(_t("Cambios sin guardar"))
        self.transient(master)
        self.resizable(False, False)
        self.choice = "cancel"
        frm = ttk.Frame(self, padding=16)
        frm.pack()
        ttk.Label(frm, justify="left", text=(
            _t("Hay {0} diálogo(s) con cambios sin guardar en tus scripts .rpy.\n¿Quieres guardarlos antes de salir?", n))).pack(anchor="w")
        row = ttk.Frame(frm)
        row.pack(anchor="e", pady=(14, 0))
        ok = ttk.Button(row, text=_t("Guardar"), command=lambda: self._done("save"))
        ok.pack(side="left")
        ttk.Button(row, text=_t("No guardar"), command=lambda: self._done("discard")).pack(side="left", padx=6)
        ttk.Button(row, text=_t("Cancelar"), command=lambda: self._done("cancel")).pack(side="left")
        self.protocol("WM_DELETE_WINDOW", lambda: self._done("cancel"))
        self.bind("<Return>", lambda e: self._done("save"))
        self.bind("<Escape>", lambda e: self._done("cancel"))
        self.update_idletasks()
        fit_window(self, parent=master)
        ok.focus_set()
        self.grab_set()

    def _done(self, choice: str) -> None:
        self.choice = choice
        self.destroy()


_BG = "#3a3a3a"
_SEL = "#ffd400"


class _DialogueEditor(tk.Toplevel):
    """Edita las líneas de diálogo elegidas tal como están escritas en el script (una por línea).

    Al aceptar, `result` = lista con el texto nuevo de cada línea (mismo nº de líneas que se abrieron:
    aquí solo se reescribe lo que ya existe; no se añaden ni se quitan diálogos)."""

    def __init__(self, master, dls, codes, validate):
        super().__init__(master)
        self.title(_t("Editar diálogo") if len(codes) == 1 else _t("Editar {0} diálogos", len(codes)))
        self.transient(master)
        self.result = None
        self._n = len(codes)
        self._orig = list(codes)
        self._validate = validate
        where = f"{dls[0].file}:{dls[0].line}" + (f" … {dls[-1].line}" if len(dls) > 1 else "")
        ttk.Label(self, text=f"{dls[0].label}  ·  {where}", font=("TkDefaultFont", 10, "bold")).pack(
            anchor="w", padx=10, pady=(10, 2))
        ttk.Label(self, foreground="#666666", justify="left", wraplength=760, text=(
            _t("Una línea por diálogo, tal como va en el script:  personaje [pose] \"texto\"  [with ...]\nComillas dentro del texto: \\\"   ·   salto de línea: \\n   ·   no añadas ni quites líneas. Tu comentario # de cada línea se conserva."))).pack(anchor="w", padx=10)
        edit_area = ttk.Frame(self)
        edit_area.pack(fill="both", expand=True, padx=10, pady=8)
        self.txt = tk.Text(edit_area, width=95, height=min(max(self._n + 1, 5), 22), wrap="none", undo=True,
                           font=("Courier New", 10))
        yscroll = ttk.Scrollbar(edit_area, orient="vertical", command=self.txt.yview)
        xscroll = ttk.Scrollbar(edit_area, orient="horizontal", command=self.txt.xview)
        self.txt.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        yscroll.pack(side="right", fill="y")
        xscroll.pack(side="bottom", fill="x")
        self.txt.pack(side="left", fill="both", expand=True)
        self.txt.insert("1.0", "\n".join(codes))
        self.txt.edit_reset()
        self.msg = ttk.Label(self, text="", foreground="#b00000", wraplength=760, justify="left")
        self.msg.pack(anchor="w", padx=10)
        btns = ttk.Frame(self)
        btns.pack(fill="x", padx=10, pady=10)
        ttk.Button(btns, text=_t("Restaurar texto"), command=self._reset).pack(side="left")
        ttk.Button(btns, text=_t("Cancelar"), command=self.destroy).pack(side="right")
        ttk.Button(btns, text=_t("Aplicar al script  (Ctrl+Enter)"), command=self._ok).pack(side="right", padx=6)
        self.txt.bind("<KeyRelease>", lambda e: self._check())
        self.bind("<Control-Return>", lambda e: (self._ok(), "break")[1])
        self.bind("<Escape>", lambda e: self.destroy())
        self.resizable(True, True)
        self.update_idletasks()
        width, height = fit_window(
            self, max(self.winfo_reqwidth(), 760), max(self.winfo_reqheight(), 420),
            parent=master, min_size=(700, 380),
        )
        self.minsize(min(700, width), min(380, height))
        self.grab_set()
        self.txt.focus_set()

    def _lines(self):
        return self.txt.get("1.0", "end-1c").split("\n")

    def _problems(self):
        lines = self._lines()
        if len(lines) != self._n:
            return [_t("Debe haber exactamente {0} línea(s), una por diálogo (hay {1}).", self._n, len(lines))]
        out = []
        for k, (old, new) in enumerate(zip(self._orig, lines), 1):
            if new.strip() != old.strip():
                why = self._validate(new)
                if why:
                    out.append(_t("Línea {0}: {1}.", k, why))
        return out

    def _check(self) -> bool:
        problems = self._problems()
        self.msg.config(text="\n".join(problems[:4]))
        return not problems

    def _reset(self) -> None:
        self.txt.delete("1.0", "end")
        self.txt.insert("1.0", "\n".join(self._orig))
        self._check()

    def _ok(self) -> None:
        if not self._check():
            return
        self.result = [l.strip() for l in self._lines()]
        self.destroy()


class _SayEditor(tk.Toplevel):
    """Editor por campos de UNA línea de diálogo.

        Quién habla (izquierda)                       Texto del diálogo (derecha)
        ├ Hablante  [selector o texto]                ┌───────────────────────────┐
        ├ ☐ Hablante no definido / nombre temporal    │ ...                       │
        └ Pose / expresión (opcional)                 └───────────────────────────┘

    Con «nombre temporal» se escribe  "Nombre" "texto"  (sin Character ni pose). `result` = la línea
    nueva (str) al aplicar, o None."""

    def __init__(self, master, dl, parts, choices, temp_names, check):
        super().__init__(master)
        self.title(_t("Editar línea · {0} · {1}", dl.label, dl.where))
        self.transient(master)
        self.result = None
        self._p0 = parts
        self._check = check
        self._by_label = {c.label: c.value for c in choices if c.kind == "var"}
        self._label_of = {c.value: c.label for c in choices if c.kind == "var"}
        self._narr = dialogue.NARRATOR_LABEL

        outer = ttk.Frame(self, padding=10)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(0, weight=1)

        left = ttk.LabelFrame(outer, text=_t("Quién habla"), padding=8)
        left.grid(row=0, column=0, sticky="ns", padx=(0, 10))
        ttk.Label(left, text=_t("Hablante:")).pack(anchor="w")
        self.v_who = tk.StringVar(value=self._initial_who(parts))
        self.cb_who = ttk.Combobox(left, textvariable=self.v_who, width=30,
                                   values=[c.label for c in choices if c.kind in ("narr", "var")])
        self.cb_who.pack(fill="x", pady=(2, 6))
        self.v_temp = tk.BooleanVar(value=parts.temp)
        ttk.Checkbutton(left, text=_t("Hablante no definido / nombre temporal"), variable=self.v_temp,
                        command=self._on_temp).pack(anchor="w")
        self.hint = ttk.Label(left, text="", wraplength=250, justify="left", foreground="#666666")
        self.hint.pack(anchor="w", pady=(2, 8))
        self.cb_temp = None
        if temp_names:
            ttk.Label(left, text=_t("Temporales que ya usa el script:")).pack(anchor="w")
            self.cb_temp = ttk.Combobox(left, state="readonly", width=30, values=list(temp_names))
            self.cb_temp.pack(fill="x", pady=(2, 8))
            self.cb_temp.bind("<<ComboboxSelected>>", self._on_pick_temp)
        ttk.Label(left, text=_t("Pose / expresión (opcional):")).pack(anchor="w")
        self.v_pose = tk.StringVar(value=parts.pose)
        self.ent_pose = ttk.Entry(left, textvariable=self.v_pose, width=32)
        self.ent_pose.pack(fill="x", pady=(2, 0))
        ttk.Label(left, text=_t("Vacío = no cambia la pose en esta línea.\nEjemplos:  1a   ·   2b"), wraplength=250,
                  justify="left", foreground="#666666").pack(anchor="w", pady=(2, 0))

        right = ttk.LabelFrame(outer, text=_t("Texto del diálogo"), padding=8)
        right.grid(row=0, column=1, sticky="nsew")
        sb = ttk.Scrollbar(right, orient="vertical")
        sb.pack(side="right", fill="y")
        self.txt = tk.Text(right, width=64, height=10, wrap="word", undo=True, yscrollcommand=sb.set,
                           font=("TkDefaultFont", 11))
        sb.config(command=self.txt.yview)
        self.txt.pack(side="left", fill="both", expand=True)
        self.txt.insert("1.0", parts.text)
        self.txt.edit_reset()

        ttk.Label(outer, text=_t("Se escribirá en el script:")).grid(row=1, column=0, columnspan=2, sticky="w",
                                                                 pady=(10, 0))
        self.lbl_code = tk.Label(outer, text="", anchor="w", justify="left", wraplength=820,
                                 font=("Courier New", 10), bg="#f2f2f2", relief="sunken", padx=6, pady=3)
        self.lbl_code.grid(row=2, column=0, columnspan=2, sticky="ew")
        self.msg = ttk.Label(outer, text="", foreground="#b00000", wraplength=820, justify="left")
        self.msg.grid(row=3, column=0, columnspan=2, sticky="w", pady=(4, 0))
        btns = ttk.Frame(outer)
        btns.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(btns, text=_t("Cancelar"), command=self.destroy).pack(side="right")
        self.btn_ok = ttk.Button(btns, text=_t("Aplicar al script  (Ctrl+Enter)"), command=self._ok)
        self.btn_ok.pack(side="right", padx=6)

        for var in (self.v_who, self.v_pose):
            var.trace_add("write", lambda *a: self._refresh())
        self.cb_who.bind("<<ComboboxSelected>>", lambda e: self._refresh())
        self.txt.bind("<KeyRelease>", lambda e: self._refresh())
        self.bind("<Control-Return>", lambda e: (self._ok(), "break")[1])
        self.bind("<Escape>", lambda e: self.destroy())
        self._refresh()
        self.update_idletasks()
        fit_window(self, parent=master, min_size=(600, 360))
        self.grab_set()
        self.txt.focus_set()

    def _initial_who(self, p) -> str:
        if p.temp:
            return p.speaker
        if not p.speaker:
            return self._narr
        return self._label_of.get(p.speaker, p.speaker)

    def _collect(self):
        """Las SayParts que describen lo que hay ahora mismo en los campos."""
        temp = bool(self.v_temp.get())
        who = self.v_who.get().strip()
        if temp:
            speaker = who
        elif who in ("", self._narr):
            speaker = ""
        else:
            speaker = self._by_label.get(who, who)
        pose = "" if (temp or not speaker) else self.v_pose.get().strip()
        return dialogue.SayParts(speaker=speaker, temp=temp, pose=pose, text=self.txt.get("1.0", "end-1c"),
                                 tail=self._p0.tail, raw_text=self._p0.raw_text, orig_text=self._p0.orig_text)

    def _on_temp(self) -> None:
        who = self.v_who.get().strip()
        if self.v_temp.get():
            if who == self._narr or who in self._by_label:
                self.v_who.set("")                       # el nombre temporal se escribe a mano
            self.cb_who.focus_set()
        elif who not in self._by_label and who != self._narr:
            self.v_who.set(self._narr)
        self._refresh()

    def _on_pick_temp(self, _e=None) -> None:
        self.v_temp.set(True)
        self.v_who.set(self.cb_temp.get())
        self._refresh()

    def _refresh(self) -> bool:
        parts = self._collect()
        why, code = self._check(parts)
        narr = not parts.temp and not parts.speaker
        self.ent_pose.config(state="disabled" if (parts.temp or narr) else "normal")
        self.hint.config(text=(_t("Se escribe:  \"Nombre\" \"Diálogo\".  Sin pose.") if parts.temp else
                               (_t("Sin hablante: línea de narrador.") if narr else "")))
        self.lbl_code.config(text=code or "—")
        self.msg.config(text=why)
        self.btn_ok.state(["disabled"] if why else ["!disabled"])
        return not why

    def _ok(self) -> None:
        if not self._refresh():
            return
        self.result = self._check(self._collect())[1]
        self.destroy()


def _photo(img, w: int, h: int):
    """PIL.Image -> PhotoImage que cabe en w x h conservando la proporción (None si no hay imagen)."""
    if img is None or not HAVE_TKIMG:
        return None
    s = min(w / img.size[0], h / img.size[1])
    size = (max(1, int(img.size[0] * s)), max(1, int(img.size[1] * s)))
    return ImageTk.PhotoImage(img.resize(size, getattr(Image, "Resampling", Image).LANCZOS))


class _Strip(ttk.LabelFrame):
    """Selector de miniaturas con columnas adaptables y ◀ ▶ para cambiar de página."""
    COLS = 3
    MIN_CARD_WIDTH = 170

    def __init__(self, master, title: str, box, on_pick, on_gallery):
        super().__init__(master, text=title, padding=(6, 4))
        self.base_box = box
        self.scale = 1.0
        self.card_width = self.MIN_CARD_WIDTH
        self.box = box
        self.on_pick = on_pick
        self.items = []                   # [(clave, texto)]
        self.thumb = lambda key: None     # clave -> PhotoImage | None
        self.sel = None
        self.page = 0
        self.columns = self.COLS
        self._photos = []
        self._cells = []
        self.btn_prev = ttk.Button(self, text="◀", width=3, command=lambda: self._turn(-1))
        self.btn_next = ttk.Button(self, text="▶", width=3, command=lambda: self._turn(1))
        self.gallery_button = ttk.Button(self, text=_t("Galería…"), command=on_gallery)
        row = ttk.Frame(self)
        self.info = ttk.Label(self, text="")
        self.btn_prev.grid(row=0, column=0)
        row.grid(row=0, column=1, padx=4, sticky="ew")
        self.btn_next.grid(row=0, column=2)
        self.gallery_button.grid(row=0, column=3, padx=(4, 0))
        self.info.grid(row=1, column=0, columnspan=4, pady=(2, 0))
        self.columnconfigure(1, weight=1)
        self.columnconfigure(3, weight=0)
        self._row = row
        self._columns_configured = 0
        self.bind("<Configure>", self._resize_columns)
        self._configure_columns()

    @classmethod
    def column_count(cls, width: int, card_width: Optional[int] = None) -> int:
        return max(cls.COLS, width // (card_width or cls.MIN_CARD_WIDTH))

    def _resize_columns(self, _event=None) -> None:
        self.after_idle(self._configure_columns)

    def _configure_columns(self) -> None:
        width = self._row.winfo_width()
        if not isinstance(width, int) or width <= 1:
            width = self.winfo_width()
        if not isinstance(width, int) or width <= 1:
            return
        columns = self.column_count(width, self.card_width)
        if columns == self.columns and len(self._cells) >= columns:
            return
        self.columns = columns
        while len(self._cells) < columns:
            c = len(self._cells)
            fr = tk.Frame(self._row, width=self.card_width, height=self.box[1] + 32, bg=_BG,
                          cursor="hand2", highlightthickness=3,
                          highlightbackground=_BG, highlightcolor=_BG)
            fr.pack_propagate(False)
            txt = tk.Label(fr, bg=_BG, fg="#ffffff", font=("TkDefaultFont", 9, "bold"))
            txt.pack(side="bottom")
            img = tk.Label(fr, bg=_BG, fg="#aaaaaa")
            img.pack(expand=True)
            for widget in (fr, img, txt):
                widget.bind("<Button-1>", lambda _event, cell=c: self._click(cell))
            self._cells.append((fr, img, txt))
        for column in range(max(columns, self._columns_configured)):
            self._row.columnconfigure(column, weight=1 if column < columns else 0,
                                      uniform="pose-strip" if column < columns else "")
        self._columns_configured = columns
        for column, (fr, _img, _txt) in enumerate(self._cells):
            if column < columns:
                fr.grid(row=0, column=column, padx=3, sticky="nsew")
            else:
                fr.grid_remove()
        if self.items and self.sel is not None:
            keys = [key for key, _label in self.items]
            if self.sel in keys:
                self.page = keys.index(self.sel) // columns
        self.refresh()

    def set_scale(self, scale: float) -> None:
        self.scale = scale
        self.box = (max(72, int(self.base_box[0] * scale)), max(68, int(self.base_box[1] * scale)))
        self.card_width = max(120, int(self.MIN_CARD_WIDTH * scale))
        for fr, _img, _txt in self._cells:
            fr.config(width=self.card_width, height=self.box[1] + 32)
        self._columns_configured = 0
        self._configure_columns()
        self.refresh()

    def set_items(self, items, thumb, sel=None) -> None:
        self.items, self.thumb, self.sel = list(items), thumb, sel
        keys = [k for k, _lbl in self.items]
        self.page = (keys.index(sel) // self.columns) if sel in keys else 0
        self.refresh()

    def refresh(self) -> None:
        self._photos = []
        start = self.page * self.columns
        for c, (fr, img, txt) in enumerate(self._cells[:self.columns]):
            i = start + c
            if i < len(self.items):
                key, label = self.items[i]
                ph = self.thumb(key)
                self._photos.append(ph)
                img.config(image=ph if ph is not None else "", text="" if ph is not None else _t("sin\nimagen"))
                txt.config(text=label)
                color = _SEL if key == self.sel else _BG
            else:
                img.config(image="", text="")
                txt.config(text="")
                color = _BG
            fr.config(highlightbackground=color, highlightcolor=color)
        pages = max(1, -(-len(self.items) // self.columns))
        self.info.config(text=_t("{0} / {1}   ·   {2} en total", self.page + 1, pages, len(self.items)))
        self.btn_prev.state(["!disabled"] if self.page > 0 else ["disabled"])
        self.btn_next.state(["!disabled"] if self.page < pages - 1 else ["disabled"])

    def _turn(self, d: int) -> None:
        pages = max(1, -(-len(self.items) // self.columns))
        self.page = max(0, min(self.page + d, pages - 1))
        self.refresh()

    def _click(self, c: int) -> None:
        i = self.page * self.columns + c
        if i < len(self.items):
            key = self.items[i][0]
            self.select(key)
            self.on_pick(key)

    def select(self, key) -> None:
        keys = [item_key for item_key, _label in self.items]
        self.sel = key if key in keys else None
        if self.sel is not None:
            self.page = keys.index(self.sel) // self.columns
        self.refresh()


class _SpriteGallery(tk.Toplevel):
    """Ventana redimensionable con todas las expresiones y poses del personaje actual."""
    TAB_NAMES = {"face": _t("Expresiones"), "pose": "Poses"}
    MIN_TILE_WIDTH = 122
    FACE_THUMB_SIZE = (82, 72)
    POSE_THUMB_SIZE = (92, 122)

    def __init__(self, master, on_select, on_close):
        super().__init__(master)
        self.title(_t("Galería de expresiones y poses"))
        self.resizable(True, True)
        self.on_select = on_select
        self.on_close = on_close
        self._photos = {"face": [], "pose": []}
        self._panels = {}
        self._content = {}
        self._tiles = {"face": [], "pose": []}
        self._items = {}
        self._selected = {}
        self._columns = {"face": 0, "pose": 0}
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=8, pady=8)
        for kind, title in self.TAB_NAMES.items():
            page = ttk.Frame(self.nb)
            scroll = _ScrollableFrame(page, padding=6)
            scroll.pack(fill="both", expand=True)
            scroll.canvas.bind("<Configure>", lambda event, tab=kind: self._layout(tab, event))
            self.nb.add(page, text=title)
            self._panels[kind] = scroll
            self._content[kind] = scroll.content
        self.bind("<Escape>", lambda _event: self._close())
        self.protocol("WM_DELETE_WINDOW", self._close)
        width, height = fit_window(self, 820, 620, parent=master, min_size=(480, 360))
        self.minsize(min(480, width), min(360, height))

    def _close(self) -> None:
        self.on_close()
        self.destroy()

    @classmethod
    def column_count(cls, width: int) -> int:
        return max(1, width // cls.MIN_TILE_WIDTH)

    def show_tab(self, kind: str) -> None:
        self.nb.select(0 if kind == "face" else 1)

    def refresh(self, kind: str, items, selected, thumb) -> None:
        content = self._content[kind]
        for tile in content.winfo_children():
            tile.destroy()
        self._photos[kind] = []
        self._tiles[kind] = []
        self._items[kind] = list(items)
        self._selected[kind] = selected
        for key, label in self._items[kind]:
            photo = thumb(key)
            self._photos[kind].append(photo)
            tile = tk.Button(
                content, text=f"✓ {label}" if key == selected else label,
                image=photo if photo is not None else "",
                compound="top", wraplength=110, padx=3, pady=3,
                bg="#ffe680" if key == selected else "#f0f0f0",
                activebackground="#ffe680" if key == selected else "#e5e5e5",
                relief="solid" if key == selected else "raised",
                bd=3 if key == selected else 1,
                highlightthickness=0,
                command=lambda choice=key: self.on_select(kind, choice),
            )
            tile.bind("<MouseWheel>", self._panels[kind]._wheel)
            self._tiles[kind].append(tile)
        self._layout(kind)

    def _layout(self, kind: str, event=None) -> None:
        content = self._content[kind]
        width = event.width if event is not None else self._panels[kind].canvas.winfo_width()
        if not isinstance(width, int) or width <= 1:
            width = 600
        columns = self.column_count(width)
        for column in range(max(columns, self._columns[kind])):
            content.columnconfigure(column, weight=1 if column < columns else 0,
                                    uniform="sprite-gallery" if column < columns else "")
        self._columns[kind] = columns
        for index, tile in enumerate(self._tiles[kind]):
            tile.grid(row=index // columns, column=index % columns, padx=4, pady=4, sticky="nsew")
        self._panels[kind]._resize_content()


class _PosePicker(tk.Toplevel):
    """Selector en dos pasos. Al aceptar: `result` = cells.Placement y `transform` = 't21'...

    Paso 1: los personajes, con su sprite de portada (pose 1).
    Paso 2: pestañas «Poses» (rostro y cuerpo) y «Efectos» (transform `at` con galería y preview),
            además del zorder y el código `show` en vivo.
    Si la celda ya tiene a alguien, se abre directamente en el paso 2.
    """

    def __init__(self, master, catalog, transforms, current=None, default_transform="t11",
                 known=None, title=_t("Elegir personaje"), db=None, engine=None, count=0, slot=1, bg=""):
        super().__init__(master)
        self._db, self._engine, self._count, self._slot, self._bg = db, engine, count, slot, bg
        self._effects = tfcatalog.effects(db) if db is not None else []
        self._syncing = False
        self.title(title)
        self.resizable(True, True)
        self.cat = catalog
        self.result = None
        self.transform = ""
        self.reflow = True
        self._known = set(known or ())
        self._photos = []
        self._cache = {}                    # miniaturas ya generadas (PhotoImage)
        self._gallery_thumb_cache = {}
        self._gallery = None
        self._ui_scale = 1.0
        self._scale_job = None
        self._at_gallery_cards = {}
        self._at_gallery_columns = 0
        self._at_gallery_photos = {}
        self._at_gallery_context = None
        self._at_gallery_loading = False
        self._at_gallery_load_job = None
        self._at_gallery_load_index = 0
        self._at_gallery_load_token = 0
        self._at_gallery_spinner_job = None
        self._at_gallery_spinner_angle = 0
        self._at_gallery_joke_shown = False
        self._at_gallery_hover_key = None
        self._at_gallery_hover_job = None
        self._at_gallery_leave_job = None
        self._at_gallery_hover_started = 0.0
        self._at_gallery_hover_photo = None
        self._at_preview_photo = None
        self._at_preview_source_image = None
        self._at_preview_job = None
        self._at_preview_started = 0.0
        self._at_preview_expr = None
        self._at_preview_duration = 0.0
        self._at_preview_loops = False
        self._at_preview_finished = False
        self.pose = ""
        self.expr = ""
        self.tag = tk.StringVar(value=current.tag if current else "")
        self.z = tk.StringVar(value=str(current.zorder if current else 0))
        self.tf = tk.StringVar(value=default_transform)
        self.step1_scroll = _ScrollableFrame(self, padding=10)
        self.step2_scroll = _ScrollableFrame(self, padding=10, fit_width=True)
        self.step1 = self.step1_scroll.content
        self.step2 = self.step2_scroll.content
        self._build_step1()
        self._build_step2()
        self._build_action_bars()
        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<Configure>", self._schedule_scale)
        self.bind("<Destroy>", self._on_pose_picker_destroy, add="+")
        width, height = 950, 700
        width, height = fit_window(
            self, width, height, parent=master,
            min_size=(min(POSE_PICKER_MIN_SIZE[0], width), min(POSE_PICKER_MIN_SIZE[1], height)),
        )
        self.minsize(min(POSE_PICKER_MIN_SIZE[0], width), min(POSE_PICKER_MIN_SIZE[1], height))
        if current is not None and current.tag in self.cat.tags():
            self._go_step2(current.tag, current.attrs)
        else:
            self.step1_scroll.pack(fill="both", expand=True)
            self.step1_actions.pack(fill="x")
        self.update_idletasks()
        self._apply_scale()
        self.after_idle(self._settle_step2_layout)
        self.grab_set()

    def _settle_step2_layout(self) -> None:
        if not self.winfo_exists() or not self.tag.get():
            return
        self.update_idletasks()
        for strip in (self.strip_face, self.strip_pose):
            strip._configure_columns()
        self.step2_scroll._resize_content()

    def _build_action_bars(self) -> None:
        self.action_bar = ttk.Frame(self, padding=(10, 4))
        self.step1_actions = ttk.Frame(self.action_bar)
        ttk.Button(self.step1_actions, text=_t("Cancelar"), command=self.destroy).pack(side="right")
        self.step2_actions = ttk.Frame(self.action_bar)
        ttk.Button(self.step2_actions, text=_t("◀ Personajes"), command=self._back).pack(side="left")
        right = ttk.Frame(self.step2_actions)
        right.pack(side="right")
        ttk.Button(right, text=_t("Cancelar"), command=self.destroy).pack(side="left")
        ttk.Button(right, text=_t("Aceptar"), command=self._ok).pack(side="left", padx=(6, 0))
        self.action_bar.pack(side="bottom", fill="x")

    # ------------------------------------------------------------------ paso 1: personaje
    def _build_step1(self) -> None:
        for column in range(4):
            self.step1.columnconfigure(column, weight=1)
        ttk.Label(self.step1, text=_t("Elige un personaje"), font=("TkDefaultFont", 11, "bold")).grid(
            row=0, column=0, columnspan=4, sticky="w", pady=(0, 8))
        self._char_buttons = []
        self._char_tags = list(self.cat.tags())
        self._char_photos = []
        for i, tag in enumerate(self.cat.tags()):
            attrs = self.cat.default_preview(tag)
            photo = _photo(self.cat.image(tag, attrs), int(150 * self._ui_scale),
                           int(190 * self._ui_scale)) if attrs else None
            self._char_photos.append(photo)
            label = tag.capitalize() + (f"\n({tag} {' '.join(attrs)})" if attrs else "")
            button = tk.Button(self.step1, text=label, image=photo, compound="top", padx=6, pady=6,
                               command=lambda t=tag: self._go_step2(t))
            button.grid(row=1 + i // 4, column=i % 4, padx=4, pady=4, sticky="nsew")
            self._char_buttons.append(button)
    # ------------------------------------------------------------------ paso 2: rostro, pose, at, z
    def _build_step2(self) -> None:
        f = self.step2
        f.columnconfigure(0, weight=1)
        f.rowconfigure(1, weight=1)
        self.head = ttk.Label(f, text="", font=("TkDefaultFont", 11, "bold"))
        self.head.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self.tfnb = ttk.Notebook(f)
        self.tfnb.grid(row=1, column=0, sticky="nsew")
        self.poses_page = ttk.Frame(self.tfnb, padding=8)
        self.effects_page = ttk.Frame(self.tfnb, padding=8)
        self.tfnb.add(self.poses_page, text="Poses")
        self.tfnb.add(self.effects_page, text=_t("Efectos"))
        self.tfnb.bind("<<NotebookTabChanged>>", self._on_tf_tab_changed)

        poses = self.poses_page
        poses.columnconfigure(1, weight=1)
        poses.rowconfigure(0, weight=1)
        pf = tk.Frame(poses, width=250, height=330, bg=_BG)
        self._preview_frame = pf
        pf.pack_propagate(False)
        pf.grid(row=0, column=0, rowspan=3, padx=(0, 10), sticky="n")
        self.prev = tk.Label(pf, bg=_BG, fg="#ffffff")
        self.prev.pack(expand=True)
        self.strip_face = _Strip(poses, _t("Rostro (expresión)"), (110, 100), self._pick_expr,
                                 lambda: self._open_gallery("face"))
        self.strip_pose = _Strip(poses, _t("Pose (cuerpo)"), (110, 150), self._pick_pose,
                                 lambda: self._open_gallery("pose"))
        self.strip_face.grid(row=0, column=1, sticky="ew")
        self.strip_pose.grid(row=1, column=1, sticky="ew", pady=(6, 0))

        effects = self.effects_page
        effects.columnconfigure(0, weight=2)
        effects.columnconfigure(1, weight=3)
        effects.rowconfigure(0, weight=1)
        left = ttk.Frame(effects)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(1, weight=0)
        ttk.Label(left, text=_t("Vista previa del efecto"), font=("TkDefaultFont", 10, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 4))
        at_preview_frame = tk.Frame(
            left, width=AT_EFFECT_PREVIEW_SIZE[0], height=AT_EFFECT_PREVIEW_SIZE[1], bg="#000000",
        )
        self._at_preview_frame = at_preview_frame
        at_preview_frame.grid(row=1, column=0, sticky="n")
        at_preview_frame.grid_propagate(False)
        self._at_preview_label = tk.Label(
            at_preview_frame, bg="#000000", fg="#ffffff",
            text=_t("Selecciona un efecto para ver la preview"),
        )
        self._at_preview_label.pack(fill="both", expand=True)
        at_preview_frame.bind("<Configure>", self._on_at_preview_resize)
        self._at_gallery_loader = tk.Frame(at_preview_frame, bg="#000000")
        self._at_gallery_loader_content = tk.Frame(self._at_gallery_loader, bg="#000000")
        self._at_gallery_loader_content.place(relx=0.5, rely=0.5, anchor="center")
        self._at_gallery_spinner = tk.Canvas(
            self._at_gallery_loader_content, width=44, height=44, bg="#000000", highlightthickness=0,
        )
        self._at_gallery_spinner.pack(pady=(0, 8))
        self._at_gallery_status = tk.Label(
            self._at_gallery_loader_content, text="", bg="#000000", fg="#ffffff",
            justify="center", wraplength=340,
        )
        self._at_gallery_status.pack(fill="x", padx=8)
        self._at_gallery_progress = ttk.Progressbar(
            self._at_gallery_loader_content, mode="determinate", maximum=1, value=0,
        )
        self._at_gallery_progress.pack(fill="x", padx=24, pady=(8, 4))
        self._at_gallery_detail = tk.Label(
            self._at_gallery_loader_content, text="", bg="#000000", fg="#c8c8c8",
            justify="center", wraplength=340,
        )
        self._at_gallery_detail.pack(fill="x", padx=8)
        self._at_preview_controls = ttk.Frame(left)
        self._at_preview_controls.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        self._at_preview_reload = ttk.Button(
            self._at_preview_controls, text=_t("Recargar preview"),
            command=self._reload_at_effect_preview,
        )
        self._at_preview_reload.pack(side="left")
        self._at_preview_loop_var = tk.BooleanVar(value=False)
        self._at_preview_loop_check = ttk.Checkbutton(
            self._at_preview_controls, text=_t("Mostrar en bucle"),
            variable=self._at_preview_loop_var,
            command=self._toggle_at_effect_loop,
        )
        self._at_preview_loop_check.pack(side="left", padx=(10, 0))
        self.fx_help = ttk.Label(left, text="", wraplength=340, justify="left")
        self.fx_help.grid(row=3, column=0, sticky="ew", pady=(6, 0))
        ttk.Label(left, text=_t("Transform «at» (edición avanzada):")).grid(
            row=4, column=0, sticky="w", pady=(10, 0))
        ttk.Label(
            left, text="Ejemplos: t21 · hf32 · t21, hop · hop(x=400, z=0.9)",
            wraplength=340, justify="left",
        ).grid(row=5, column=0, sticky="w")
        self._tf_entry = ttk.Entry(left, textvariable=self.tf, font=("Courier New", 10), width=54)
        self._tf_entry.grid(row=6, column=0, sticky="ew", pady=(3, 0))
        self.reflow_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            left, text=_t("Reajustar a los demás personajes si es una posición (t21, hf32...)"),
            variable=self.reflow_var,
        ).grid(row=7, column=0, sticky="w", pady=(4, 0))

        gallery = ttk.Frame(effects)
        gallery.grid(row=0, column=1, sticky="nsew")
        gallery.rowconfigure(0, weight=1)
        gallery.columnconfigure(0, weight=1)
        self._at_gallery_scroll = _ScrollableFrame(gallery, padding=6, fit_width=True)
        self._at_gallery_scroll.grid(row=0, column=0, sticky="nsew")
        self._at_gallery_content = self._at_gallery_scroll.content
        self._at_gallery_layout_job = None
        self._at_gallery_scroll.canvas.bind("<Configure>", self._schedule_at_gallery_layout)
        self._build_at_effect_gallery()

        opt = ttk.Frame(f)
        opt.grid(row=2, column=0, sticky="w", pady=(6, 0))
        ttk.Label(opt, text=_t("Z (capa):")).pack(side="left")
        tk.Spinbox(opt, from_=-99, to=99, width=4, textvariable=self.z,
                   command=self._update).pack(side="left", padx=4)
        ttk.Label(opt, text=_t("mayor = al frente")).pack(side="left")
        self.z.trace_add("write", lambda *_: self._update_code())
        self.fx_list = tk.Listbox(self, height=0, width=0, exportselection=False)
        self._fx_list = self.fx_list
        for e in self._effects:
            self.fx_list.insert("end", e.label + ("" if e.known else _t("   · propio")))
        self.fx_list.bind("<<ListboxSelect>>", self._on_effect)
        self.tf.trace_add("write", lambda *_: self._on_tf_changed())
        self.code = tk.Entry(f, font=("Courier New", 10), state="readonly", width=42,
                             readonlybackground="#f2f2f2")
        self.code.grid(row=3, column=0, sticky="ew", pady=(6, 0))
        self.tf_msg = ttk.Label(f, text="", wraplength=380, justify="left")
        self.tf_msg.grid(row=4, column=0, sticky="ew")

    def _build_at_effect_gallery(self) -> None:
        for effect in self._effects:
            frame = tk.Frame(
                self._at_gallery_content, bd=0, highlightthickness=1,
                highlightbackground="#b8b8b8",
            )
            canvas = tk.Canvas(
                frame, width=AT_EFFECT_CARD_SIZE[0], height=AT_EFFECT_CARD_SIZE[1],
                highlightthickness=0, bg="#101010",
            )
            canvas.pack(padx=3, pady=(3, 1))
            label = ttk.Label(
                frame, text="", anchor="center", justify="center",
                wraplength=AT_EFFECT_CARD_SIZE[0],
            )
            label.pack(fill="x", padx=2, pady=(0, 3))
            for widget in (frame, canvas, label):
                widget.bind("<Button-1>", lambda _event, key=effect.name: self._select_at_effect_card(key))
                widget.bind("<MouseWheel>", self._at_gallery_scroll._wheel)
                widget.bind("<Enter>", lambda _event, key=effect.name: self._on_at_effect_card_enter(key))
                widget.bind("<Leave>", lambda _event, key=effect.name: self._on_at_effect_card_leave(key))
            self._at_gallery_cards[effect.name] = {
                "frame": frame, "canvas": canvas, "label": label, "effect": effect,
            }
        self._layout_at_gallery()
        self._update_at_effect_selection()

    @staticmethod
    def _at_effect_column_count(width: int) -> int:
        return max(3, width // AT_EFFECT_CARD_MIN_WIDTH)

    def _layout_at_gallery(self, event=None) -> None:
        if not hasattr(self, "_at_gallery_scroll"):
            return
        content = self._at_gallery_content
        width = (event.width if event is not None and event.width > 1
                 else self._at_gallery_scroll.canvas.winfo_width())
        if not isinstance(width, int) or width <= 1:
            width = 600
        columns = self._at_effect_column_count(width)
        for column in range(max(columns, self._at_gallery_columns)):
            content.columnconfigure(
                column,
                weight=1 if column < columns else 0,
                uniform="at-effect-cards" if column < columns else "",
            )
        self._at_gallery_columns = columns
        for index, card in enumerate(self._at_gallery_cards.values()):
            card["frame"].grid(row=index // columns, column=index % columns, padx=3, pady=3, sticky="nsew")
        self._at_gallery_scroll._resize_content()

    def _schedule_at_gallery_layout(self, _event=None) -> None:
        if self._at_gallery_layout_job is not None:
            self.after_cancel(self._at_gallery_layout_job)
        self._at_gallery_layout_job = self.after_idle(self._run_at_gallery_layout)

    def _run_at_gallery_layout(self) -> None:
        self._at_gallery_layout_job = None
        if self.winfo_exists():
            self._layout_at_gallery()

    def _on_tf_tab_changed(self, _event=None) -> None:
        if self.tfnb.select() == str(self.effects_page):
            self._schedule_at_gallery_layout()
            self._refresh_at_effect_gallery()
            self._refresh_at_effect_preview()
        else:
            self._stop_at_effect_preview()

    def _at_effect_text(self, effect) -> str:
        return tfcatalog.resolve(effect, self._count, self._slot, self._defined())

    def _at_effect_context(self):
        return self.tag.get(), tuple(self._attrs() or ()), self.expr, self._bg

    def _at_effect_event(self, text: str) -> str:
        if (self._engine.lib.duration_expr(text, "show") > 0
                or self._engine.lib.loops_expr(text, "show")):
            return "show"
        return "hide" if self._engine.lib.has_event(text, "hide") else "show"

    def _at_effect_image(self, effect_name: str, t):
        effect = next(item for item in self._effects if item.name == effect_name)
        text = self._at_effect_text(effect)
        return self._engine.sprite_frame(
            self.tag.get(), self._attrs() or (), text, self._at_effect_event(text), t,
            f"t{self._count}{self._slot}", self._bg, quick=True,
        )

    def _at_effect_static_photo(self, effect):
        text = self._at_effect_text(effect)
        cache_key = (*self._at_gallery_context, text, AT_EFFECT_CARD_SIZE)
        if cache_key not in self._at_gallery_photos:
            event = self._at_effect_event(text)
            duration = self._engine.lib.duration_expr(text, event)
            t = (min(duration / 2, 1.0) if duration > 0 else None)
            if self._engine.lib.loops_expr(text, event):
                t = 1.0
            image = self._at_effect_image(effect.name, t)
            self._at_gallery_photos[cache_key] = _photo(image, *AT_EFFECT_CARD_SIZE)
        return self._at_gallery_photos[cache_key]

    def _refresh_at_effect_gallery(self) -> None:
        if not self.tag.get():
            return
        context = self._at_effect_context()
        if context != self._at_gallery_context:
            self._stop_at_effect_hover(render_static=False)
            self._cancel_at_gallery_load()
            self._at_gallery_context = context
            self._at_gallery_photos.clear()
            if self._engine is None or not HAVE_TKIMG:
                self._show_unavailable_at_effects()
            elif self._at_gallery_cards:
                self._start_at_gallery_load()
            else:
                self._update_at_effect_selection()
        elif not self._at_gallery_loading:
            self._update_at_effect_selection()
        self._at_gallery_scroll._resize_content()

    def _show_unavailable_at_effects(self) -> None:
        for card in self._at_gallery_cards.values():
            card["canvas"].delete("all")
            card["canvas"].create_text(
                AT_EFFECT_CARD_SIZE[0] // 2, AT_EFFECT_CARD_SIZE[1] // 2,
                text=_t("Vista previa no disponible"), fill="#ffffff", width=AT_EFFECT_CARD_SIZE[0] - 8,
            )
        self._update_at_effect_selection()

    def _start_at_gallery_load(self) -> None:
        self._at_gallery_loading = True
        self._at_gallery_load_token += 1
        token = self._at_gallery_load_token
        self._at_gallery_load_index = 0
        self._at_gallery_joke_shown = False
        self._at_gallery_loader.place(relx=0, rely=0, relwidth=1, relheight=1)
        self._at_gallery_loader.lift()
        self._at_preview_label.config(image="", text="")
        self._at_gallery_progress.configure(maximum=max(len(self._effects), 1), value=0)
        self._at_gallery_status.config(text=_t("Leyendo efectos del personaje…"))
        self._at_gallery_detail.config(text=_t("Preparando la caché de previews"))
        self._animate_at_gallery_spinner()
        self._at_gallery_load_job = self.after(
            180, lambda current_token=token: self._cache_next_at_effect(current_token),
        )

    def _cancel_at_gallery_load(self, hide: bool = True) -> None:
        self._at_gallery_load_token += 1
        if self._at_gallery_load_job is not None:
            self.after_cancel(self._at_gallery_load_job)
            self._at_gallery_load_job = None
        if self._at_gallery_spinner_job is not None:
            self.after_cancel(self._at_gallery_spinner_job)
            self._at_gallery_spinner_job = None
        self._at_gallery_loading = False
        if hide:
            self._at_gallery_loader.place_forget()

    def _animate_at_gallery_spinner(self) -> None:
        if not self._at_gallery_loading:
            return
        canvas = self._at_gallery_spinner
        canvas.delete("all")
        canvas.create_oval(5, 5, 39, 39, outline="#454545", width=4)
        canvas.create_arc(
            5, 5, 39, 39, start=self._at_gallery_spinner_angle,
            extent=250, style="arc", outline="#67b7ff", width=4,
        )
        self._at_gallery_spinner_angle = (self._at_gallery_spinner_angle - 24) % 360
        self._at_gallery_spinner_job = self.after(70, self._animate_at_gallery_spinner)

    def _cache_next_at_effect(self, token: int) -> None:
        self._at_gallery_load_job = None
        if token != self._at_gallery_load_token or not self._at_gallery_loading:
            return
        if self._at_gallery_load_index >= len(self._effects):
            self._finish_at_gallery_load()
            return
        index = self._at_gallery_load_index
        effect = self._effects[index]
        self._at_gallery_status.config(
            text=_t("Cargando previews: {0} de {1}", index + 1, len(self._effects)),
        )
        self._at_gallery_detail.config(text=_t("Almacenando miniaturas en caché…"))
        try:
            photo = self._at_effect_static_photo(effect)
        except Exception as exc:
            self._cancel_at_gallery_load()
            messagebox.showerror(
                _t("Error al cargar previews"),
                _t("No se pudo generar la preview del efecto «{0}»:\n{1}", effect.label, exc),
                parent=self,
            )
            return
        self._draw_at_effect_card(effect.name, photo)
        self._at_gallery_load_index += 1
        self._at_gallery_progress.configure(value=self._at_gallery_load_index)
        if self._at_gallery_load_index >= max(1, (len(self._effects) * 9 + 9) // 10):
            self._at_gallery_joke_shown = True
            self._at_gallery_detail.config(text=_t("Esperar a que Monika revise el script"))
        self._at_gallery_load_job = self.after(
            500 if self._at_gallery_joke_shown else 60,
            lambda current_token=token: self._cache_next_at_effect(current_token),
        )

    def _finish_at_gallery_load(self) -> None:
        self._at_gallery_loading = False
        if self._at_gallery_spinner_job is not None:
            self.after_cancel(self._at_gallery_spinner_job)
            self._at_gallery_spinner_job = None
        self._at_gallery_loader.place_forget()
        self._update_at_effect_selection()
        self._at_gallery_scroll._resize_content()
        self._refresh_at_effect_preview(force=True)

    def _refresh_at_effect_preview(self, force: bool = False) -> None:
        if (self.tfnb.select() != str(self.effects_page) or not self.tag.get()
                or self._engine is None or not HAVE_TKIMG):
            self._stop_at_effect_preview()
            return
        expression = self.tf.get().strip()
        context = (*self._at_effect_context(), expression)
        if force or context != self._at_preview_expr:
            self._stop_at_effect_preview()
            self._at_preview_expr = context
            self._at_preview_started = time.monotonic()
            event = self._at_effect_event(expression)
            self._at_preview_duration = self._engine.lib.duration_expr(expression, event)
            self._at_preview_loops = bool(self._at_preview_loop_var.get())
            self._at_preview_finished = False
        if self._at_preview_finished or self._at_preview_job is not None:
            return
        if self._at_preview_duration <= 0 and not self._at_preview_loops:
            self._draw_at_effect_preview(None)
            self._at_preview_finished = True
            return
        self._tick_at_effect_preview()

    def _stop_at_effect_preview(self) -> None:
        if self._at_preview_job is not None:
            self.after_cancel(self._at_preview_job)
            self._at_preview_job = None

    def _on_pose_picker_destroy(self, event) -> None:
        if event.widget is self:
            self._stop_at_effect_preview()
            self._stop_at_effect_hover(render_static=False)
            self._cancel_at_gallery_load(hide=False)
            if self._at_gallery_layout_job is not None:
                self.after_cancel(self._at_gallery_layout_job)
                self._at_gallery_layout_job = None

    def _tick_at_effect_preview(self) -> None:
        self._at_preview_job = None
        if self.tfnb.select() != str(self.effects_page):
            return
        elapsed = time.monotonic() - self._at_preview_started
        if self._at_preview_loops and self._at_preview_duration > 0:
            elapsed %= self._at_preview_duration
        elif elapsed >= self._at_preview_duration:
            self._draw_at_effect_preview(None)
            self._at_preview_finished = True
            return
        self._draw_at_effect_preview(elapsed)
        self._at_preview_job = self.after(33, self._tick_at_effect_preview)

    def _draw_at_effect_preview(self, elapsed) -> None:
        expression = self.tf.get().strip()
        image = self._engine.sprite_frame(
            self.tag.get(), self._attrs() or (), expression,
            self._at_effect_event(expression), elapsed,
            f"t{self._count}{self._slot}", self._bg, quick=True,
        )
        self._at_preview_source_image = image
        self._show_at_effect_preview_image(image)

    def _on_at_preview_resize(self, _event=None) -> None:
        if self._at_preview_source_image is not None:
            self._show_at_effect_preview_image(self._at_preview_source_image)

    def _show_at_effect_preview_image(self, image) -> None:
        width = int(AT_EFFECT_PREVIEW_SIZE[0] * self._ui_scale)
        height = int(AT_EFFECT_PREVIEW_SIZE[1] * self._ui_scale)
        self._at_preview_photo = _photo(image, width, height)
        self._at_preview_label.config(
            image=self._at_preview_photo if self._at_preview_photo is not None else "",
            text="" if self._at_preview_photo is not None else _t("(vista previa no disponible)"),
            compound="center",
        )

    def _reload_at_effect_preview(self) -> None:
        self._at_preview_expr = None
        self._at_preview_finished = False
        self._at_preview_loops = bool(self._at_preview_loop_var.get())
        self._at_gallery_loader.place_forget()
        self._stop_at_effect_preview()
        self._refresh_at_effect_preview(force=True)

    def _toggle_at_effect_loop(self) -> None:
        self._at_preview_loops = bool(self._at_preview_loop_var.get())
        self._at_gallery_loader.place_forget()
        self._refresh_at_effect_preview(force=True)

    def _draw_at_effect_card(self, effect_name: str, photo) -> None:
        canvas = self._at_gallery_cards[effect_name]["canvas"]
        canvas.delete("all")
        if photo is not None:
            canvas.create_image(
                AT_EFFECT_CARD_SIZE[0] // 2, AT_EFFECT_CARD_SIZE[1] // 2,
                anchor="center", image=photo,
            )

    def _update_at_effect_selection(self) -> None:
        selected = tfcatalog.effect_of(self.tf.get(), self._db) if self._db is not None else None
        selected_name = None
        if selected is not None and self._at_effect_text(selected) == self.tf.get().strip():
            selected_name = selected.name
        for key, card in self._at_gallery_cards.items():
            is_selected = key == selected_name
            frame = card["frame"]
            frame.configure(
                highlightthickness=3 if is_selected else 1,
                highlightbackground="#2376b5" if is_selected else "#b8b8b8",
            )
            effect = card["effect"]
            suffix = _t("   · propio") if not effect.known else ""
            card["label"].configure(
                text=("✓ " if is_selected else "") +
                f"{effect.label}{suffix}\nat {self._at_effect_text(effect)}",
            )

    def _select_at_effect_card(self, effect_name: str) -> None:
        index = next(
            (i for i, effect in enumerate(self._effects) if effect.name == effect_name),
            None,
        )
        if index is None:
            return
        self.fx_list.selection_clear(0, "end")
        self.fx_list.selection_set(index)
        self.fx_list.see(index)
        self._on_effect()

    def _on_at_effect_card_enter(self, effect_name: str) -> None:
        if self._engine is None or not HAVE_TKIMG or self._at_gallery_context is None:
            return
        if effect_name == self._at_gallery_hover_key:
            return
        self._stop_at_effect_hover(render_static=True)
        effect = self._at_gallery_cards[effect_name]["effect"]
        text = self._at_effect_text(effect)
        event = self._at_effect_event(text)
        if (self._engine.lib.duration_expr(text, event) <= 0
                and not self._engine.lib.loops_expr(text, event)):
            return
        self._at_gallery_hover_key = effect_name
        self._at_gallery_hover_started = time.monotonic()
        self._render_at_effect_hover(effect_name, 0.0)
        self._at_gallery_hover_job = self.after(
            33, lambda key=effect_name: self._tick_at_effect_hover(key),
        )

    def _on_at_effect_card_leave(self, effect_name: str) -> None:
        if effect_name != self._at_gallery_hover_key or self._at_gallery_leave_job is not None:
            return
        self._at_gallery_leave_job = self.after(
            40, lambda key=effect_name: self._finish_at_effect_leave(key),
        )

    def _finish_at_effect_leave(self, effect_name: str) -> None:
        self._at_gallery_leave_job = None
        card = self._at_gallery_cards.get(effect_name)
        if card is None:
            return
        frame = card["frame"]
        x, y = self.winfo_pointerx(), self.winfo_pointery()
        inside = (frame.winfo_rootx() <= x < frame.winfo_rootx() + frame.winfo_width()
                  and frame.winfo_rooty() <= y < frame.winfo_rooty() + frame.winfo_height())
        if not inside:
            self._stop_at_effect_hover(render_static=True)

    def _stop_at_effect_hover(self, render_static: bool) -> None:
        if self._at_gallery_hover_job is not None:
            self.after_cancel(self._at_gallery_hover_job)
            self._at_gallery_hover_job = None
        if self._at_gallery_leave_job is not None:
            self.after_cancel(self._at_gallery_leave_job)
            self._at_gallery_leave_job = None
        effect_name = self._at_gallery_hover_key
        self._at_gallery_hover_key = None
        self._at_gallery_hover_photo = None
        if render_static and effect_name in self._at_gallery_cards:
            self._draw_at_effect_card(
                effect_name, self._at_effect_static_photo(self._at_gallery_cards[effect_name]["effect"]),
            )

    def _render_at_effect_hover(self, effect_name: str, elapsed: float) -> None:
        if self._engine is None:
            return
        card = self._at_gallery_cards[effect_name]
        text = self._at_effect_text(card["effect"])
        event = self._at_effect_event(text)
        duration = self._engine.lib.duration_expr(text, event)
        if duration > 0 and not self._engine.lib.loops_expr(text, event):
            elapsed %= duration
        image = self._at_effect_image(effect_name, elapsed)
        self._at_gallery_hover_photo = _photo(image, *AT_EFFECT_CARD_SIZE)
        self._draw_at_effect_card(effect_name, self._at_gallery_hover_photo)

    def _tick_at_effect_hover(self, effect_name: str) -> None:
        if effect_name != self._at_gallery_hover_key:
            return
        self._render_at_effect_hover(effect_name, time.monotonic() - self._at_gallery_hover_started)
        self._at_gallery_hover_job = self.after(
            33, lambda key=effect_name: self._tick_at_effect_hover(key),
        )

    # ------------------------------------------------------------------ efectos <-> texto
    def _defined(self) -> set:
        return set(self._known)

    def _schedule_scale(self, event=None) -> None:
        if event is not None and event.widget is not self:
            return
        if self._scale_job is not None:
            self.after_cancel(self._scale_job)
        self._scale_job = self.after(80, self._apply_scale)

    def _apply_scale(self) -> None:
        self._scale_job = None
        width, height = max(self.winfo_width(), 1), max(self.winfo_height(), 1)
        scale = max(0.65, min(1.0, width / 950, height / 700))
        if abs(scale - self._ui_scale) < 0.025:
            self.after_idle(self._settle_step2_layout)
            return
        self._ui_scale = scale
        self._preview_frame.config(width=int(250 * scale), height=int(330 * scale))
        for strip in (self.strip_face, self.strip_pose):
            strip.set_scale(scale)
        self._tf_entry.config(width=max(36, int(54 * scale)))
        self._at_preview_frame.config(
            width=int(AT_EFFECT_PREVIEW_SIZE[0] * scale),
            height=int(AT_EFFECT_PREVIEW_SIZE[1] * scale),
        )
        self.fx_help.config(wraplength=int(340 * scale))
        self._cache.clear()
        self._char_photos = []
        for tag, button in zip(self._char_tags, self._char_buttons):
            attrs = self.cat.default_preview(tag)
            photo = _photo(self.cat.image(tag, attrs), int(150 * scale), int(190 * scale)) if attrs else None
            self._char_photos.append(photo)
            button.config(image=photo if photo is not None else "")
        if self.tag.get():
            self.strip_face.refresh()
            self.strip_pose.refresh()
            self._update()
            self._refresh_at_effect_preview()
        self.after_idle(self._settle_step2_layout)

    def _on_effect(self, _e=None) -> None:
        sel = self.fx_list.curselection()
        if not sel:
            return
        eff = self._effects[sel[0]]
        text = tfcatalog.resolve(eff, self._count, self._slot, self._defined())
        self._syncing = True
        try:
            self.tf.set(text)
        finally:
            self._syncing = False
        self._show_effect_help(eff, text)
        self._update_at_effect_selection()
        self._reload_at_effect_preview()

    def _show_effect_help(self, eff, text: str) -> None:
        self.fx_help.config(text=_t("{0}\n\nSe escribe:  at {1}", eff.help, text) if eff else "")

    def _on_tf_changed(self) -> None:
        """El texto cambió (a mano o por la lista): actualiza código, selección de la lista y ventana."""
        if not self._syncing:
            eff = tfcatalog.effect_of(self.tf.get(), self._db) if self._db is not None else None
            plain = eff is not None and tfcatalog.resolve(
                eff, self._count, self._slot, self._defined()) == self.tf.get().strip()
            self.fx_list.selection_clear(0, "end")
            if plain:
                i = self._effects.index(eff)
                self.fx_list.selection_set(i)
                self.fx_list.see(i)
                self._show_effect_help(eff, self.tf.get().strip())
            else:
                self.fx_help.config(text=_t("Transform personalizado (edítalo en el campo «at»).\n\nSe escribe:  at {0}", self.tf.get().strip()))
        self._update_code()
        self._update_at_effect_selection()
        if not self._syncing:
            self._refresh_at_effect_preview()

    def _go_step2(self, tag, attrs=None) -> None:
        self.tag.set(tag)
        self.head.config(text=_t("{0}: elige rostro, pose y efecto", tag.capitalize()))
        poses = self.cat.poses(tag)
        loc = self.cat.locate(tag, attrs) if attrs is not None else None
        if loc:
            self.pose, self.expr = loc
        else:
            self.pose = "1" if "1" in poses else (poses[0] if poses else "")
            exprs = self.cat.exprs(tag, self.pose)
            self.expr = "a" if "a" in exprs else (exprs[0] if exprs else "")
        self._cache.clear()
        self.strip_pose.set_items([(p, f"{p} · {cells.outfit_label(p)}") for p in poses],
                                  self._pose_thumb, self.pose)
        self._reload_faces()
        self._on_tf_changed()
        self.step1_scroll.pack_forget()
        self.step2_scroll.pack(fill="both", expand=True)
        self.step1_actions.pack_forget()
        self.step2_actions.pack(fill="x")
        self.bind("<Return>", lambda e: self._ok())
        self._refresh_sprite_gallery()
        self._on_tf_tab_changed()
        self.after_idle(self._settle_step2_layout)

    def _back(self) -> None:
        self.step2_scroll.pack_forget()
        self.step1_scroll.pack(fill="both", expand=True)
        self.step2_actions.pack_forget()
        self.step1_actions.pack(fill="x")
        self.unbind("<Return>")
        if self._gallery is not None and self._gallery.winfo_exists():
            self._gallery.destroy()
            self._gallery = None

    def _reload_faces(self) -> None:
        tag = self.tag.get()
        exprs = self.cat.exprs(tag, self.pose)
        if self.expr not in exprs:
            self.expr = exprs[0] if exprs else ""
        self.strip_face.set_items([(e, e or _t("(única)")) for e in exprs], self._face_thumb, self.expr)
        self._update()

    def _pick_pose(self, pose) -> None:
        self.pose = pose
        self._reload_faces()
        self.strip_pose.select(pose)         # la selección también puede venir de la galería
        self._refresh_sprite_gallery()
        self._on_tf_tab_changed()

    def _pick_expr(self, expr) -> None:
        self.expr = expr
        self.strip_face.select(expr)
        self.strip_pose.refresh()           # la pose se ve con el rostro elegido
        self._update()
        self._refresh_sprite_gallery()
        self._on_tf_tab_changed()

    def _open_gallery(self, kind: str) -> None:
        if self._gallery is None or not self._gallery.winfo_exists():
            self._gallery = _SpriteGallery(self, self._select_gallery_item, self._gallery_closed)
        self._refresh_sprite_gallery()
        self._gallery.show_tab(kind)
        self._gallery.lift()

    def _gallery_closed(self) -> None:
        self._gallery = None

    def _gallery_items(self, kind: str):
        tag = self.tag.get()
        if kind == "face":
            return [(expr, expr or _t("(única)")) for expr in self.cat.exprs(tag, self.pose)]
        return [(pose, f"{pose} · {cells.outfit_label(pose)}") for pose in self.cat.poses(tag)]

    def _gallery_thumb(self, kind: str, key: str):
        tag = self.tag.get()
        if kind == "face":
            cache_key = (kind, tag, self.pose, key)
            if cache_key not in self._gallery_thumb_cache:
                image = self.cat.face_image(tag, self.pose, key) or self.cat.sprite_image(tag, self.pose, key)
                self._gallery_thumb_cache[cache_key] = _photo(image, *_SpriteGallery.FACE_THUMB_SIZE)
        else:
            expr = self.expr if self.cat.combo(tag, key, self.expr) else (
                (self.cat.exprs(tag, key) or [""])[0])
            cache_key = (kind, tag, key, expr)
            if cache_key not in self._gallery_thumb_cache:
                self._gallery_thumb_cache[cache_key] = _photo(
                    self.cat.sprite_image(tag, key, expr), *_SpriteGallery.POSE_THUMB_SIZE)
        return self._gallery_thumb_cache[cache_key]

    def _refresh_sprite_gallery(self) -> None:
        if self._gallery is None or not self._gallery.winfo_exists() or not self.tag.get():
            return
        self._gallery.title(_t("Galería · {0}", self.tag.get().capitalize()))
        for kind in ("face", "pose"):
            selected = self.expr if kind == "face" else self.pose
            self._gallery.refresh(kind, self._gallery_items(kind), selected,
                                  lambda key, tab=kind: self._gallery_thumb(tab, key))

    def _select_gallery_item(self, kind: str, key: str) -> None:
        if kind == "face":
            self._pick_expr(key)
        else:
            self._pick_pose(key)

    # ------------------------------------------------------------------ miniaturas
    def _face_thumb(self, expr):
        key = ("f", self.tag.get(), self.pose, expr)
        if key not in self._cache:
            tag = self.tag.get()
            img = self.cat.face_image(tag, self.pose, expr) or self.cat.sprite_image(tag, self.pose, expr)
            self._cache[key] = _photo(img, *self.strip_face.box)
        return self._cache[key]

    def _pose_thumb(self, pose):
        tag = self.tag.get()
        expr = self.expr if self.cat.combo(tag, pose, self.expr) else (
            (self.cat.exprs(tag, pose) or [""])[0])
        key = ("p", tag, pose, expr)
        if key not in self._cache:
            self._cache[key] = _photo(self.cat.sprite_image(tag, pose, expr), *self.strip_pose.box)
        return self._cache[key]

    # ------------------------------------------------------------------ vista previa y código
    def _attrs(self):
        return self.cat.attrs(self.tag.get(), self.pose, self.expr)

    def _zorder(self) -> int:
        try:
            return max(-99, min(99, int(self.z.get())))
        except ValueError:
            return 0

    def _update(self) -> None:
        img = self.cat.sprite_image(self.tag.get(), self.pose, self.expr)
        photo = _photo(img, int(240 * self._ui_scale), int(320 * self._ui_scale))
        self._last = photo                    # Tk descarta la imagen si no se guarda
        self.prev.config(image=photo if photo is not None else "",
                         text="" if photo is not None else _t("(sin vista previa:\nfaltan los PNG o Pillow)"))
        self._update_code()

    def _check_transform(self):
        """(lista de nombres | None, aviso). Solo es error lo que no se puede escribir tras `at`."""
        try:
            names = cells.parse_transform_list(self.tf.get())
        except ValueError as exc:
            self.tf_msg.config(text=str(exc), foreground="#b00000")
            return None
        unknown = [cells.call_name(n) for n in names if self._known and cells.call_name(n) not in self._known]
        if unknown:
            self.tf_msg.config(text=_t("No está en tu transforms.rpy: ") + ", ".join(unknown) +
                               _t(" (el preview usará la posición estándar; el script se guarda igual)."),
                               foreground="#a05000")
        else:
            self.tf_msg.config(text="", foreground="#666666")
        return names

    def _update_code(self) -> None:
        attrs = self._attrs()
        self._check_transform()
        text = f"show {self.tag.get()} {' '.join(attrs)} zorder {self._zorder()} at {self.tf.get().strip()}" if attrs else ""
        self.code.config(state="normal")
        self.code.delete(0, "end")
        self.code.insert(0, text)
        self.code.config(state="readonly")

    def _ok(self) -> None:
        attrs = self._attrs()
        if attrs is None:
            return
        if self._check_transform() is None:
            return                                   # transform imposible de escribir: no se cierra
        self.result = cells.Placement(self.tag.get(), tuple(attrs), self._zorder())
        self.transform = self.tf.get().strip()
        self.reflow = bool(self.reflow_var.get())
        self.destroy()


def run(db: rpy_parser.ScriptDB, game_dir: str) -> None:
    DialogueBrowser(db, game_dir).mainloop()
