#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generador automàtic de recaps per a sessions de D&D des de VODs de Twitch.

Flux:
  1. Descarrega l'àudio del VOD amb yt-dlp (convertit a mp3 128kbps)
  2. Transcriu el fitxer sencer amb AssemblyAI Universal-2 (suport Català natiu)
  3. Genera el recap estructurat amb Claude
  4. Desa el Markdown a /docs/recaps/ i actualitza /docs/recaps.json

Ús:
  python generate_recap.py --url <URL_VOD> [--titulo "Nom sessió"] [--idioma ca]

Variables d'entorn requerides:
  ANTHROPIC_API_KEY
  ASSEMBLYAI_API_KEY
"""

import os
import sys
import json
import argparse
import re
import tempfile
from pathlib import Path
from datetime import datetime

import yt_dlp
import anthropic
import assemblyai as aai


# ──────────────────────────────────────────────
# DESCÀRREGA D'ÀUDIO
# ──────────────────────────────────────────────

def descargar_audio(url_vod: str, directorio_temp: Path) -> Path:
    """
    Descarrega l'àudio del VOD de Twitch usant yt-dlp.
    Converteix a mp3 128kbps. Rev.ai accepta fitxers grans (fins a 5 GB),
    de manera que no cal fragmentar l'àudio.
    """
    ruta_salida = directorio_temp / "audio.mp3"

    opciones_ydl = {
        "format": "bestaudio/best",
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "128",
        }],
        "outtmpl": str(directorio_temp / "audio.%(ext)s"),
        "quiet": False,
        "no_warnings": False,
    }

    print(f"[1/3] Descarregant àudio de: {url_vod}")
    with yt_dlp.YoutubeDL(opciones_ydl) as ydl:
        ydl.download([url_vod])

    if not ruta_salida.exists():
        raise FileNotFoundError(
            "yt-dlp no ha generat audio.mp3. "
            "Si el VOD és de subscriptors o ha caducat, no es pot descarregar."
        )

    tamanio_mb = ruta_salida.stat().st_size / 1024 / 1024
    print(f"    Àudio descarregat: {tamanio_mb:.1f} MB")
    return ruta_salida


# ──────────────────────────────────────────────
# TRANSCRIPCIÓ AMB ASSEMBLYAI
# ──────────────────────────────────────────────

def transcribir_con_assemblyai(ruta_audio: Path, idioma: str = "ca") -> str:
    """
    Transcriu el fitxer d'àudio sencer amb AssemblyAI Universal-2.

    Universal-2 suporta Català (ca) en la categoria d'alta precisió (≤10% WER).
    Universal-3 Pro no suporta Català — no usar-lo com a fallback per a ca.
    El SDK gestiona l'upload, el polling i els errors automàticament.
    Cost aproximat: $0.15/h (~$0.90/mes per 6h).
    """
    aai.settings.api_key = os.environ["ASSEMBLYAI_API_KEY"]

    mida_mb = ruta_audio.stat().st_size / 1024 / 1024
    print(f"[2/3] Enviant {mida_mb:.1f} MB a AssemblyAI (Universal-2, idioma: {idioma})…")
    print(f"    Sessió de 3h tarda ~10-15 min. El SDK gestiona l'espera.")

    config = aai.TranscriptionConfig(
        speech_models=["universal-2"],
        language_code=idioma,
    )

    transcript = aai.Transcriber(config=config).transcribe(str(ruta_audio))

    if transcript.status == aai.TranscriptStatus.error:
        raise RuntimeError(f"AssemblyAI error: {transcript.error}")

    paraules = len(transcript.text.split())
    print(f"    Transcripció completa: {paraules:,} paraules.")
    return transcript.text


# ──────────────────────────────────────────────
# GENERACIÓ DEL RECAP AMB ANTHROPIC
# ──────────────────────────────────────────────

PROMPT_RECAP = """\
Ets el cronista oficial d'una campanya de Dungeons & Dragons. Reps la transcripció \
completa d'una sessió de joc gravada a Twitch.

La sessió és en {idioma_nom}. Genera el recap en {idioma_nom}, útil tant \
per als jugadors que volen repassar la sessió com per a qui se la va perdre.

IMPORTANT: genera EXACTAMENT aquestes seccions Markdown, en aquest ordre, amb aquests encapçalaments:

## Resum Executiu
Paràgraf de 3-5 frases. El més important de la sessió d'un cop d'ull.

## ⚔️ Combats
Llista numerada de cada encontre: enemics, resultat, baixes o conseqüències notables.
Si no hi ha hagut combats, escriu "Sense combats en aquesta sessió."

## 👥 NPCs Trobats
Llista amb nom, una línia de descripció i el paper que han jugat en la sessió.
Inclou tant NPCs nous com els que han reaparegut.

## 💰 Botí i Recompenses
Tot el botí, objectes màgics, monedes o recompenses aconseguides.
Si no hi ha hagut botí, escriu-ho explícitament.

## 🎯 Decisions Clau
Les decisions més importants preses pel grup (no pel DM) i les seves conseqüències immediates o potencials.

## 🪝 Plot Hooks
Fils argumentals que han quedat oberts, misteris sense resoldre o llavors que el DM \
sembla haver plantat per a futures sessions.

## ✨ Moments Memorables
Els 3-5 moments més èpics, divertits o emocionants. Sigues específic i descriptiu.

## 💬 Cites Destacades
Cites textuals memorables de jugadors o el DM. Atribueix-les si el nom és identificable.
Format: > "Cita" — *Nom*

---

Si alguna cosa no s'entén bé en la transcripció, usa [inaudible] o [poc clar].
No inventis fets; cenyeix-te al que apareix en la transcripció.

