#!/usr/bin/env python3
"""
YouTube Video Downloader — Shorts Factory
Descarga videos/Shorts de YouTube para análisis de nicho.
Uso: python tools/download_video.py <URL> [-o carpeta] [-q calidad] [-a solo-audio]
"""

import argparse
import sys
import subprocess
import json
from pathlib import Path


def check_yt_dlp():
    try:
        subprocess.run(["yt-dlp", "--version"], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("Instalando yt-dlp...")
        subprocess.run([sys.executable, "-m", "pip", "install", "yt-dlp"], check=True)


def get_video_info(url: str) -> dict:
    result = subprocess.run(
        ["yt-dlp", "--dump-json", "--no-playlist", url],
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)


def download_video(url: str, output_path: str = "output/reference",
                   quality: str = "best", format_type: str = "mp4",
                   audio_only: bool = False) -> bool:
    check_yt_dlp()
    Path(output_path).mkdir(parents=True, exist_ok=True)

    cmd = ["yt-dlp"]

    if audio_only:
        cmd.extend(["-x", "--audio-format", "mp3", "--audio-quality", "0"])
    else:
        if quality == "best":
            fmt = "bestvideo+bestaudio/best"
        elif quality == "worst":
            fmt = "worstvideo+worstaudio/worst"
        else:
            height = quality.replace("p", "")
            fmt = f"bestvideo[height<={height}]+bestaudio/best[height<={height}]"
        cmd.extend(["-f", fmt, "--merge-output-format", format_type])

    cmd.extend(["-o", f"{output_path}/%(title)s.%(ext)s", "--no-playlist"])
    cmd.append(url)

    try:
        info = get_video_info(url)
        dur = info.get("duration", 0)
        print(f"Título   : {info.get('title', '?')}")
        print(f"Duración : {dur // 60}:{dur % 60:02d}")
        print(f"Canal    : {info.get('uploader', '?')}")
        print(f"Destino  : {output_path}\n")

        subprocess.run(cmd, check=True)
        print("\n✅ Descarga completada.")
        return True
    except subprocess.CalledProcessError as e:
        print(f"\n❌ Error: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Descarga videos de YouTube para Shorts Factory")
    parser.add_argument("url", help="URL del video o Short de YouTube")
    parser.add_argument("-o", "--output", default="output/reference", help="Carpeta destino")
    parser.add_argument("-q", "--quality", default="best",
                        choices=["best", "1080p", "720p", "480p", "360p", "worst"])
    parser.add_argument("-f", "--format", default="mp4", choices=["mp4", "webm", "mkv"])
    parser.add_argument("-a", "--audio-only", action="store_true", help="Solo audio MP3")
    args = parser.parse_args()

    ok = download_video(args.url, args.output, args.quality, args.format, args.audio_only)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
