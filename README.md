# DDLC Possing in Real Time Tool (PRTT)

PRTT es una herramienta de escritorio para revisar y editar escenas de mods de **Doki Doki Literature Club!** escritos con Ren'Py. Lee los archivos `.rpy` de la carpeta `game`, muestra una vista previa de los diálogos y permite ajustar personajes, poses, fondos y transiciones sin tener que editar manualmente cada instrucción del script.

> PRTT se ejecuta por separado del juego. No es un mod que se active desde el menú de DDLC.

## Contenido

- [Funciones](#funciones)
- [Requisitos](#requisitos)
- [Instalación en un mod](#instalación-en-un-mod)
- [Abrir PRTT](#abrir-prtt)
- [Flujo de trabajo recomendado](#flujo-de-trabajo-recomendado)
- [Solución de problemas](#solución-de-problemas)
- [Modo de consola](#modo-de-consola)

## Funciones

### Diálogos y scripts

- Lee los archivos `.rpy` de la carpeta `game` y organiza los diálogos por archivo y `label`.
- Permite buscar texto y revisar el contexto de los diálogos.
- Edita texto y ayuda a mantener sincronizada la pose del personaje que habla.
- Guarda cambios en los scripts, permite deshacer y rehacer, y ofrece auto-guardado.
- Crea una copia de respaldo `.prtt-bak` la primera vez que modifica un archivo.
- Comprueba si un script cambió externamente antes de guardar, para evitar sobrescribir cambios ajenos.

### Vista previa de escenas

- Renderiza diálogos y personajes en una vista previa de 1280 × 720.
- Permite avanzar o retroceder entre diálogos, animar la escena y guardar una imagen PNG.
- Muestra y permite editar la composición de personajes en celdas, incluyendo poses, expresiones, orden de capa y efectos `at`.

### Fondos y transiciones

- Explora los fondos e imágenes definidos por el mod y permite añadir un color sólido.
- Incluye una galería de transiciones con previsualizaciones.
- Permite elegir transiciones estándar de Ren'Py y transiciones encontradas en los scripts del mod.
- Puede reemplazar, agregar o eliminar una instrucción de escena, y deshacer el último cambio del selector.

### Personajes y efectos

- Carga las poses y expresiones disponibles en los `.rpy` del mod.
- Ofrece galerías de poses y expresiones, y una galería de efectos `at`.
- Muestra previews estáticas y animadas de los efectos `at`, con control de reproducción en bucle y recarga.
- Lee los transforms y las posiciones existentes del mod cuando están disponibles.

## Requisitos

- **Windows** y Python **3.8 o posterior**.
- Tkinter, incluido en el instalador oficial de Python para Windows. Si instalas Python manualmente, selecciona **tcl/tk and IDLE**.
- **Pillow** para renderizar personajes, fondos y previews. Sin Pillow, algunas funciones gráficas no estarán disponibles.
- Los scripts `.rpy` del mod para que PRTT pueda leer diálogos, personajes, fondos y transforms.

Instala Pillow con:

```powershell
py -3 -m pip install pillow
```

## Instalación en un mod

1. Cierra DDLC y haz una copia de seguridad de tu mod.
2. Copia la carpeta completa `PRTT` dentro de la carpeta `game` del mod. La estructura debe quedar así:

   ```text
   MiMod/
   └── game/
       ├── script.rpy
       ├── definitions.rpy
       └── PRTT/
           ├── PRTT.bat
           ├── prtt.py
           ├── prtt_gui.py
           └── ...
   ```

   Conserva juntos todos los archivos de PRTT: además de los dos archivos mostrados, la herramienta utiliza otros módulos Python de esa carpeta.

3. Comprueba que la instalación está completa abriendo PowerShell o Símbolo del sistema en `game\PRTT` y ejecutando:

   ```powershell
   py -3 check_install.py
   ```

4. Si el diagnóstico indica que falta Pillow, instálalo usando el comando de [Requisitos](#requisitos).

PRTT busca por defecto los archivos `.rpy` en la carpeta `game` que contiene a `PRTT` y excluye sus propios archivos al leer. **No muevas `PRTT` fuera de `game`** si quieres que esa detección automática funcione.

## Abrir PRTT

En Windows, haz doble clic en `game\PRTT\PRTT.bat`.

También puedes abrir una terminal en esa carpeta y ejecutar:

```powershell
py -3 prtt.py
```

La ventana principal presenta las pestañas **Diálogos** y **Preview**. Selecciona un diálogo y abre **Preview** para revisar la escena; desde ahí puedes abrir los selectores de personajes y escenarios.

Si quieres analizar otra instalación del mod, especifica su carpeta `game`:

```powershell
py -3 prtt.py --game "C:\ruta\al\mod\game"
```

## Flujo de trabajo recomendado

1. Guarda una copia de seguridad del mod antes de hacer cambios.
2. Abre PRTT desde la carpeta `game\PRTT`.
3. Elige un `label` y un diálogo en **Diálogos**.
4. Ve a **Preview** y ajusta personajes, poses, expresiones, efectos, fondos o transiciones.
5. Revisa el resultado y guarda con **Guardar en .rpy** o `Ctrl+S`. También puedes activar **Auto-guardar**.
6. Ejecuta el mod en Ren'Py para confirmar el resultado dentro del juego.

Al modificar un `.rpy`, PRTT conserva una copia con el sufijo `.prtt-bak`. Si avisa que el archivo cambió desde que se abrió, usa **Recargar .rpy**, revisa de nuevo la escena y vuelve a guardar.

## Solución de problemas

### No se abre la ventana

- Confirma que Python 3.8 o posterior está instalado.
- En Windows, prueba `py -3 prtt.py` desde `game\PRTT` para ver el mensaje de error.
- Si Tkinter no está disponible, modifica la instalación de Python e incluye **tcl/tk and IDLE**.

### No aparecen personajes o imágenes

- Instala Pillow: `py -3 -m pip install pillow`.
- Comprueba que los archivos del personaje y sus definiciones están presentes en el mod.
- Ejecuta `py -3 check_install.py` para verificar los archivos de PRTT.

### No se encuentran diálogos o contenido del juego

- Verifica que `PRTT` esté dentro de la carpeta `game` correcta.
- PRTT lee archivos `.rpy`; no lee directamente scripts que sigan empaquetados dentro de archivos `.rpa`. Si el mod solo incluye scripts empaquetados, extráelos primero usando una herramienta compatible con Ren'Py y trabaja sobre una copia del mod.

### Un cambio no aparece en el juego

- Guarda los cambios en el `.rpy` y vuelve a cargar o ejecutar el proyecto en Ren'Py.
- Comprueba que abriste la carpeta `game` del mod correcto.
- Si el script cambió externamente mientras PRTT estaba abierto, recárgalo antes de guardar.

## Modo de consola

PRTT también incluye utilidades de consulta que se pueden ejecutar sin abrir la interfaz:

```powershell
py -3 prtt.py --cli
py -3 prtt.py --cli --buscar "texto para buscar"
py -3 prtt.py --cli --label ch1_main
py -3 prtt.py --escena ch1_main
py -3 prtt.py --catalogo sayori
```

Para ver todas las opciones:

```powershell
py -3 prtt.py --help
```
