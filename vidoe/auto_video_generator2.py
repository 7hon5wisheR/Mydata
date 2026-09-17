"""
Auto Video Generator (Offline Pipeline)
========================================
Generate video pendek (Shorts/Reels) otomatis dari naskah teks:
  1. Text-to-Speech narasi (Edge-TTS, gratis)
  2. AI-generated background image (Pollinations.ai, gratis, tanpa API key)
  3. Efek Ken Burns (zoom/pan) pada gambar
  4. Subtitle otomatis sinkron ke audio (dari word-timestamp Edge-TTS)
  5. Render video final ke folder output/ -> tinggal didownload/dipakai

TIDAK melakukan auto-upload ke platform manapun. Output = file .mp4 lokal.

Cara pakai:
  1. Isi folder scripts/ dengan file .txt, satu file = satu naskah video.
     Nama file akan jadi nama video, contoh: scripts/fakta_luar_angkasa.txt
  2. pip install -r requirements.txt
  3. python auto_video_generator.py
  4. Video jadi ada di folder output/
"""

import asyncio
import glob
import os
import re
import textwrap
import time
import urllib.parse
import urllib.request

import PIL.Image

# Patch kompatibilitas: Pillow baru sudah hapus Image.ANTIALIAS,
# tapi moviepy 1.0.3 masih memanggilnya. Tambahkan alias biar tetap jalan.
if not hasattr(PIL.Image, "ANTIALIAS"):
    PIL.Image.ANTIALIAS = PIL.Image.Resampling.LANCZOS

import edge_tts
from deep_translator import GoogleTranslator
from moviepy.editor import (
    AudioFileClip,
    CompositeVideoClip,
    ImageClip,
    TextClip,
    concatenate_videoclips,
)

# =========================
# KONFIGURASI
# =========================
SCRIPTS_DIR = "scripts"          # folder berisi naskah .txt (1 file = 1 video)
OUTPUT_DIR = "output"            # folder hasil video
VOICE = "id-ID-ArdiNeural"       # suara narator (coba juga: id-ID-GadisNeural)
VIDEO_SIZE = (1080, 1920)        # 9:16 untuk Shorts/Reels
IMAGE_PER_VIDEO = 4              # jumlah segmen gambar per video
FONT_SIZE = 60
ZOOM_RATIO = 1.06                # kekuatan efek ken burns


def ensure_dirs():
    os.makedirs(SCRIPTS_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs("_tmp", exist_ok=True)


def split_script_into_segments(text, n_segments):
    """Bagi naskah jadi N bagian buat cari prompt gambar per bagian."""
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    sentences = [s for s in sentences if s]
    if len(sentences) <= n_segments:
        return sentences + [sentences[-1]] * (n_segments - len(sentences))
    chunk = len(sentences) // n_segments
    return [
        " ".join(sentences[i * chunk : (i + 1) * chunk]) or sentences[-1]
        for i in range(n_segments)
    ]


def make_fallback_image(out_path, seed=0):
    """Bikin background gradient polos kalau AI image gagal didownload."""
    from PIL import Image

    palettes = [
        ((20, 20, 40), (80, 40, 120)),
        ((10, 40, 60), (40, 120, 140)),
        ((40, 20, 20), (120, 60, 40)),
        ((20, 40, 20), (40, 120, 80)),
    ]
    top, bottom = palettes[seed % len(palettes)]
    w, h = 1080, 1920
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        t = y / h
        r = int(top[0] + (bottom[0] - top[0]) * t)
        g = int(top[1] + (bottom[1] - top[1]) * t)
        b = int(top[2] + (bottom[2] - top[2]) * t)
        for x in range(w):
            px[x, y] = (r, g, b)
    img.save(out_path)
    return out_path


def translate_to_prompt(text_id, retries=2):
    """Terjemahkan naskah Indonesia ke Inggris + tambah gaya visual,
    biar AI image generator (dilatih mayoritas teks Inggris) lebih paham
    konteksnya dan hasil gambar nyambung ke isi naskah."""
    text_en = text_id
    for attempt in range(1, retries + 1):
        try:
            text_en = GoogleTranslator(source="id", target="en").translate(text_id[:400])
            break
        except Exception as e:
            print(f"  (translate percobaan {attempt}/{retries} gagal: {e})")
            time.sleep(3)
    return f"{text_en}, cinematic, high detail, digital art, no text, no watermark"


def download_ai_image(prompt, out_path, seed=None, retries=3):
    """Ambil gambar AI gratis dari Pollinations.ai berdasarkan prompt teks.
    Kalau gagal (403/timeout/dll) setelah beberapa kali coba, pakai gambar
    gradient fallback biar video tetap jadi."""
    prompt = translate_to_prompt(prompt)
    q = urllib.parse.quote(prompt[:250])
    seed_part = f"&seed={seed}" if seed is not None else ""
    url = f"https://image.pollinations.ai/prompt/{q}?width=1080&height=1920{seed_part}&nologo=true"

    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0 Safari/537.36"
                    ),
                    "Accept": "image/*",
                },
            )
            with urllib.request.urlopen(req, timeout=30) as resp, open(out_path, "wb") as f:
                f.write(resp.read())
            return out_path
        except Exception as e:
            print(f"  (percobaan {attempt}/{retries} gagal ambil AI image: {e})")
            time.sleep(2)

    print("  -> pakai gambar fallback (gradient) karena AI image gagal terus.")
    return make_fallback_image(out_path, seed or 0)


