# 🎭 PRTT — DDLC Posing in Real Time Tool

> **Coloca, previsualiza y guarda poses de personajes de DDLC directamente en los scripts de tu mod… ¡en tiempo real!**

**PRTT** es una herramienta gráfica e independiente para **posicionar y editar poses y expresiones de los personajes de *Doki Doki Literature Club!*** dentro de las escenas de tus mods.

Elige poses → ajusta posiciones → organiza capas → revisa fondos y música → guarda en tus scripts `.rpy`.

> ⚠️ **PRTT no es un mod de DDLC.** Es una aplicación externa que edita tus scripts RPY. Comprueba siempre el resultado final en Ren'Py antes de distribuir tu mod.

---

# ✨ ¿Qué puedes hacer?

### 🎭 Personajes
Elige poses y expresiones definidas en tu mod, cambia su ubicación, organiza quién va delante y previsualiza efectos y *transforms*.

### 🖼️ Fondos y escenarios
Consulta los fondos y transiciones asociados a cada diálogo y previsualiza los cambios de escena disponibles.

### 🎵 Música
Selecciona pistas, escúchalas en vista previa y edita instrucciones `play music` / `stop music` con *fades* y bucles.

### 💬 Diálogos
Navega por archivos y *labels* para encontrar rápidamente la escena que quieres editar.

### 💾 Guardado protegido
PRTT crea una copia `.prtt-bak` antes de guardar y te avisa si el archivo fue modificado externamente.

### 💿 Auto-Guardado
PRTT tiene un sistema de autoguardo que puede ser activado y desactivado a preferencia del usuario y la cual tiene un cooldown de 4 segundos. Perfecto para no interrumpir tu trabajo con pausas para guardar.

> 🧠 **Nota:** las previsualizaciones interpretan los scripts y recursos disponibles, pero **no ejecutan el motor de Ren'Py** ni las ramas del juego.

---

# 🚀 Instalación (versión portable)

La versión portable incluye **el ejecutable, Python y todas las dependencias**. No necesitas instalar Python por separado. 🎉

## 1️⃣ Descarga

Descarga el ZIP de tu sistema desde **[Releases](https://github.com/ZABAxddd/PRTT/releases)**.
Usa **solo** una arquitectura publicada allí.

## 2️⃣ Coloca PRTT dentro de la carpeta `game` de tu mod

Haz una copia de seguridad del mod y extrae la carpeta `PRTT` dentro de `game`, junto a los scripts `.rpy`:

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

> 📌 **Importante:** coloca PRTT dentro de `game`, **no** junto al ejecutable del juego. Así puede encontrar los scripts, imágenes y fondos de tu mod.
>
> Si tus scripts están dentro de un `.rpa`, **extráelos primero** y trabaja sobre una copia del mod.

## 3️⃣ Inicia la herramienta

| Sistema | Cómo iniciar |
| :--- | :--- |
| 🪟 **Windows** | Doble clic en `game\PRTT\PRTT.bat` o en `game\PRTT\PRTT.exe`|
| 🐧 **Linux** | Terminal en `game/PRTT` → `./PRTT.sh` |
| 🍎 **macOS** | Abre `PRTT.sh` desde una terminal |

> macOS puede avisar que la aplicación no está firmada. Descarga solo builds de este repositorio.

## 4️⃣ ¡A trabajar!

Elige el diálogo → abre **Preview** → coloca los personajes → guarda con **Guardar en .rpy** o `Ctrl+S`. ✅

---

# 🛠️ Uso desde el código fuente

Necesitas **Python 3.8 o superior** y **Tkinter**.
En Windows, instala Python incluyendo **tcl/tk and IDLE**.

**Instala las dependencias:**

```powershell
py -3 -m pip install -r requirements-runtime.txt
```

**Inicia la interfaz:**

```powershell
py -3 prtt.py
```

**O abre una carpeta `game` concreta:**

```powershell
py -3 prtt.py --game "C:\ruta\al\mod\game"
```

> 🧩 **Dependencias opcionales:**
>
> | Dependencia | Para qué sirve |
> | :--- | :--- |
> | **Pillow** | Composición de sprites |
> | **pygame-ce** | Escucha de música |
> | **NumPy** y **SoundFile** | Mejor lectura de ondas de audio |

> (nota, si estas ejecutando la versión portable, no hace falta instalar nada salvo la herramienta en el juego a modificar)

---

# 🎨 Colocar personajes paso a paso

1. En **Diálogos**, selecciona el archivo, el `label` y el diálogo que quieres editar.
2. Abre **Preview** y elige una celda de personaje.
3. Selecciona el personaje y una de las poses o expresiones disponibles en el mod.
4. **Arrastra** los personajes para reorganizarlos; ajusta su capa y prueba efectos si hace falta.
5. Recorre los diálogos cercanos para verificar la escena y **guarda** cuando estés conforme. 💾

> 🎭 Las poses y expresiones dependen de las definiciones presentes en tu mod. **PRTT no incluye los recursos de personajes del juego.**

---

# 🖼️ Fondos y 🎵 música

### Escenarios
Consulta fondos y transiciones reconocidos en los scripts y revisa sus previsualizaciones.

### Música
Elige una pista para el diálogo y ajusta *fade* y bucle.

También puedes activar **♫ Reproducir música del guion** en Preview para escuchar la instrucción musical del diálogo seleccionado.

> ⚠️ La vista musical sigue el **orden escrito** en los scripts: no ejecuta condiciones, saltos, llamadas ni `queue music` de Ren'Py.
>
> Si los audios están dentro de un `.rpa`, extráelos para poder escucharlos.

---

# 💾 Guardar y comprobar

- Antes de editar un archivo, PRTT conserva una copia con el sufijo **`.prtt-bak`**.
- Si detecta que el script cambió externamente, te pedirá **recargarlo** para no sobrescribir cambios ajenos.
- Después de guardar, **abre o recarga el proyecto en Ren'Py** y verifica allí la escena y el audio.

> PRTT edita los scripts, pero **no modifica el juego mientras está en ejecución**.

---

# 🧯 Solución de problemas

| Problema | Solución |
| :--- | :--- |
| ❌ **No encuentra scripts** | Confirma que `PRTT` está dentro de la carpeta `game` correcta y que los `.rpy` están extraídos. |
| ❌ **No aparecen personajes o poses** | Revisa las definiciones y recursos del mod; si ejecutas desde código fuente, instala las dependencias indicadas. |
| 🔇 **No se escucha la música desde el código fuente** | Instala `pygame-ce` con `py -3 -m pip install -r requirements-audio.txt`. La versión portable ya incluye el reproductor. |
| 👀 **El preview difiere del juego** | PRTT no ejecuta el intérprete de Ren'Py; confirma el resultado final en el propio juego. |

---

# 📜 Resumen rápido

```text
✅ Elige diálogo
   → 🎭 Coloca personajes
   → 👀 Previsualiza
   → 💾 Guarda en .rpy
   → 🎮 Comprueba en Ren'Py
```

**PRTT** te ahorra horas de prueba y error colocando poses a mano. ¡Dale alas a tu mod de DDLC! 🚀💖
