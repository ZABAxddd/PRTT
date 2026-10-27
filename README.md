PRTT - DDLC Possing in Real Time Tool
=====================================

INSTALAR
1. Instala Python 3.8 o superior (https://www.python.org/downloads/).
   En el instalador marca "Add python.exe to PATH" y deja marcado "tcl/tk and IDLE".
2. Instala Pillow (para armar los sprites). En una consola:
       py -3 -m pip install pillow
3. Copia esta carpeta (PRTT) dentro de la carpeta /game de tu mod:
       MiMod/game/PRTT/
4. Doble clic en PRTT.bat  (o:  python prtt.py  desde la carpeta PRTT).

PRTT lee automáticamente la carpeta /game (la carpeta que contiene a PRTT) y todas sus
subcarpetas, y se ignora a sí misma. No modifica ningún archivo.

USO (etapa actual: lector de diálogos + pose en escena)
- Izquierda: archivos .rpy y sus labels. Centro: las líneas de diálogo. Arriba: buscador.
- Derecha: al elegir una línea de diálogo se muestra la pose de cada personaje en escena,
  en celdas 1..N según su posición (t11, t21, t22...). El que habla lleva una marca.
  Debajo se listan las capas de cada pose y las que faltan.
- Las poses se arman con las definiciones  image ... = im.Composite(...)  de tus .rpy
  (por ejemplo definitions.rpy). Los PNG se buscan en /game/images y en /game.
- Modo consola:
      python prtt.py --cli
      python prtt.py --cli --file script-ch1.rpy
      python prtt.py --cli --label ch1_main
      python prtt.py --cli --buscar "texto"
      python prtt.py --escena ch1_main               (fondo, celdas y poses por diálogo)
      python prtt.py --pose "sayori 1a"              (capas de la pose y si existen los archivos)
      python prtt.py --pose "sayori 1a" --png out.png  (guarda la pose armada)

LIMITACIONES CONOCIDAS
- Las imágenes deben estar como archivos PNG sueltos (no dentro de images.rpa).
- Poses definidas con im.Composite / im.LiveComposite o con una ruta simple. Otras formas
  (im.Flip, im.Scale, ATL, DynamicDisplayable) se marcan como "no soportada".
- El estado de escena se calcula dentro de cada label (no sigue jump/call todavía).

IMPORTANTE
- PRTT solo lee archivos .rpy. Si los scripts del juego siguen dentro de scripts.rpa
  (solo existen como .rpyc), primero hay que extraerlos.
- Ren'Py ignora los .py de /game, pero antes de distribuir tu mod borra la carpeta PRTT.
- La carpeta _dev contiene muestras de prueba con extensión .txt a propósito, para que
  Ren'Py no las cargue como scripts del juego.