async def generate_audio_and_words(text, out_mp3):
    """Generate audio TTS + ambil timestamp tiap kata (buat subtitle sinkron)."""
    communicate = edge_tts.Communicate(text, VOICE)
    words = []
    with open(out_mp3, "wb") as f:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                words.append(
                    {
                        "text": chunk["text"],
                        "start": chunk["offset"] / 10_000_000,  # -> detik
                        "dur": chunk["duration"] / 10_000_000,
                    }
                )
    return words


def words_to_caption_chunks(words, max_chars=40):
    """Gabung kata jadi potongan caption pendek (max_chars) lengkap dengan waktu tampil."""
    chunks = []
    buf, start = [], None
    for w in words:
        if start is None:
            start = w["start"]
        buf.append(w["text"])
        if len(" ".join(buf)) >= max_chars:
            end = w["start"] + w["dur"]
            chunks.append({"text": " ".join(buf), "start": start, "end": end})
            buf, start = [], None
    if buf:
        end = words[-1]["start"] + words[-1]["dur"]
        chunks.append({"text": " ".join(buf), "start": start, "end": end})
    return chunks


def ken_burns_clip(image_path, duration, zoom_ratio=ZOOM_RATIO):
    """Bikin clip dari gambar statis dengan efek zoom pelan (ken burns)."""
    clip = ImageClip(image_path).set_duration(duration)
    w, h = VIDEO_SIZE
    clip = clip.resize(height=int(h * zoom_ratio)).resize(
        lambda t: 1 + (zoom_ratio - 1) * (t / duration)
    )
    clip = clip.set_position(("center", "center"))
    return clip.resize(newsize=VIDEO_SIZE) if False else clip.crop(
        x_center=clip.w / 2, y_center=clip.h / 2, width=w, height=h
    )


def build_caption_clips(caption_chunks):
    clips = []
    for c in caption_chunks:
        wrapped = "\n".join(textwrap.wrap(c["text"], width=22))
        txt = (
            TextClip(
                wrapped,
                fontsize=FONT_SIZE,
                color="white",
                font="DejaVu-Sans-Bold",
                stroke_color="black",
                stroke_width=3,
                method="label",
            )
            .set_start(c["start"])
            .set_end(c["end"])
            .set_position(("center", 0.8), relative=True)
        )
        clips.append(txt)
    return clips


async def generate_one_video(script_path):
    name = os.path.splitext(os.path.basename(script_path))[0]
    with open(script_path, "r", encoding="utf-8") as f:
        text = f.read().strip()

    print(f"[1/4] Generate audio narasi: {name}")
    audio_path = f"_tmp/{name}.mp3"
    words = await generate_audio_and_words(text, audio_path)
    audio_clip = AudioFileClip(audio_path)
    total_duration = audio_clip.duration

    print(f"[2/4] Generate AI image background: {name}")
    segments = split_script_into_segments(text, IMAGE_PER_VIDEO)
    seg_duration = total_duration / len(segments)
    image_clips = []
    for i, seg_text in enumerate(segments):
        img_path = f"_tmp/{name}_{i}.jpg"
        download_ai_image(seg_text, img_path, seed=i)
        image_clips.append(ken_burns_clip(img_path, seg_duration))
        time.sleep(2)  # jeda biar gak kena rate-limit translate/image server

    print(f"[3/4] Susun subtitle sinkron & gabungkan video: {name}")
    background = concatenate_videoclips(image_clips, method="compose")
    caption_clips = build_caption_clips(words_to_caption_chunks(words))
    final = CompositeVideoClip([background, *caption_clips], size=VIDEO_SIZE)
    final = final.set_audio(audio_clip)

    print(f"[4/4] Render & simpan: {name}")
    out_path = os.path.join(OUTPUT_DIR, f"{name}.mp4")
    final.write_videofile(out_path, fps=30, codec="libx264", audio_codec="aac")
    return out_path


def main():
    ensure_dirs()
    script_files = sorted(glob.glob(os.path.join(SCRIPTS_DIR, "*.txt")))
    if not script_files:
        print(
            f"Belum ada naskah. Isi file .txt di folder '{SCRIPTS_DIR}/' dulu, "
            "satu file = satu video."
        )
        return

    print(f"Ditemukan {len(script_files)} naskah. Mulai render batch...\n")
    results = []
    for path in script_files:
        try:
            out = asyncio.run(generate_one_video(path))
            results.append(out)
        except Exception as e:
            print(f"GAGAL '{path}': {e}")
        time.sleep(1)

    print(f"\nSelesai. {len(results)} video tersimpan di folder '{OUTPUT_DIR}/':")
    for r in results:
        print(f"  - {r}")


if __name__ == "__main__":
    main()
