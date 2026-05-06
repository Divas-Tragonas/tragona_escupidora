#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generador automático de recaps para sesiones de D&D desde VODs de Twitch.

Flujo:
  1. Descarga el audio del VOD con yt-dlp (convertido a mp3 128kbps)
  2. Trocea el audio en segmentos de 10 minutos con ffmpeg
  3. Transcribe cada segmento con Groq Whisper large-v3
  4. Genera el recap estructurado con Claude claude-opus-4-7
  5. Guarda el Markdown en /docs/recaps/ y actualiza /docs/recaps.json

Uso:
  python generate_recap.py --url <URL_VOD> [--titulo "Nombre sesión"] [--idioma es]

Variables de entorno requeridas:
  ANTHROPIC_API_KEY
  GROQ_API_KEY
"""

import os
import sys
import json
import time
import argparse
import subprocess
import re
import tempfile
from pathlib import Path
from datetime import datetime

import yt_dlp
import anthropic
from groq import Groq


# ──────────────────────────────────────────────
# DESCARGA DE AUDIO
# ──────────────────────────────────────────────

def descargar_audio(url_vod: str, directorio_temp: Path) -> Path:
    """
    Descarga el audio del VOD de Twitch usando yt-dlp.
    Convierte automáticamente a mp3 128kbps para mantener los segmentos
    por debajo del límite de 25MB de la API de Groq.
    """
    ruta_salida = directorio_temp / "audio.mp3"

    opciones_ydl = {
        "format": "bestaudio/best",
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "128",
        }],
        # Plantilla de salida sin extensión; yt-dlp la añade tras la conversión
        "outtmpl": str(directorio_temp / "audio.%(ext)s"),
        "quiet": False,
        "no_warnings": False,
    }

    print(f"[1/4] Descargando audio de: {url_vod}")
    with yt_dlp.YoutubeDL(opciones_ydl) as ydl:
        ydl.download([url_vod])

    if not ruta_salida.exists():
        raise FileNotFoundError(
            "yt-dlp no generó audio.mp3. "
            "Si el VOD es de suscriptores o está caducado, no se puede descargar."
        )

    tamanio_mb = ruta_salida.stat().st_size / 1024 / 1024
    print(f"    Audio descargado: {tamanio_mb:.1f} MB")
    return ruta_salida


# ──────────────────────────────────────────────
# TROCEADO CON FFMPEG
# ──────────────────────────────────────────────

def trocear_audio(ruta_audio: Path, directorio_temp: Path, duracion_seg: int = 600) -> list[Path]:
    """
    Divide el audio en segmentos de longitud fija usando ffmpeg.
    Con -c copy no re-encoda, solo busca puntos de corte en el stream mp3,
    lo que es muy rápido y sin pérdida de calidad adicional.
    """
    patron_salida = str(directorio_temp / "segmento_%03d.mp3")

    cmd = [
        "ffmpeg",
        "-i", str(ruta_audio),
        "-f", "segment",
        "-segment_time", str(duracion_seg),
        "-c", "copy",
        "-reset_timestamps", "1",
        patron_salida,
        "-y",           # sobreescribir sin preguntar
        "-loglevel", "warning",
    ]

    print(f"[2/4] Troceando audio en segmentos de {duracion_seg // 60} minutos...")
    resultado = subprocess.run(cmd, capture_output=True, text=True)
    if resultado.returncode != 0:
        raise RuntimeError(f"ffmpeg falló:\n{resultado.stderr}")

    segmentos = sorted(directorio_temp.glob("segmento_*.mp3"))
    if not segmentos:
        raise RuntimeError("ffmpeg no generó ningún segmento.")

    print(f"    {len(segmentos)} segmentos generados.")
    return segmentos


# ──────────────────────────────────────────────
# TRANSCRIPCIÓN CON GROQ
# ──────────────────────────────────────────────

def transcribir_segmento(cliente_groq: Groq, ruta_segmento: Path, idioma: str = "es") -> str:
    """
    Transcribe un único segmento de audio con Groq Whisper large-v3.
    Incluye reintentos con espera exponencial para absorber errores transitorios de la API.
    """
    max_reintentos = 3
    for intento in range(max_reintentos):
        try:
            with open(ruta_segmento, "rb") as f:
                transcripcion = cliente_groq.audio.transcriptions.create(
                    model="whisper-large-v3",
                    file=f,
                    language=idioma,
                    response_format="text",
                )
            return transcripcion
        except Exception as e:
            if intento < max_reintentos - 1:
                espera = 2 ** intento * 5   # 5s, 10s, 20s
                print(f"    Error en segmento {ruta_segmento.name}: {e}. Reintentando en {espera}s...")
                time.sleep(espera)
            else:
                raise RuntimeError(f"No se pudo transcribir {ruta_segmento.name}: {e}") from e


def transcribir_todos(cliente_groq: Groq, segmentos: list[Path], idioma: str = "es") -> str:
    """
    Transcribe todos los segmentos secuencialmente y concatena el resultado.
    Cada bloque incluye una marca de segmento para que Claude pueda orientarse en el tiempo.
    """
    print(f"[3/4] Transcribiendo {len(segmentos)} segmentos con Groq Whisper...")
    partes = []

    for i, segmento in enumerate(segmentos, 1):
        minuto_inicio = (i - 1) * 10
        print(f"    Segmento {i}/{len(segmentos)} (≈{minuto_inicio:02d}:00)...", end=" ", flush=True)
        texto = transcribir_segmento(cliente_groq, segmento, idioma)
        partes.append(f"[Segmento {i} — minuto {minuto_inicio}]\n{texto.strip()}")
        print("✓")

    transcripcion_completa = "\n\n".join(partes)
    palabras = len(transcripcion_completa.split())
    print(f"    Transcripción completa: {palabras:,} palabras.")
    return transcripcion_completa


# ──────────────────────────────────────────────
# GENERACIÓN DEL RECAP CON ANTHROPIC
# ──────────────────────────────────────────────

PROMPT_RECAP = """\
Eres el cronista oficial de una campaña de Dungeons & Dragons. Recibes la transcripción \
completa de una sesión de juego grabada en Twitch.

