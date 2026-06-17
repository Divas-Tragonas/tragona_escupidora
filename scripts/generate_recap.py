#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Transcriptor automàtic de sessions de D&D des de VODs de Twitch.

Flux:
  1. Descarrega l'àudio del VOD amb yt-dlp (convertit a mp3 128kbps)
  2. Transcriu el fitxer sencer amb AssemblyAI Universal-2 (suport Català natiu)
  3. Desa la transcripció a /docs/transcripts/YYYY-MM-DD.txt

La generació del recap es fa manualment: copia la transcripció,
pega-la al wizard (Pas 2 → Enganxar transcripció) i segueix des d'allà.

Ús:
  python generate_recap.py --url <URL_VOD> [--titol "Nom sessió"] [--idioma ca]

Variables d'entorn requerides:
  ASSEMBLYAI_API_KEY
"""

import os
import sys
import re
import argparse
import tempfile
from pathlib import Path
from datetime import datetime

import yt_dlp
import assemblyai as aai


# ──────────────────────────────────────────────
# DESCÀRREGA D'ÀUDIO
# ──────────────────────────────────────────────

def descargar_audio(url_vod: str, directorio_temp: Path) -> Path:
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

    print(f"[1/2] Descarregant àudio de: {url_vod}")
    with yt_dlp.YoutubeDL(opciones_ydl) as ydl:
        ydl.download([url_vod])

    if not ruta_salida.exists():
        raise FileNotFoundError(
            "yt-dlp no ha generat audio.mp3. "
            "Comprova que el VOD és públic i no ha caducat."
        )

    mida_mb = ruta_salida.stat().st_size / 1024 / 1024
    print(f"    Àudio descarregat: {mida_mb:.1f} MB")
    return ruta_salida


# ──────────────────────────────────────────────
# TRANSCRIPCIÓ AMB ASSEMBLYAI
# ──────────────────────────────────────────────

def transcribir(ruta_audio: Path, idioma: str = "ca") -> str:
    """
    Transcriu el fitxer sencer amb AssemblyAI Universal-2.
    Universal-2 suporta Català (ca) en la categoria d'alta precisió (≤10% WER).
    El SDK gestiona l'upload, el polling i els errors automàticament.
    """
    aai.settings.api_key = os.environ["ASSEMBLYAI_API_KEY"]

    mida_mb = ruta_audio.stat().st_size / 1024 / 1024
    print(f"[2/2] Enviant {mida_mb:.1f} MB a AssemblyAI (Universal-2, idioma: {idioma})…")
    print(f"    Sessió de 3h tarda ~10-15 min.")

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
# UTILITATS
# ──────────────────────────────────────────────

def slugify(texto: str) -> str:
    texto = texto.lower()
    for con_tilde, sin_tilde in [("áàäâ", "a"), ("éèëê", "e"), ("íìïî", "i"), ("óòöô", "o"), ("úùüû", "u")]:
        for c in con_tilde:
            texto = texto.replace(c, sin_tilde)
    texto = texto.replace("ñ", "n").replace("ç", "c").replace("·", "l")
    texto = re.sub(r"[^a-z0-9]+", "-", texto)
    return texto.strip("-")[:50]


# ──────────────────────────────────────────────
# PUNT D'ENTRADA
# ──────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Transcriu sessions de D&D des de VODs de Twitch amb AssemblyAI."
    )
    parser.add_argument("--url",    required=True, help="URL del VOD de Twitch")
    parser.add_argument("--titol",  default="",    help="Títol de la sessió (opcional)")
    parser.add_argument("--idioma", default="ca",  help="Codi d'idioma per a AssemblyAI (default: ca)")
    parser.add_argument("--salida", default="docs/transcripts", help="Directori on desar la transcripció")
    args = parser.parse_args()

    if not os.environ.get("ASSEMBLYAI_API_KEY"):
        print("Error: la variable d'entorn ASSEMBLYAI_API_KEY no està configurada.", file=sys.stderr)
        sys.exit(1)

    dir_salida = Path(args.salida)
    dir_salida.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="dnd_transcript_") as tmp:
        tmp_path = Path(tmp)
        ruta_audio = descargar_audio(args.url, tmp_path)
        transcripcio = transcribir(ruta_audio, args.idioma)

    fecha = datetime.now().strftime("%Y-%m-%d")
    titol = args.titol.strip()
    slug  = slugify(titol) if titol else "sessio"
    nom_fitxer = f"{fecha}-{slug}.txt"

    ruta_fitxer = dir_salida / nom_fitxer
    with open(ruta_fitxer, "w", encoding="utf-8") as f:
        if titol:
            f.write(f"# {titol}\n")
        f.write(f"# Data: {fecha}\n")
        f.write(f"# VOD: {args.url}\n")
        f.write(f"# Idioma: {args.idioma}\n\n")
        f.write(transcripcio)

    paraules = len(transcripcio.split())
    print(f"\n✓ Transcripció desada a: {ruta_fitxer}")
    print(f"  {paraules:,} paraules")
    print(f"\nSeguent pas: ves al wizard → Nova Sessió → Pas 2 → 'Enganxar transcripció'")

    # Escriure el path per al workflow de GitHub Actions
    print(f"::set-output name=transcript_file::{ruta_fitxer}")
    print(f"::set-output name=transcript_slug::{fecha}-{slug}")


if __name__ == "__main__":
    main()
