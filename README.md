# DDLC Possing in Real Time Tool

**PRTT** es una herramienta gráfica para organizar y ajustar la colocación de personajes en mods de **Doki Doki Literature Club!**. Su función principal es permitirte ver una escena y acomodar a sus personajes de forma visual e inmediata, sin tener que calcular manualmente las posiciones `at` de Ren'Py.

Selecciona un diálogo, abre la vista previa y trabaja directamente con las celdas de personajes: agrega o cambia personajes, elige poses y expresiones, mueve sus posiciones y ajusta sus efectos. Cuando termines, PRTT puede escribir la disposición en el archivo `.rpy` correspondiente.

> PRTT es una aplicación independiente para Windows. No se instala dentro del juego como un mod activable desde el menú de DDLC; se coloca junto a los scripts del mod para leerlos y guardar los cambios.

## Contenido

- [Qué puedes hacer](#qué-puedes-hacer)
- [Requisitos](#requisitos)
- [Instalar PRTT en tu mod](#instalar-prtt-en-tu-mod)
- [Organización del código](#organización-del-código)
- [Iniciar la herramienta](#iniciar-la-herramienta)
- [Colocar personajes](#colocar-personajes)
- [Guardar los cambios](#guardar-los-cambios)
- [Música](#música)
- [Herramientas adicionales](#herramientas-adicionales)
- [Solución de problemas](#solución-de-problemas)
- [Uso desde la consola](#uso-desde-la-consola)

## Qué puedes hacer

### Colocación visual de personajes

- Previsualiza el diálogo actual y la composición de personajes en una escena de 1280 × 720.
- Elige cuántos personajes mostrar y en qué celda colocarlos.
- Selecciona personajes, poses y expresiones desde galerías visuales.
- Arrastra personajes entre celdas, intercámbialos, quítalos o cambia su posición.
- Haz clic derecho en una celda para abrir inmediatamente sus opciones. Al quitar un personaje, puedes quitar solo su efecto y mantenerlo en escena, o quitarlo de la escena para que PRTT escriba `hide [personaje]`.
- Ajusta el orden de capa (`zorder`) para decidir qué personaje queda delante.
- Prueba efectos de entrada y transformaciones `at` con previews; activa el bucle cuando quieras revisar una animación.
- Las previews de transforms que solo definen `on hide` reproducen su animación de salida en vez de quedarse estáticas.
- Al elegir un transform de salida que tenga `on hide` (por ejemplo, `thide`, `lhide` o `rhide`), PRTT muestra la animación y escribe el `hide [personaje]` después de su `show`.
- Si quitas solo el efecto, PRTT conserva la posición del personaje y elimina el `hide` asociado. Si quitas de escena un personaje que ya estaba presente, conserva su salida `show ... at [transform on hide]` + `hide`; si era una adición nueva, elimina el par que ya no se necesita.
- Avanza y retrocede por los diálogos para comprobar la composición en contexto; al avanzar, el preview reproduce transiciones reconocidas, incluidas las `MultipleTransition` con `Dissolve`, `ImageDissolve`, `Pause` y `Solid`. `ImageDissolve` necesita que su máscara esté disponible como archivo dentro de `game`. Si una transición depende de código de Ren'Py o del mod que PRTT no puede interpretar, no inventa un efecto equivalente y avisa en pantalla.

Los cambios de colocación se reflejan en la vista previa al instante. PRTT usa las definiciones y transforms que encuentra en los `.rpy` del mod para interpretar poses y posiciones.

### Herramientas complementarias

- Navega por archivos, `label` y líneas de diálogo; también puedes buscar texto y editar diálogos.
- Consulta el fondo y la transición que acompañan al diálogo. El selector de escenarios ofrece galerías y previsualizaciones para trabajar con fondos y transiciones disponibles en los scripts.
- En las previews de transición, al mantener seleccionado el fondo actual se muestra el fondo anterior hacia el actual; si no hay fondo anterior, se usa gris. Al seleccionar otro fondo, se previsualiza la transición desde el fondo actual hacia el nuevo.
- Elige la música de cada diálogo: gráfica de la pista con fade-in, fade-out y bucle, vista previa de audio y escritura de `play music` / `stop music` en el `.rpy`.
- En la pestaña **Preview**, activa **♫ Reproducir música del guion** para escuchar la instrucción `play music` o `stop music` vigente al diálogo seleccionado, incluidos los puntos de bucle y fades del script.

Esta escucha sigue las instrucciones en el orden textual de los `.rpy`; no ejecuta el intérprete de Ren'Py ni resuelve ramas (`if`, `jump`, `call`) o `queue music`.
- Guarda una captura de la vista previa como PNG.
- Usa deshacer y rehacer, y activa el auto-guardado para las ediciones compatibles.

La colocación gráfica de personajes es el propósito central de PRTT; las herramientas para diálogos y escenarios son funciones auxiliares.

## Requisitos

- **Windows**.
- Python **3.8 o posterior**.
- Tkinter, incluido en el instalador oficial de Python para Windows. Si instalas Python manualmente, selecciona **tcl/tk and IDLE**.
- **Pillow**, necesario para componer sprites y mostrar previews gráficas.
- **pygame-ce**, necesario para escuchar la música en la ventana «Música» (`python -m pip install -r requirements-audio.txt`).
- Los scripts `.rpy` del mod y los recursos de imagen de los personajes que quieras previsualizar.

Instala Pillow desde PowerShell o Símbolo del sistema:

```powershell
py -3 -m pip install pillow
```

Si ejecutas PRTT con un entorno virtual, actívalo antes de instalar pygame-ce para que el lanzador use ese mismo entorno.

## Instalar PRTT en tu mod

1. Cierra DDLC y crea una copia de seguridad del mod.
2. Copia la carpeta completa `PRTT` dentro de la carpeta `game` del mod. Debe quedar junto a los scripts del juego:

   ```text
   MiMod/
   └── game/
       ├── script.rpy
       ├── definitions.rpy
       ├── transforms.rpy
       └── PRTT/
           ├── PRTT.bat
           ├── prtt.py
           ├── prtt_gui.py
           └── ...
   ```

   Mantén todos los archivos de PRTT dentro de esa carpeta; la aplicación utiliza varios módulos Python, no solo `prtt.py`.

3. Abre una terminal en `game\PRTT` y ejecuta el comprobador de archivos:

   ```powershell
   py -3 check_install.py
   ```

4. Si el comprobador informa que Pillow no está instalado, ejecuta el comando indicado en [Requisitos](#requisitos).

Por defecto, PRTT lee los scripts `.rpy` de la carpeta `game` que contiene a `PRTT` y excluye sus propios archivos. Por eso, **no coloques `PRTT` en la carpeta raíz del juego ni dentro de otra carpeta distinta de `game`**.

## Organización del código

Los módulos se agrupan por responsabilidad para que cada área del proyecto tenga un lugar claro:

- `rpy_parser.py` lee los scripts Ren'Py; `rpy_writer.py` guarda cambios en ellos.
- `dialogue.py` indexa, busca y prepara la edición de diálogos.
- `prtt_gui.py` contiene la ventana principal y su historial de deshacer/rehacer; `scene_gui.py`, la ventana de escenarios.
- `scene_edit.py` analiza y modifica escenarios, y coordina sus operaciones; `scenes.py` recopila fondos y `transitions.py` describe transiciones.
- `preview.py`, `stage.py`, `sprites.py` y `cells.py` cubren la vista previa y la composición de personajes.
- `audio.py` detecta pistas, duración y onda y contiene el reproductor; `audio_edit.py` lee y escribe `play music` / `stop music`; `audio_gui.py` es la ventana «Música».
- `guiconf.py` lee la configuración visual de Ren'Py y contiene el ajuste del tamaño y la posición de las ventanas.

Mantén estos módulos junto a `PRTT.bat`; la herramienta los importa al iniciarse.

## Iniciar la herramienta

En Windows, haz doble clic en:

```text
game\PRTT\PRTT.bat
```

También puedes abrir una terminal en `game\PRTT` y ejecutar:

```powershell
py -3 prtt.py
```

La herramienta se ejecuta en una ventana separada del juego. Para abrir otro mod o una carpeta `game` distinta, puedes especificar la ruta:

```powershell
py -3 prtt.py --game "C:\ruta\al\mod\game"
```

## Colocar personajes

1. En **Diálogos**, elige el `label` y el diálogo cuya composición quieres ajustar.
2. Abre la pestaña **Preview** para ver el contexto visual del diálogo.
3. Usa el selector de cantidad de personajes y selecciona una celda en la imagen.
4. Abre la selección de personaje para elegir una pose o expresión. Puedes arrastrar personajes entre celdas para reorganizar la composición.
5. Ajusta el orden de capa o abre **Efectos** para probar un efecto `at` y revisar su animación.
6. Avanza o retrocede entre diálogos para comprobar que la colocación funciona en el momento deseado.
7. Guarda cuando el resultado visual sea el que buscas.

## Guardar los cambios

Al guardar, PRTT actualiza las instrucciones de personaje necesarias en los scripts `.rpy`. Antes de modificar un archivo, crea una copia de respaldo con el sufijo `.prtt-bak`. También comprueba si el archivo fue cambiado externamente desde que se cargó; si detecta cambios, solicita recargar antes de guardar para evitar sobrescribirlos.

Puedes guardar con **Guardar en .rpy** o `Ctrl+S`. El botón **Deshacer** y `Ctrl+Z` revierten ediciones; **Rehacer** y `Ctrl+Y` las recuperan. El auto-guardado se puede activar o desactivar en la interfaz.

Después de guardar, ejecuta o recarga el proyecto en Ren'Py para comprobar el resultado dentro del juego. PRTT es una herramienta de edición y previsualización externa; no modifica el juego mientras está ejecutándose.

## Música

El botón **🎵 Música…** (pestañas Diálogos y Preview) abre una ventana para elegir la pista que suena en el diálogo seleccionado y escribir su `play music` o `stop music` en el `.rpy`.

**De dónde salen las pistas.** PRTT lee `definitions.rpy` y todos los scripts: cada `define audio.t1 = "bgm/1.ogg"` es una pista. Como nombre se usa el comentario de esa definición (`define audio.t1 = "bgm/1.ogg"  # Doki Doki Literature Club!` o una línea `# Título` justo encima); si no hay comentario, se usa el nombre de la variable (`t1`). También aparecen las rutas sueltas que tus scripts reproducen (`play music "bgm/x.ogg"`) y las pistas que usan pero nadie define, marcadas como «sin definir».

**Gráfica interactiva.** Muestra la línea de tiempo de la canción con su onda (si PRTT puede leerla), el bucle (`<loop N>` de la definición) y la curva del fade. Arrastra el círculo naranja para ajustar el fade; un clic en la gráfica fija desde dónde escuchar. El selector **Vista** acerca el tramo inicial para ajustar con precisión.

**Pestaña Reproducir música**
- **Fade-in** y **Bucle (loop)**: generan `play music t1 fadein 1.0`. La música de Ren'Py ya se repite sola, así que con «Bucle» marcado no se escribe nada extra y, al desmarcarlo, se escribe `noloop`. Un fade-in de 0 omite la cláusula.
- **Reemplazar música actual** reescribe el `play music` que manda en ese diálogo (conserva tu comentario y cláusulas como `volume`). **Agregar nueva música** inserta otra línea encima del diálogo sin tocar la anterior; si justo encima ya hay una instrucción de música, PRTT lo avisa porque la nueva la sustituirá al instante.

**Pestaña Detener música**
- Muestra la misma gráfica con la curva de **Fade-out** y escribe `stop music fadeout 1.0`. La línea roja indica en qué momento de la canción cae el stop (solo afecta a la vista previa).
- Al agregar o reemplazar un `stop music`, PRTT averigua qué pista está deteniendo (la última `play music` anterior en el label) y la muestra y usa en la gráfica. Si no hay ninguna, o ya la detuvo otro `stop`, lo avisa. La opción *Anotar en el script qué pista detiene* añade `# detiene: Título` a la línea (apagada por defecto).

**Escuchar.** Para oír la vista previa, con fade-in, fade-out y bucle, instala `pygame` (`py -3 -m pip install pygame`). Sin él, la gráfica y la escritura funcionan igual. La duración se lee de los `.ogg`, `.wav` y `.mp3` sin instalar nada, y la forma de onda de los `.wav`; para ver la onda de `.ogg` o `.mp3` hace falta `pygame` o `soundfile` (con `numpy`). Si los audios siguen dentro de un `.rpa`, extráelos primero.

Los cambios usan las mismas protecciones que el resto de PRTT: copia `.prtt-bak`, escritura atómica, aviso si el archivo cambió fuera y **Deshacer último cambio** en la propia ventana.

## Herramientas adicionales

- **Diálogos:** explorar archivos y `label`, buscar texto y editar líneas.
- **Preview:** recorrer diálogos y guardar una imagen PNG de referencia.
- **Escenarios:** consultar y previsualizar fondos y transiciones disponibles en el mod; incluye transiciones estándar de Ren'Py.
- **Música:** elegir pistas por su título, ajustar fades y bucle sobre una gráfica y escribir `play music` / `stop music`.
- **Catálogo de personajes:** ver las poses y expresiones que PRTT pudo leer de las definiciones del proyecto.

## Solución de problemas

### PRTT no se inicia

- Comprueba que Python 3.8 o posterior está instalado.
- Ejecuta `py -3 prtt.py` desde `game\PRTT` para ver errores en la terminal.
- Si Tkinter no está disponible, modifica la instalación de Python e incluye **tcl/tk and IDLE**.

### No aparecen las previews de personajes

- Instala Pillow con `py -3 -m pip install pillow`.
- Verifica que las definiciones de imagen y los PNG del personaje existen en el mod.
- Ejecuta `py -3 check_install.py` para comprobar los archivos de PRTT.

### PRTT no encuentra los scripts o los personajes

- Confirma que copiaste `PRTT` dentro de la carpeta `game` correcta.
- PRTT lee archivos `.rpy`; no lee directamente scripts que sigan dentro de archivos `.rpa`. Si el mod solo incluye scripts empaquetados, extráelos con una herramienta compatible con Ren'Py y trabaja sobre una copia del mod.
- Algunas poses y posiciones dependen de las definiciones presentes en `definitions.rpy`, `transforms.rpy` u otros scripts del proyecto.

### El resultado guardado no aparece en el juego

- Confirma que PRTT está apuntando a la carpeta `game` del mod que ejecutas.
- Guarda los cambios y vuelve a cargar o ejecutar el proyecto en Ren'Py.
- Si el script cambió externamente durante la sesión, recárgalo en PRTT y revisa de nuevo la colocación.

## Uso desde la consola

PRTT también incluye utilidades de consulta que pueden ejecutarse sin abrir la interfaz:

```powershell
py -3 prtt.py --cli
py -3 prtt.py --cli --buscar "texto para buscar"
py -3 prtt.py --cli --label ch1_main
py -3 prtt.py --escena ch1_main
py -3 prtt.py --catalogo sayori
py -3 prtt.py --musica
```

Para consultar todas las opciones:

```powershell
py -3 prtt.py --help
```
