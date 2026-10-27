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

PESTAÑA PREVIEW (nueva)
- Al elegir un diálogo en «Diálogos», pasa a la pestaña «Preview»: dibuja la escena a 1280x720
  como en el juego: fondo, sprites en su posición (según tu transforms.rpy), textbox, caja del
  nombre y texto con la fuente y outline de tu mod.
- Teclas: Enter o → = siguiente diálogo, ← = anterior. «Animar» reproduce la entrada/movimiento
  (tcommon, focus, hop, sink, leftin...). «Guardar PNG» guarda lo que ves. «Ajustes leídos» muestra
  qué medidas de la caja de texto se leyeron de tus .rpy y cuáles son POR DEFECTO (suposiciones).
- Fondos: se leen de tus  image bg ... = "..."  ("#000000", rutas, LiveTile, bloques con choice).
- Consola:
      python prtt.py --preview ch1_main --n 3 --png salida.png
      python prtt.py --conf                      (ajustes de la caja de texto y de dónde salieron)
- Los avisos (fuente, imagen o transform que no se encuentran) salen bajo la imagen.
- La fuente (gui/font/...) y los PNG de gui/ deben estar como archivos sueltos; si no, se usa una
  fuente de reserva y una caja de reemplazo.

ELEGIR PERSONAJES EN LAS CELDAS (pestaña Preview)
- Clic en una celda vacía: paso 1 = elige personaje (Sayori, Yuri, Natsuki, Monika; con su sprite 1);
  paso 2 = elige ROSTRO y POSE en tiras de 1 fila x 3 columnas (◀ ▶ pasan de 3 en 3), la posición
  (at: t11, t21, t22, t31...) y el zorder (Z). Abajo se ve el código resultante.
- Las poses y expresiones se leen de tus definiciones  image personaje pose = im.Composite(...)
  (definitions.rpy). La pose sale del nombre (1, 2, 3, 4, 5; 1b, 2b... = casual) y el rostro es
  la capa de cara (a.png, b.png...). Solo se ofrecen combinaciones que existen en tus .rpy.
  Comprobación en consola:   python prtt.py --catalogo            (todos)
                             python prtt.py --catalogo sayori
- El transform se elige por EFECTO (ver «TRANSFORMS POR EFECTO» más abajo); la celda es la que clicaste.
- Clic en una celda ocupada: cambiar rostro/pose, intercambiar, mover o quitar.
- «Copiar script» copia, por ejemplo:   show sayori 1a zorder 1 at t11


ATAJOS DE TECLADO Y TRANSFORM LIBRE
- Ctrl+Z = deshacer · Ctrl+Y o Ctrl+Mayús+Z = rehacer · Ctrl+S = guardar. Funcionan en cualquier
  pestaña (si estás en otra, te lleva a Preview para ver el cambio) y con Bloq Mayús activado.
  Si el foco está escribiendo en un campo de texto (buscador, Z), Ctrl+Z/Y son de ese campo;
  con el selector de personaje abierto no actúan sobre el historial.
- En el selector de personaje, la pestaña «Avanzado (texto)» es un campo EDITABLE: escribe lo que
  quieras tras el `at` (t45, hf32, mi_pos, "t21, hop", hop(x=400, z=0.9)...). Solo se rechaza lo que
  no se puede escribir tras un `at` (espacios sueltos, signos, paréntesis sin cerrar, palabras
  reservadas). Si el nombre no está en tu transforms.rpy se avisa, pero se guarda igual.
- Casilla «Reajustar a los demás personajes»: marcada, una posición tipo t21/hf32 mueve al resto
  como siempre (t11 -> t21...). Desmarcada, o con un nombre propio, el transform se aplica tal
  cual en la celda clicada y no se toca a nadie más.
- Al mover/intercambiar un personaje de celda, su transform libre vuelve al estándar de la nueva celda.
- Al reabrir, PRTT conserva lo que el guion ya decía (hf21, "at t21, hop", transforms propios);
  antes una re-edición de ese diálogo lo reescribía como t21.

GUARDADO EN LOS .rpy (reemplazo inteligente, sin comentarios)
- PRTT ya no escribe bloques «# PRTT:begin/end». Al guardar un diálogo mira las instrucciones que
  hay entre el diálogo anterior y ese: si ya existe un `show` del personaje, REEMPLAZA esa línea
  (conservando su `with`, `behind`, `onlayer` y tu comentario); si no, inserta una nueva tras el
  último show/hide/scene. Volver a guardar reemplaza las mismas líneas: nunca duplica.
- Lo que no cambió no se escribe: un personaje que ya está exactamente así desde un diálogo anterior
  no genera ninguna línea.