Tu tarea es generar un recap estructurado, detallado y entretenido en español, útil tanto \
para los jugadores que quieren repasar la sesión como para quien se la perdió.

IMPORTANTE: genera EXACTAMENTE estas secciones Markdown, en este orden, con estos encabezados:

## Resumen Ejecutivo
Párrafo de 3-5 frases. Lo más importante de la sesión de un vistazo.

## ⚔️ Combates
Lista numerada de cada encuentro: enemigos, resultado, bajas o consecuencias notables.
Si no hubo combates, escribe "Sin combates esta sesión."

## 👥 NPCs Encontrados
Lista con nombre, una línea de descripción y el papel que jugaron en la sesión.
Incluye tanto NPCs nuevos como los que reaparecieron.

## 💰 Loot y Recompensas
Todo el botín, objetos mágicos, monedas o recompensas conseguidos.
Si no hubo loot, escríbelo explícitamente.

## 🎯 Decisiones Clave
Las decisiones más importantes tomadas por el grupo (no por el DM) y sus consecuencias inmediatas o potenciales.

## 🪝 Plot Hooks
Hilos argumentales que quedaron abiertos, misterios sin resolver o semillas que el DM \
parece haber plantado para futuras sesiones.

## ✨ Momentos Memorables
Los 3-5 momentos más épicos, divertidos o emocionantes. Sé específico y descriptivo.

## 💬 Quotes Destacadas
Citas textuales memorables de jugadores o el DM. Atribúyelas si el nombre es identificable.
Formato: > "Cita" — *Nombre*

---

Si algo no se entiende bien en la transcripción, usa [inaudible] o [poco claro].
No inventes hechos; cíñete a lo que aparece en la transcripción.

TRANSCRIPCIÓN:

{transcripcion}
"""


def generar_titulo_automatico(cliente_anthropic: anthropic.Anthropic, fragmento: str) -> str:
    """
    Pide a Claude que genere un título creativo de máximo 6 palabras
    basándose en el primer fragmento de la transcripción.
    """
    respuesta = cliente_anthropic.messages.create(
        model="claude-opus-4-7",
        max_tokens=80,
        messages=[{
            "role": "user",
            "content": (
                "Basándote en este fragmento de una sesión de D&D, genera un título creativo "
                "y conciso en español (máximo 6 palabras). Solo devuelve el título, sin comillas "
                "ni explicaciones adicionales.\n\nFragmento:\n" + fragmento[:3000]
            ),
        }],
    )
    return respuesta.content[0].text.strip()


def generar_recap(cliente_anthropic: anthropic.Anthropic, transcripcion: str, titulo: str) -> str:
    """
    Llama a Claude claude-opus-4-7 para generar el recap completo.
    claude-opus-4-7 tiene una ventana de contexto de 200K tokens, suficiente
    para sesiones de D&D de hasta ~6 horas.
    """
    print("[4/4] Generando recap con Claude claude-opus-4-7...")
    respuesta = cliente_anthropic.messages.create(
        model="claude-opus-4-7",
        max_tokens=4096,
        messages=[{
            "role": "user",
            "content": PROMPT_RECAP.format(transcripcion=transcripcion),
        }],
    )
    contenido = respuesta.content[0].text

    tokens_entrada = respuesta.usage.input_tokens
    tokens_salida = respuesta.usage.output_tokens
    print(f"    Tokens usados: {tokens_entrada:,} entrada / {tokens_salida:,} salida.")
    return contenido


# ──────────────────────────────────────────────
# UTILIDADES
# ──────────────────────────────────────────────

def slugify(texto: str) -> str:
    """Convierte un texto libre a formato kebab-case seguro para nombres de archivo."""
    texto = texto.lower()
    # Reemplazar vocales con tilde
    for con_tilde, sin_tilde in [("áàäâ", "a"), ("éèëê", "e"), ("íìïî", "i"), ("óòöô", "o"), ("úùüû", "u")]:
        for c in con_tilde:
            texto = texto.replace(c, sin_tilde)
    texto = texto.replace("ñ", "n")
    texto = re.sub(r"[^a-z0-9]+", "-", texto)
    texto = texto.strip("-")
    return texto[:60]   # límite razonable de longitud


def extraer_resumen_ejecutivo(contenido_recap: str) -> str:
    """
    Extrae el texto bajo '## Resumen Ejecutivo' para usarlo como
    descripción corta en recaps.json.
    """
    lineas = contenido_recap.split("\n")
    capturando = False
    fragmentos = []

    for linea in lineas:
        if "Resumen Ejecutivo" in linea:
            capturando = True
            continue
        if capturando:
            if linea.startswith("##"):
                break
            if linea.strip():
                fragmentos.append(linea.strip())

    resumen = " ".join(fragmentos)
    return resumen[:250] + "…" if len(resumen) > 250 else resumen


def actualizar_indice(ruta_indice: Path, nueva_entrada: dict) -> None:
    """
    Inserta la nueva entrada al principio de recaps.json (más reciente primero).
    Crea el archivo si no existe.
    """
    recaps = []
    if ruta_indice.exists() and ruta_indice.stat().st_size > 2:
        with open(ruta_indice, "r", encoding="utf-8") as f:
            recaps = json.load(f)

    recaps.insert(0, nueva_entrada)

    with open(ruta_indice, "w", encoding="utf-8") as f:
        json.dump(recaps, f, ensure_ascii=False, indent=2)

    print(f"    recaps.json actualizado ({len(recaps)} entradas).")


# ──────────────────────────────────────────────
# PUNTO DE ENTRADA
# ──────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Genera recaps de sesiones de D&D desde VODs de Twitch."
    )
    parser.add_argument("--url", required=True, help="URL del VOD de Twitch")
    parser.add_argument("--titulo", default="", help="Título de la sesión (se genera automáticamente si se omite)")
    parser.add_argument("--idioma", default="es", help="Código de idioma del audio para Whisper (default: es)")
    parser.add_argument("--salida", default="docs/recaps", help="Directorio donde guardar el Markdown generado")
    parser.add_argument("--duracion-seg", type=int, default=600, help="Duración de cada segmento en segundos (default: 600)")
    args = parser.parse_args()

    # Verificar que las API keys estén presentes
    for variable in ("ANTHROPIC_API_KEY", "GROQ_API_KEY"):
        if not os.environ.get(variable):
            print(f"Error: la variable de entorno {variable} no está configurada.", file=sys.stderr)
            sys.exit(1)

    cliente_groq = Groq(api_key=os.environ["GROQ_API_KEY"])
    cliente_anthropic = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    dir_salida = Path(args.salida)
    dir_salida.mkdir(parents=True, exist_ok=True)

    # Usar directorio temporal para audio; se borra automáticamente al terminar
    with tempfile.TemporaryDirectory(prefix="dnd_recap_") as tmp:
        tmp_path = Path(tmp)

        # Paso 1: Descargar audio
        ruta_audio = descargar_audio(args.url, tmp_path)

        # Paso 2: Trocear
        segmentos = trocear_audio(ruta_audio, tmp_path, args.duracion_seg)

        # Paso 3: Transcribir
        transcripcion = transcribir_todos(cliente_groq, segmentos, args.idioma)

        # Paso 4a: Generar título si no se proporcionó
        titulo = args.titulo.strip()
        if not titulo:
            print("    Generando título automático con Claude...")
            titulo = generar_titulo_automatico(cliente_anthropic, transcripcion)
        print(f"    Título: {titulo}")

        # Paso 4b: Generar recap
        recap_cuerpo = generar_recap(cliente_anthropic, transcripcion, titulo)

    # Construir el archivo Markdown final
    fecha = datetime.now().strftime("%Y-%m-%d")
    slug = slugify(titulo)
    nombre_archivo = f"{fecha}-{slug}.md"

    encabezado_frontmatter = f"""\
---
titulo: "{titulo}"
fecha: "{fecha}"
vod_url: "{args.url}"
---

# {titulo}

*Sesión del {fecha} · [Ver VOD]({args.url})*

"""

    contenido_final = encabezado_frontmatter + recap_cuerpo

    # Guardar el Markdown
    ruta_archivo = dir_salida / nombre_archivo
    with open(ruta_archivo, "w", encoding="utf-8") as f:
        f.write(contenido_final)
    print(f"\n✓ Recap guardado en: {ruta_archivo}")

    # Actualizar el índice JSON
    resumen_corto = extraer_resumen_ejecutivo(recap_cuerpo)
    entrada_indice = {
        "id": f"{fecha}-{slug}",
        "titulo": titulo,
        "fecha": fecha,
        "archivo": f"recaps/{nombre_archivo}",
        "vod_url": args.url,
        "resumen": resumen_corto,
    }

    ruta_indice = dir_salida.parent / "recaps.json"
    actualizar_indice(ruta_indice, entrada_indice)

    print(f"✓ Proceso completado. Recap disponible en GitHub Pages en unos segundos.")


if __name__ == "__main__":
    main()
