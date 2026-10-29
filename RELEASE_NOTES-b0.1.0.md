# PRTT b0.1.0 beta

Esta versión beta mejora el flujo de trabajo para editar y revisar escenas de mods de DDLC desde una interfaz visual.

## Cambios

- **Previsualización de personajes:** mayor consistencia al recorrer diálogos y revisar poses, expresiones, transforms y composición de escena.
- **Fondos y transiciones:** consulta el escenario asociado al diálogo y previsualiza cambios de fondo y transiciones que PRTT puede interpretar.
- **Edición de colocación:** mueve personajes entre celdas, controla el orden de capa y guarda los cambios en los scripts `.rpy` con copia de seguridad.
- **Herramientas de música:** explora pistas, escucha el audio y edita `play music`/`stop music` con fades y bucles.
- **Música en Preview:** escucha la instrucción musical asociada al diálogo seleccionado, siguiendo el orden textual de los scripts.
- **Paquete portable:** se añadió el sistema de empaquetado para incluir la aplicación y sus dependencias, sin requerir que el usuario instale Python.
- **Instalación documentada:** el [README](./README.md) prioriza la función de colocación de personajes y explica cómo instalar la versión portable o ejecutar desde el código fuente.

## Instalación

1. Descarga el ZIP de PRTT para tu sistema desde la sección **Assets** de este release.
2. Extrae la carpeta `PRTT` dentro de la carpeta `game` de tu mod, junto a los archivos `.rpy`.
3. En Windows, inicia `PRTT.bat`. En Linux o macOS, inicia `./PRTT.sh` desde una terminal.
4. Selecciona un diálogo, abre **Preview**, coloca los personajes y guarda con **Guardar en .rpy** o `Ctrl+S`.

La versión portable incluye Python y las dependencias de PRTT. Si ejecutas desde el código fuente, consulta la guía de instalación completa en el [README](./README.md).

## Alcance y limitaciones

Las previsualizaciones interpretan scripts y recursos del mod, pero no ejecutan el motor de Ren'Py ni resuelven ramas (`if`, `jump`, `call`) o `queue music`. Verifica siempre el resultado en Ren'Py. Los scripts que solo estén dentro de archivos `.rpa` deben extraerse para que PRTT pueda leerlos.