- Sintaxis de pose:
    · solo cambia la pose de quien HABLA ........ s 1b "texto"        (se sobreescribe su línea)
    · solo cambia la pose de otro personaje ..... show monika 1b       (sin zorder ni at)
    · cambia zorder / at, o entra en escena ..... show sayori 1b zorder 2 at t21   (antes del diálogo)
  Si la línea de diálogo del que habla ya llevaba pose, se mantiene sincronizada.
- Comentarios: NINGUNO por defecto. La casilla «Comentarios # PRTT en el script» (arriba, se recuerda
  en prtt_settings.json) añade «  # PRTT» al final de las líneas que PRTT escribe.
- Deshacer (Ctrl+Z) incluso después de guardar devuelve el script EXACTAMENTE al original (PRTT
  recuerda el tramo original mientras está abierto). Si editaste ese tramo a mano entremedias, avisa
  y no lo toca (queda la copia archivo.rpy.prtt-bak).
- Los bloques «# PRTT:begin/end» de versiones anteriores se retiran al volver a guardar ese diálogo.
- Una instrucción en varias líneas (con «\» o con bloque ATL) no se reescribe a medias: se avisa.

EDITAR DIÁLOGOS (pestaña Diálogos)
- Elige una o varias líneas (Ctrl/Mayús+clic) y pulsa «✎ Editar diálogo», F2 o doble clic. Se
  muestran tal como están en el script, una por línea; edítalas (comillas: \"  ·  salto: \n) y
  «Aplicar al script» (Ctrl+Enter). Se sustituye SOLO esa línea física (sangría, saltos de línea y
  tu comentario # se conservan), se valida con el propio parser y la lista, el índice, el detalle y
  el preview se actualizan. No se añaden ni quitan diálogos.

CELDAS (pestaña Preview)
- «Personajes:» es ahora un desplegable compacto en la barra superior (0 = ninguno). Al bajar la
  cantidad se quitan solos los personajes de la DERECHA (4 -> 1 deja solo la celda 1), sin alertas:
  Ctrl+Z lo devuelve.
- Arrastra una celda ocupada con el ratón a otra. Si el destino está ocupado, su personaje se
  desplaza a la celda vacía más próxima. Un clic sin mover sigue abriendo el menú de la celda.


TRANSFORMS POR EFECTO, ANIMACIÓN Y MODO AVANZADO (selector de personaje)
- La lista ya no enseña t11, t21, t22, h21, i31... (decenas de variantes repetidas). Enseña los
  EFECTOS de tu transforms.rpy con su nombre: tcommon, tinstant, focus, hop, hop focus, sink, dip,
  panic, left in, right in, thide, left hide, right hide y, al final, el resto de transforms propios
  de tu mod («· propio»). Los nombres del tipo tXY no aparecen.
- Al elegir un efecto, PRTT escribe la variante de la celda donde está el personaje: «hop» en la
  celda 1 de 2 escribe `at h21`; «hop focus» en la 2 de 3, `at hf32`; «panic» o «thide», su nombre
  (no tienen variantes por celda). Al reabrir un personaje, el efecto de su transform queda marcado
  (h21 -> hop). El texto exacto que se escribirá se ve en la línea `show ...` de abajo.
- Pestaña «Avanzado (texto)»: el transform en texto original, tal como va tras `at`. Admite nombres
  (t45, mi_pos), varios separados por comas (t21, hop) y llamadas con parámetros que no están en la
  lista: hop(x=400, z=0.9), tcommon(300). Lo que escribas manda y se guarda tal cual; la lista de
  efectos y el texto están sincronizados en los dos sentidos.
- «▶ Previsualizar animación…» (también en el menú de una celda ocupada: «Previsualizar su
  animación…») abre una ventana que dibuja SOLO a ese personaje, con la pose y el rostro elegidos,
  sobre el fondo de la escena:
    · Entrada (show) · Cambio de celda (replace, «desde la celda» N) · Salida (hide)
    · ▶ Reproducir (o barra espaciadora), ■ Parar, Bucle, velocidad 0.25× a 2×
    · la barra de tiempo se puede arrastrar para ver cualquier instante
    · si cambias el texto del transform en el selector, la ventana se actualiza sola
  Usa el mismo motor ATL que el preview principal. Los bucles infinitos (panic) se enseñan 4 s. La
  salida solo se anima si el transform tiene un bloque `on hide` (thide, lhide, rhide); el resto
  simplemente desaparece. Varios transforms (t21, hop) se aplican en orden y el último pisa lo que toca.
- Aviso: un transform con nombre propio y SIN posición tXY (mi_pos solo) no dice en qué celda está;
  al reabrir PRTT lo coloca en la primera celda libre. Para conservar la celda usa «t22, mi_pos».
