# DDLC Possing in Real Time Tool (PRTT)

PRTT es una herramienta gráfica para **colocar personajes de Doki Doki Literature Club! en escenas de mods**. Permite elegir sus poses y expresiones, ajustar su posición y revisar el resultado visualmente antes de guardar los cambios en los scripts `.rpy`.

El trabajo principal se hace en la pestaña **Preview**: selecciona un diálogo, acomoda los personajes en las celdas y avanza por la escena para comprobar cada composición. El cambio de fondos y la música son funciones complementarias. PRTT es una aplicación independiente: no se instala como un mod dentro de DDLC.

## Funciones principales

- **Personajes:** selecciona las poses y expresiones que el mod define, cambia su ubicación, organiza qué personaje aparece delante y previsualiza efectos y transforms.
- **Fondos y escenarios:** consulta los fondos y transiciones asociados a los diálogos y previsualiza los cambios de escena disponibles.
- **Música:** selecciona pistas, escucha una vista previa y edita instrucciones `play music` y `stop music`, con fades y bucles.
- **Diálogos:** navega por archivos y labels para ubicar la escena que quieres editar.
- **Guardado protegido:** PRTT crea una copia `.prtt-bak` antes de guardar y avisa si el archivo fue modificado externamente.

Las previsualizaciones interpretan los scripts y recursos disponibles, pero no ejecutan el motor de Ren'Py ni las ramas del juego. Comprueba los cambios en Ren'Py antes de distribuir tu mod.

## Instalación recomendada: versión portable

La versión portable incluye el ejecutable, Python y las dependencias de PRTT; no necesitas instalar Python por separado.

1. Descarga el ZIP correspondiente a tu sistema desde [Releases](https://github.com/ZABAxddd/PRTT/releases). Utiliza únicamente una arquitectura que esté publicada allí.
2. Haz una copia de seguridad del mod y extrae la carpeta `PRTT` dentro de su carpeta `game`, junto a los scripts `.rpy`:

   ```text
   MiMod/
   └── game/
       ├── script.rpy
       ├── definitions.rpy
       └── PRTT/
           ├── PRTT.bat
           ├── PRTT.exe
           └── ...
   ```

3. Inicia la herramienta:
   - **Windows:** haz doble clic en `game\PRTT\PRTT.bat`.
   - **Linux:** abre una terminal en `game/PRTT` y ejecuta `./PRTT.sh`.
   - **macOS:** abre `PRTT.sh` desde una terminal. macOS puede avisar que la aplicación no está firmada; descarga solo builds de este repositorio.
4. En PRTT, elige el diálogo y abre **Preview** para colocar los personajes. Guarda con **Guardar en .rpy** o `Ctrl+S`.

Coloca PRTT dentro de `game`, no junto al ejecutable del juego. Así puede encontrar los scripts, imágenes y fondos del mod. Si los scripts están únicamente dentro de archivos `.rpa`, extráelos primero y trabaja sobre una copia del mod.

## Uso desde el código fuente

Para desarrollar o ejecutar PRTT desde Python, necesitas Python 3.8 o posterior y Tkinter. En Windows, instala Python incluyendo **tcl/tk and IDLE**.

Desde una terminal abierta en la carpeta `PRTT`, instala las dependencias:

```powershell
py -3 -m pip install -r requirements-runtime.txt
```

Inicia la interfaz con:

```powershell
py -3 prtt.py
```

Para abrir una carpeta `game` específica:

```powershell
py -3 prtt.py --game "C:\ruta\al\mod\game"
```

Pillow se usa para componer sprites. pygame-ce habilita la escucha de música; NumPy y SoundFile mejoran la lectura de ondas de audio.

## Colocar personajes

1. Selecciona en **Diálogos** el archivo, el `label` y el diálogo que quieres editar.
2. Abre **Preview** y elige una celda de personaje.
3. Selecciona el personaje y una de las poses o expresiones disponibles en el mod.
4. Arrastra los personajes para reorganizarlos; ajusta su capa y prueba los efectos si hace falta.
5. Recorre los diálogos cercanos para verificar la escena y guarda cuando estés conforme.

Las poses y expresiones dependen de las definiciones presentes en el mod. PRTT no incluye los recursos de personajes del juego.

## Fondos y música

La pestaña de escenarios permite consultar fondos y transiciones reconocidos en los scripts y revisar sus previsualizaciones.

En **Música**, elige una pista para el diálogo y ajusta opciones de fade y bucle. También puedes activar **♫ Reproducir música del guion** en Preview para escuchar la instrucción musical correspondiente al diálogo seleccionado.

La vista musical sigue el orden escrito en los scripts; no ejecuta condiciones, saltos, llamadas ni `queue music` de Ren'Py. Si los audios están dentro de un `.rpa`, extráelos para poder escucharlos.

## Guardar y comprobar

Antes de editar un archivo, PRTT conserva una copia con el sufijo `.prtt-bak`. Si detecta que el script cambió externamente, solicita recargarlo para evitar sobrescribir cambios ajenos.

Después de guardar, abre o recarga el proyecto en Ren'Py y verifica allí la escena y el audio. PRTT edita los scripts, pero no modifica el juego mientras está en ejecución.

## Solución de problemas

- **No encuentra scripts:** confirma que `PRTT` está dentro de la carpeta `game` correcta y que los archivos `.rpy` están extraídos.
- **No aparecen personajes o poses:** revisa las definiciones y recursos del mod; para la ejecución desde código fuente, instala las dependencias indicadas arriba.
- **No se escucha la música desde el código fuente:** instala `pygame-ce` con `py -3 -m pip install -r requirements-audio.txt`. La versión portable incluye el reproductor.
- **El preview difiere del juego:** PRTT no ejecuta el intérprete de Ren'Py; confirma el resultado final en el propio juego.

## Notas de versión

Consulta [RELEASE_NOTES-b0.1.0.md](./RELEASE_NOTES-b0.1.0.md) para los cambios de esta beta.
