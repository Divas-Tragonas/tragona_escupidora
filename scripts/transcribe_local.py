#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Transcripció local de VODs de D&D amb OpenAI Whisper (sense APIs, sense cost).

Flux:
  URL de Twitch  →  yt-dlp descàrrega l'àudio  →  ffmpeg el divideix en chunks
  →  Whisper transcriu cada chunk  →  .txt llest per enganxar al wizard

Ús:
  python transcribe_local.py --url "https://www.twitch.tv/videos/1234567890"
  python transcribe_local.py --audio audio.mp3
  python transcribe_local.py --audio audio.mp3 --model large-v3

Instal·lació (una sola vegada):
  pip install openai-whisper
  sudo apt install ffmpeg      # Ubuntu/Debian
  pip install yt-dlp           # o: sudo apt install yt-dlp

Temps aproximat (CPU, sessió de 3h):
  tiny    →  ~30 min   (qualitat baixa)
  base    →  ~45 min   (qualitat acceptable)
  small   →  ~1.5 h    (bona qualitat)
  medium  →  ~3 h      (recomanat)
  large-v3 → ~6-8 h   (màxima qualitat, millor amb noms propis)
"""

import os
import sys
import json
import argparse
import subprocess
import time
from pathlib import Path
from datetime import datetime


# ── Verificació de dependències ───────────────────────────────────────────────

def verificar_dependencias(necesita_ytdlp: bool) -> None:
    errors = []

    try:
        import whisper  # noqa: F401
    except ImportError:
        errors.append(
            "openai-whisper no instal·lat.\n"
            "     Executa: pip install openai-whisper"
        )

    for cmd in ["ffmpeg", "ffprobe"]:
        if subprocess.run(["which", cmd], capture_output=True).returncode != 0:
            errors.append(
                f"{cmd} no instal·lat.\n"
                "     Executa: sudo apt install ffmpeg"
            )

    if necesita_ytdlp:
        if subprocess.run(["which", "yt-dlp"], capture_output=True).returncode != 0:
            errors.append(
                "yt-dlp no instal·lat.\n"
                "     Executa: pip install yt-dlp"
            )

    if errors:
        print("\n❌  Falten dependències:\n")
        for e in errors:
            print(f"   • {e}\n")
        sys.exit(1)


# ── Utilitats ─────────────────────────────────────────────────────────────────

def fmt_temps(segons: float) -> str:
    h = int(segons // 3600)
    m = int((segons % 3600) // 60)
    s = int(segons % 60)
    if h > 0:
        return f"{h}h {m:02d}m {s:02d}s"
    if m > 0:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def duracio_audio(ruta: Path) -> float:
    """Retorna la duració en segons d'un fitxer d'àudio via ffprobe."""
    res = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "json", str(ruta)],
        capture_output=True, text=True, check=True
    )
    return float(json.loads(res.stdout)["format"]["duration"])


# ── Descàrrega ────────────────────────────────────────────────────────────────

def descarregar_audio(url: str, directori: Path) -> Path:
    ruta = directori / "audio.mp3"

    if ruta.exists():
        mida = ruta.stat().st_size / 1024 / 1024
        print(f"✓  Àudio ja descarregat: {ruta.name} ({mida:.1f} MB) — saltant.")
        return ruta

    print(f"⬇  Descarregant àudio de: {url}")
    res = subprocess.run([
        "yt-dlp", "-x",
        "--audio-format", "mp3",
        "--audio-quality", "128K",
        "--no-playlist",
        "-o", str(directori / "audio.%(ext)s"),
        url,
    ])

    if res.returncode != 0:
        raise RuntimeError(
            "yt-dlp ha fallat. Comprova que el VOD és públic i no ha caducat."
        )
    if not ruta.exists():
        raise FileNotFoundError(
            "yt-dlp no ha generat audio.mp3. "
            "Comprova que el VOD és accessible des del navegador."
        )

    mida = ruta.stat().st_size / 1024 / 1024
    print(f"✓  Àudio descarregat: {mida:.1f} MB")
    return ruta


# ── Divisió en chunks ─────────────────────────────────────────────────────────

def dividir_audio(ruta: Path, directori: Path, seg_chunk: int) -> list[Path]:
    """
    Divideix l'àudio en chunks amb ffmpeg usant -reset_timestamps 1
    perquè cada chunk tingui metadades de durada pròpies (important
    si s'usen APIs que llegeixen el tag TLEN del ID3).
    """
    existents = sorted(directori.glob("chunk_*.mp3"))
    if existents:
        print(f"✓  Trobats {len(existents)} chunks anteriors — reanudant.")
        return existents

    try:
        total = duracio_audio(ruta)
    except Exception:
        total = None

    n_est = int(total / seg_chunk) + 1 if total else "?"
    mins  = seg_chunk // 60
    desc  = fmt_temps(total) if total else "durada desconeguda"
    print(f"✂  Dividint {desc} en chunks de {mins} min (~{n_est} chunks)…")

    res = subprocess.run([
        "ffmpeg", "-i", str(ruta),
        "-f", "segment",
        "-segment_time", str(seg_chunk),
        "-c", "copy",
        "-reset_timestamps", "1",
        str(directori / "chunk_%04d.mp3"),
        "-y", "-loglevel", "warning",
    ], capture_output=True, text=True)

    if res.returncode != 0:
        raise RuntimeError(f"ffmpeg ha fallat en dividir l'àudio:\n{res.stderr}")

    chunks = sorted(directori.glob("chunk_*.mp3"))
    if not chunks:
        raise RuntimeError("ffmpeg no ha generat cap chunk.")

    print(f"✓  {len(chunks)} chunks generats.")
    return chunks


# ── Transcripció ──────────────────────────────────────────────────────────────

def transcriure(chunks: list[Path], model_nom: str, idioma: str,
                fitxer_progres: Path) -> str:
    import whisper

    # Carregar progrés previ
    progres: dict[str, str] = {}
    if fitxer_progres.exists():
        with open(fitxer_progres, encoding="utf-8") as f:
            progres = json.load(f)
        fets = sum(1 for c in chunks if c.name in progres)
        if fets:
            print(f"↻  Reprenent — {fets}/{len(chunks)} chunks ja transcrits.\n")

    # Carregar model (una sola vegada, pot trigar uns minuts)
    pendents = [c for c in chunks if c.name not in progres]
    if not pendents:
        print("✓  Tots els chunks ja estaven transcrits.")
    else:
        print(f"🤖  Carregant model Whisper '{model_nom}'…")
        t0 = time.time()
        model = whisper.load_model(model_nom)
        print(f"✓  Model carregat en {fmt_temps(time.time() - t0)}.\n")

        durades: list[float] = []

        for idx, chunk in enumerate(chunks):
            num = idx + 1
            total = len(chunks)

            if chunk.name in progres:
                print(f"  [{num:03d}/{total}] {chunk.name} — ja transcrit, saltant.")
                continue

            mida_mb = chunk.stat().st_size / 1024 / 1024
            print(f"  [{num:03d}/{total}] {chunk.name}  ({mida_mb:.1f} MB)…",
                  end="", flush=True)
            t_inici = time.time()

            for intent in range(3):
                try:
                    resultat = model.transcribe(
                        str(chunk),
                        language=idioma,
                        verbose=False,
                        fp16=False,        # segur en CPU
                        condition_on_previous_text=True,
                    )
                    text = resultat["text"].strip()
                    break
                except Exception as exc:
                    if intent == 2:
                        raise RuntimeError(
                            f"No s'ha pogut transcriure {chunk.name} "
                            f"després de 3 intents: {exc}"
                        ) from exc
                    print(" (error, reintentant…)", end="", flush=True)
                    time.sleep(3)

            elapsed = time.time() - t_inici
            durades.append(elapsed)

            # Guardar progrés immediatament
            progres[chunk.name] = text
            with open(fitxer_progres, "w", encoding="utf-8") as f:
                json.dump(progres, f, ensure_ascii=False, indent=2)

            # ETA
            restants = total - num
            if durades and restants > 0:
                eta = fmt_temps((sum(durades) / len(durades)) * restants)
                print(f"  ✓  ({fmt_temps(elapsed)}, ETA: ~{eta})")
            else:
                print(f"  ✓  ({fmt_temps(elapsed)})")

    # Ensamblat en ordre
    parts = []
    for i, chunk in enumerate(chunks):
        text = progres.get(chunk.name, "").strip()
        if text:
            # Marca de temps aproximada basada en l'índex del chunk
            parts.append(text)

    return "\n\n".join(parts)


# ── Punt d'entrada ────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Transcriu VODs de D&D amb Whisper local (sense APIs ni costos).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
exemples:
  python transcribe_local.py --url "https://www.twitch.tv/videos/1234567890"
  python transcribe_local.py --audio audio.mp3
  python transcribe_local.py --audio audio.mp3 --model large-v3
  python transcribe_local.py --audio audio.mp3 --model medium --idioma ca
        """,
    )

    grup = parser.add_mutually_exclusive_group(required=True)
    grup.add_argument("--url",   help="URL del VOD de Twitch")
    grup.add_argument("--audio", help="Ruta a un fitxer d'àudio ja descarregat")

    parser.add_argument(
        "--model", default="medium",
        choices=["tiny", "base", "small", "medium", "large", "large-v2", "large-v3"],
        help=(
            "Model Whisper (default: medium). "
            "Més gran = més lent i millor qualitat. "
            "large-v3 reconeix millor noms propis i paraules de fantasia."
        ),
    )
    parser.add_argument(
        "--idioma", default="ca",
        help="Codi d'idioma ISO 639-1 (default: ca = català). "
             "Usar 'ca' força Whisper a transcriure en català.",
    )
    parser.add_argument(
        "--chunk", type=int, default=1200,
        help="Durada de cada chunk en segons (default: 1200 = 20 min).",
    )
    parser.add_argument(
        "--salida", default=".",
        help="Directori on guardar la transcripció (default: directori actual).",
    )

    args = parser.parse_args()

    print("\n🐉  Transcriptor local D&D — Whisper")
    print("─" * 45)

    model_temps = {
        "tiny": "~10 min", "base": "~20 min", "small": "~1 h",
        "medium": "~3 h", "large": "~6 h", "large-v2": "~6 h", "large-v3": "~6-8 h",
    }
    print(f"    Model:  {args.model}  ({model_temps.get(args.model, '?')} per 3h de sessió en CPU)")
    print(f"    Idioma: {args.idioma}")
    print()

    verificar_dependencias(necesita_ytdlp=bool(args.url))

    dir_treball = Path(args.salida) / "transcripcio_treball"
    dir_treball.mkdir(parents=True, exist_ok=True)
    fitxer_progres = dir_treball / "progres.json"

    try:
        # Pas 1 — Àudio
        if args.url:
            ruta_audio = descarregar_audio(args.url, dir_treball)
        else:
            ruta_audio = Path(args.audio)
            if not ruta_audio.exists():
                print(f"❌  No es troba el fitxer: {ruta_audio}")
                sys.exit(1)
            mida = ruta_audio.stat().st_size / 1024 / 1024
            print(f"✓  Fitxer d'àudio: {ruta_audio.name}  ({mida:.1f} MB)")

        print()

        # Pas 2 — Dividir
        chunks = dividir_audio(ruta_audio, dir_treball, args.chunk)

        print()

        # Pas 3 — Transcriure
        t_total_inici = time.time()
        transcripcio = transcriure(chunks, args.model, args.idioma, fitxer_progres)
        temps_total = time.time() - t_total_inici

        # Pas 4 — Guardar
        data = datetime.now().strftime("%Y-%m-%d")
        nom_sortida = f"transcripcio_{data}.txt"
        ruta_sortida = Path(args.salida) / nom_sortida

        with open(ruta_sortida, "w", encoding="utf-8") as f:
            f.write(transcripcio)

        paraules = len(transcripcio.split())

        print()
        print("─" * 45)
        print(f"✅  Fet en {fmt_temps(temps_total)}")
        print(f"    {paraules:,} paraules  →  {ruta_sortida}")
        print()
        print("➡  Ara ves al wizard de la web (pas 2) i enganxa el contingut")
        print(f"   d'aquest fitxer, o usa l'opció 'Enganxa transcripció'.")
        print()

    except KeyboardInterrupt:
        print("\n\n⏸  Interromput per l'usuari.")
        print(f"   El progrés s'ha guardat a: {fitxer_progres}")
        print("   Torna a executar la mateixa comanda per reprendre.")
        sys.exit(0)

    except Exception as exc:
        print(f"\n❌  Error: {exc}")
        if fitxer_progres.exists():
            print(f"   El progrés parcial s'ha guardat a: {fitxer_progres}")
            print("   Torna a executar la mateixa comanda per reprendre.")
        sys.exit(1)


if __name__ == "__main__":
    main()