TRANSCRIPCIÓ:

{transcripcion}
"""

IDIOMES = {
    "ca": "català",
    "es": "castellà",
    "en": "anglès",
    "fr": "francès",
    "de": "alemany",
    "pt": "portuguès",
    "it": "italià",
}


def generar_titulo_automatico(cliente_anthropic: anthropic.Anthropic, fragmento: str, idioma: str = "ca") -> str:
    idioma_nom = IDIOMES.get(idioma, idioma)
    respuesta = cliente_anthropic.messages.create(
        model="claude-opus-4-7",
        max_tokens=80,
        messages=[{
            "role": "user",
            "content": (
                f"Basant-te en aquest fragment d'una sessió de D&D, genera un títol creatiu "
                f"i concís en {idioma_nom} (màxim 6 paraules). Retorna només el títol, sense cometes "
                f"ni explicacions addicionals.\n\nFragment:\n" + fragmento[:3000]
            ),
        }],
    )
    return respuesta.content[0].text.strip()


def generar_recap(cliente_anthropic: anthropic.Anthropic, transcripcion: str, titulo: str, idioma: str = "ca") -> str:
    idioma_nom = IDIOMES.get(idioma, idioma)
    print("[3/3] Generant recap amb Claude…")
    respuesta = cliente_anthropic.messages.create(
        model="claude-opus-4-7",
        max_tokens=4096,
        messages=[{
            "role": "user",
            "content": PROMPT_RECAP.format(
                transcripcion=transcripcion,
                idioma_nom=idioma_nom,
            ),
        }],
    )
    contenido = respuesta.content[0].text

    tokens_entrada = respuesta.usage.input_tokens
    tokens_salida = respuesta.usage.output_tokens
    print(f"    Tokens usats: {tokens_entrada:,} entrada / {tokens_salida:,} sortida.")
    return contenido


# ──────────────────────────────────────────────
# UTILITATS
# ──────────────────────────────────────────────

def slugify(texto: str) -> str:
    texto = texto.lower()
    for con_tilde, sin_tilde in [("áàäâ", "a"), ("éèëê", "e"), ("íìïî", "i"), ("óòöô", "o"), ("úùüû", "u")]:
        for c in con_tilde:
            texto = texto.replace(c, sin_tilde)
    texto = texto.replace("ñ", "n").replace("ç", "c").replace("·", "l")
    texto = re.sub(r"[^a-z0-9]+", "-", texto)
    texto = texto.strip("-")
    return texto[:60]


def extraer_resumen_ejecutivo(contenido_recap: str) -> str:
    lineas = contenido_recap.split("\n")
    capturando = False
    fragmentos = []

    for linea in lineas:
        if "Resum Executiu" in linea or "Resumen Ejecutivo" in linea:
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
    recaps = []
    if ruta_indice.exists() and ruta_indice.stat().st_size > 2:
        with open(ruta_indice, "r", encoding="utf-8") as f:
            recaps = json.load(f)

    recaps.insert(0, nueva_entrada)

    with open(ruta_indice, "w", encoding="utf-8") as f:
        json.dump(recaps, f, ensure_ascii=False, indent=2)

    print(f"    recaps.json actualitzat ({len(recaps)} entrades).")


# ──────────────────────────────────────────────
# PUNT D'ENTRADA
# ──────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Genera recaps de sessions de D&D des de VODs de Twitch."
    )
    parser.add_argument("--url", required=True, help="URL del VOD de Twitch")
    parser.add_argument("--titulo", default="", help="Títol de la sessió (es genera automàticament si s'omet)")
    parser.add_argument("--idioma", default="ca", help="Codi d'idioma per a Rev.ai (default: ca = català)")
    parser.add_argument("--salida", default="docs/recaps", help="Directori on desar el Markdown generat")
    args = parser.parse_args()

    for variable in ("ANTHROPIC_API_KEY", "ASSEMBLYAI_API_KEY"):
        if not os.environ.get(variable):
            print(f"Error: la variable d'entorn {variable} no està configurada.", file=sys.stderr)
            sys.exit(1)

    cliente_anthropic = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    dir_salida = Path(args.salida)
    dir_salida.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="dnd_recap_") as tmp:
        tmp_path = Path(tmp)

        ruta_audio = descargar_audio(args.url, tmp_path)
        transcripcion = transcribir_con_assemblyai(ruta_audio, args.idioma)

        titulo = args.titulo.strip()
        if not titulo:
            print("    Generant títol automàtic amb Claude…")
            titulo = generar_titulo_automatico(cliente_anthropic, transcripcion, args.idioma)
        print(f"    Títol: {titulo}")

        recap_cuerpo = generar_recap(cliente_anthropic, transcripcion, titulo, args.idioma)

    fecha = datetime.now().strftime("%Y-%m-%d")
    slug = slugify(titulo)
    nombre_archivo = f"{fecha}-{slug}.md"

    encabezado_frontmatter = f"""\
---
titol: "{titulo}"
data: "{fecha}"
vod_url: "{args.url}"
idioma: "{args.idioma}"
---

# {titulo}

*Sessió del {fecha} · [Veure VOD]({args.url})*

"""

    contenido_final = encabezado_frontmatter + recap_cuerpo

    ruta_archivo = dir_salida / nombre_archivo
    with open(ruta_archivo, "w", encoding="utf-8") as f:
        f.write(contenido_final)
    print(f"\n✓ Recap desat a: {ruta_archivo}")

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

    print(f"✓ Procés completat. Recap disponible a GitHub Pages en uns segons.")


if __name__ == "__main__":
    main()
