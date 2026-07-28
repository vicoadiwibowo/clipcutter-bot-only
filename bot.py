#!/usr/bin/env python3
"""
Clip Cutter - Bot Telegram (STANDALONE, tanpa web/Flask)
Semua proses (potong video, watermark, subtitle, download YouTube)
jalan langsung di dalam bot ini. Tidak ada server web sama sekali.

Setup:
  pip install python-telegram-bot requests yt-dlp --break-system-packages

Jalankan:
  export BOT_TOKEN="isi_token_dari_BotFather"
  python bot.py
"""

import os
import re
import io
import time
import uuid
import glob
import html
import shutil
import asyncio
import threading
import subprocess

from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import (
    Application,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ======================================================================
# Konfigurasi dasar
# ======================================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
DOWNLOADS_DIR = os.path.join(BASE_DIR, "downloads")
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(DOWNLOADS_DIR, exist_ok=True)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    token_file = os.path.join(BASE_DIR, "bot_token.txt")
    if os.path.isfile(token_file):
        BOT_TOKEN = open(token_file, encoding="utf-8").read().strip()

LINE_PATTERN = re.compile(
    r"^\s*(\d{2}:\d{2}:\d{2})\s*-\s*(\d{2}:\d{2}:\d{2})\s+(.+?)\s*$"
)
SRT_TIME_PATTERN = re.compile(
    r"(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2}),(\d{3})"
)
YT_PROGRESS_PATTERN = re.compile(r"\[download\]\s+([\d.]+)%")
YT_DEST_PATTERN = re.compile(r"\[(?:download|Merger)\].*?(?:Destination|into):\s*(.+)$")

JOBS = {}
JOBS_LOCK = threading.Lock()

YT_JOBS = {}
YT_JOBS_LOCK = threading.Lock()

MAX_AGE_SECONDS = 24 * 60 * 60
CLEANUP_INTERVAL_SECONDS = 30 * 60

WATERMARK_TEXT = "@omah_cliperr"
WATERMARK_OPACITY = 0.22

# ======================================================================
# Util dasar (dipindah dari app.py, tidak ada yang bergantung ke Flask)
# ======================================================================

def sanitize_filename(name: str) -> str:
    name = re.sub(r"[^\w\s\-()]", "", name, flags=re.UNICODE)
    name = re.sub(r"\s+", " ", name).strip()
    return name[:120] if name else "clip"


def parse_lines(raw_text: str):
    jobs, errors = [], []
    for i, line in enumerate(raw_text.splitlines(), start=1):
        if not line.strip():
            continue
        m = LINE_PATTERN.match(line)
        if not m:
            errors.append(f"Baris {i} tidak dikenali formatnya: {line}")
            continue
        start, end, title = m.groups()
        jobs.append((start, end, title))
    return jobs, errors


def to_seconds(hms: str) -> float:
    h, m, s = hms.split(":")
    return int(h) * 3600 + int(m) * 60 + int(s)


def probe_square_size(path: str, default: int = 1080) -> int:
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=p=0",
        path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        w_str, h_str = result.stdout.strip().split(",")
        w, h = int(w_str), int(h_str)
        return min(w, h)
    except Exception:
        return default


def parse_srt(path: str):
    try:
        text = open(path, encoding="utf-8", errors="ignore").read()
    except Exception as e:
        print(f"Error membaca file srt: {e}")
        return []

    entries = []
    blocks = re.split(r"\n\s*\n", text.strip())
    for block in blocks:
        lines = block.strip().splitlines()
        if len(lines) < 2:
            continue
        match, idx = None, 0
        for i, line in enumerate(lines):
            match = SRT_TIME_PATTERN.search(line)
            if match:
                idx = i
                break
        if not match:
            continue
        h1, m1, s1, ms1, h2, m2, s2, ms2 = map(int, match.groups())
        start = h1 * 3600 + m1 * 60 + s1 + ms1 / 1000
        end = h2 * 3600 + m2 * 60 + s2 + ms2 / 1000
        text_lines = [l for l in lines[idx + 1:] if l.strip()]
        if text_lines:
            entries.append((start, end, "\n".join(text_lines)))
    return entries


def _format_ass_time(t: float) -> str:
    t = max(0.0, t)
    h = int(t // 3600); t -= h * 3600
    m = int(t // 60); t -= m * 60
    s = int(t)
    cs = int(round((t - s) * 100))
    if cs >= 100:
        cs = 0
        s += 1
    return f"{h:01d}:{m:02d}:{s:02d}.{cs:02d}"


def _escape_ass_text(text: str) -> str:
    text = text.replace("\\", "\\\\")
    text = text.replace("{", "\\{").replace("}", "\\}")
    text = text.replace("\n", "\\N")
    return text


def build_clip_ass(entries, clip_start_sec, clip_end_sec, out_path, square_size=1080):
    fontsize = max(20, round(square_size * 0.044))
    marginv = round(square_size * 0.075)
    margin_lr = round(square_size * 0.055)
    outline = max(2, round(square_size * 0.0028))
    shadow = 1

    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {square_size}\n"
        f"PlayResY: {square_size}\n"
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,Arial,{fontsize},&H00FFFFFF,&H000000FF,&H00000000,"
        f"&H00000000,1,0,0,0,100,100,0,0,1,{outline},{shadow},2,"
        f"{margin_lr},{margin_lr},{marginv},1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    lines = [header]
    count = 0
    for start, end, text in entries:
        if end <= clip_start_sec or start >= clip_end_sec:
            continue
        rel_start = max(start, clip_start_sec) - clip_start_sec
        rel_end = min(end, clip_end_sec) - clip_start_sec
        if rel_end <= rel_start:
            continue
        count += 1
        ass_text = _escape_ass_text(text)
        lines.append(
            f"Dialogue: 0,{_format_ass_time(rel_start)},{_format_ass_time(rel_end)},"
            f"Default,,0,0,0,,{ass_text}\n"
        )

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("".join(lines))
    return count


def escape_for_ffmpeg_filter(path: str) -> str:
    return path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def escape_drawtext(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
        .replace("%", "\\%")
    )


def build_watermark_filter(square_size: int) -> str:
    fontsize = max(18, round(square_size * 0.045))
    escaped = escape_drawtext(WATERMARK_TEXT)
    return (
        f"drawtext=text='{escaped}':"
        f"fontcolor=white@{WATERMARK_OPACITY}:"
        f"bordercolor=black@{WATERMARK_OPACITY}:"
        "borderw=2:"
        f"fontsize={fontsize}:"
        "x=(w-text_w)/2:y=(h-text_h)/2"
    )


CROP_1TO1 = (
    "crop="
    "w='min(iw\\,ih)':h='min(iw\\,ih)':"
    "x='(iw-min(iw\\,ih))/2':y='(ih-min(iw\\,ih))/2'"
)


def _run_ffmpeg_with_progress(cmd, duration, on_progress):
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        universal_newlines=True, bufsize=1,
    )
    stderr_lines = []

    def read_stderr():
        for line in proc.stderr:
            stderr_lines.append(line)

    t = threading.Thread(target=read_stderr, daemon=True)
    t.start()

    for line in proc.stdout:
        line = line.strip()
        if line.startswith("out_time_ms="):
            try:
                ms = int(line.split("=")[1])
                pct = int((ms / 1_000_000) / duration * 100)
                on_progress(max(0, min(99, pct)))
            except (ValueError, ZeroDivisionError):
                pass
        elif line == "progress=end":
            on_progress(100)

    proc.wait()
    t.join(timeout=2)
    success = proc.returncode == 0
    return success, "".join(stderr_lines)[-2000:]


def cut_clip(input_path, start, end, out_path, on_progress, ass_path=None, square_size=1080):
    duration = max(1, to_seconds(end) - to_seconds(start))

    filters_ = [CROP_1TO1, build_watermark_filter(square_size)]
    has_subtitle = bool(ass_path)
    if has_subtitle:
        escaped = escape_for_ffmpeg_filter(ass_path)
        filters_.append(f"subtitles='{escaped}'")
    vf = ",".join(filters_)

    cmd = [
        "ffmpeg", "-y",
        "-ss", start,
        "-i", input_path,
        "-t", str(duration),
        "-map", "0:v:0",
        "-map", "0:a:0?",
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "20",
        "-c:a", "aac",
        "-b:a", "192k",
        "-avoid_negative_ts", "make_zero",
        "-movflags", "+faststart",
        "-progress", "pipe:1",
        "-nostats",
        out_path,
    ]
    return _run_ffmpeg_with_progress(cmd, duration, on_progress)


def cleanup_old_outputs():
    while True:
        try:
            now = time.time()
            if os.path.isdir(OUTPUT_DIR):
                for name in os.listdir(OUTPUT_DIR):
                    folder = os.path.join(OUTPUT_DIR, name)
                    if not os.path.isdir(folder):
                        continue
                    age = now - os.path.getmtime(folder)
                    if age > MAX_AGE_SECONDS:
                        shutil.rmtree(folder, ignore_errors=True)
                        with JOBS_LOCK:
                            JOBS.pop(name, None)
                        print(f"[cleanup] Menghapus folder kadaluarsa: {folder}")
        except Exception as e:
            print(f"[cleanup] Error: {e}")
        time.sleep(CLEANUP_INTERVAL_SECONDS)


def run_youtube_download(job_id: str, url: str):
    state = YT_JOBS[job_id]
    out_template = os.path.join(DOWNLOADS_DIR, "%(title).150s.%(ext)s")

    cmd = [
        "yt-dlp",
        "-f", "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
        "--merge-output-format", "mp4",
        "--no-playlist",
        "--restrict-filenames",
        "-o", out_template,
        "--newline",
        url,
    ]

    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            universal_newlines=True, bufsize=1,
        )
    except FileNotFoundError:
        state["status"] = "error"
        state["error"] = "yt-dlp belum terinstall. Jalankan: pip install yt-dlp --break-system-packages"
        return

    dest_found = None
    last_lines = []
    for line in proc.stdout:
        line = line.strip()
        last_lines.append(line)
        last_lines[:] = last_lines[-15:]

        m = YT_PROGRESS_PATTERN.search(line)
        if m:
            try:
                state["progress"] = min(99, int(float(m.group(1))))
            except ValueError:
                pass

        m2 = YT_DEST_PATTERN.search(line)
        if m2:
            dest_found = m2.group(1).strip()

    proc.wait()

    if proc.returncode != 0:
        state["status"] = "error"
        state["error"] = "\n".join(last_lines)[-1500:]
        return

    filename = os.path.basename(dest_found) if dest_found else None
    if not filename or not os.path.isfile(os.path.join(DOWNLOADS_DIR, filename)):
        candidates = sorted(
            glob.glob(os.path.join(DOWNLOADS_DIR, "*")),
            key=os.path.getmtime, reverse=True,
        )
        filename = os.path.basename(candidates[0]) if candidates else None

    state["status"] = "done"
    state["progress"] = 100
    state["filename"] = filename
    state["path"] = os.path.join(DOWNLOADS_DIR, filename) if filename else None


def list_downloaded_videos():
    files = []
    for name in sorted(os.listdir(DOWNLOADS_DIR)):
        path = os.path.join(DOWNLOADS_DIR, name)
        if os.path.isfile(path):
            files.append({
                "name": name,
                "path": path,
                "size_mb": round(os.path.getsize(path) / (1024 * 1024), 1),
            })
    return files


def delete_downloaded_video(name: str):
    safe_name = os.path.basename(name)
    path = os.path.join(DOWNLOADS_DIR, safe_name)
    if not os.path.isfile(path):
        return False, "File tidak ditemukan."
    try:
        os.remove(path)
        return True, None
    except OSError as e:
        return False, str(e)


def run_job(session_id, video_path, full_srt_path):
    """Proses potong semua klip untuk 1 session. Jalan di background thread."""
    state = JOBS[session_id]
    session_dir = os.path.join(OUTPUT_DIR, session_id)

    square_size = probe_square_size(video_path)

    subtitle_entries = None
    if full_srt_path and os.path.exists(full_srt_path):
        subtitle_entries = parse_srt(full_srt_path)
        state["subtitle_status"] = "ok" if subtitle_entries else "empty"
    else:
        state["subtitle_status"] = "skipped"

    for clip in state["clips"]:
        clip["status"] = "processing"

        def cb(pct, clip=clip):
            clip["progress"] = pct

        out_path = os.path.join(session_dir, clip["filename"])
        clip["out_path"] = out_path
        start_sec = to_seconds(clip["start"])
        end_sec = to_seconds(clip["end"])

        used_subtitle = False
        clip_ass_path = None
        if subtitle_entries:
            candidate_ass = os.path.join(session_dir, f"{clip['filename']}.ass")
            n_lines = build_clip_ass(
                subtitle_entries, start_sec, end_sec, candidate_ass, square_size=square_size
            )
            if n_lines > 0:
                clip_ass_path = candidate_ass
                used_subtitle = True

        success, log = cut_clip(
            video_path, clip["start"], clip["end"], out_path, cb,
            ass_path=clip_ass_path, square_size=square_size
        )

        clip["has_subtitle"] = used_subtitle
        if success:
            clip["status"] = "done"
            clip["progress"] = 100
        else:
            clip["status"] = "error"
            clip["progress"] = 100
            clip["log"] = log

    state["finished"] = True


# ======================================================================
# Bot Telegram
# ======================================================================

MAIN_MENU, ASK_PATH, ASK_TIMESTAMPS, ASK_SRT, ASK_YOUTUBE_URL, ASK_DELETE_NAME = range(6)

LAST_SESSION = {}

BTN_POTONG = "✂️ Potong Video Baru"
BTN_STATUS = "📊 Cek Status"
BTN_YOUTUBE = "⬇️ Download YouTube"
BTN_LIST = "📃 Daftar Video"
BTN_DELETE = "🗑️ Hapus Video"
BTN_BATAL = "❌ Batal"
BTN_LEWATI = "⏭️ Lewati (Tanpa Subtitle)"

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        [BTN_POTONG, BTN_STATUS],
        [BTN_YOUTUBE, BTN_LIST],
        [BTN_DELETE, BTN_BATAL],
    ],
    resize_keyboard=True,
)

SRT_KEYBOARD = ReplyKeyboardMarkup(
    [[BTN_LEWATI], [BTN_BATAL]],
    resize_keyboard=True,
)


def code(text: str) -> str:
    return f"<code>{html.escape(str(text))}</code>"


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "Halo! Selamat datang di Clip Cutter Bot.\nSilakan pilih menu di bawah.",
        reply_markup=MAIN_KEYBOARD,
    )
    return MAIN_MENU


async def menu_potong(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "Kirim lokasi lengkap file video-nya.\n\n"
        "Contoh:\n"
        "/storage/emulated/0/snaptube/download/SnapTube Video/nama_video.mp4",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ASK_PATH


async def receive_path(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    video_path = update.message.text.strip()
    if not os.path.isfile(video_path):
        await update.message.reply_text(
            f"⚠️ File tidak ditemukan:\n{code(video_path)}\n\nKirim ulang path yang benar, atau tekan Batal.",
            parse_mode="HTML",
        )
        return ASK_PATH
    context.user_data["video_path"] = video_path
    await update.message.reply_text(
        "Sekarang paste daftar potongannya. Format tiap baris:\n"
        "HH:MM:SS-HH:MM:SS Judul Klip\n\n"
        "Contoh:\n"
        "00:03:52-00:04:36 Akar Masalah Semua Bisnis"
    )
    return ASK_TIMESTAMPS


async def receive_timestamps(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw_text = update.message.text
    jobs, errors = parse_lines(raw_text)
    if not jobs:
        msg = "\n".join(errors) if errors else "Tidak ada baris yang valid."
        await update.message.reply_text(f"⚠️ Format tidak valid:\n{msg}\n\nCoba kirim ulang.")
        return ASK_TIMESTAMPS
    context.user_data["raw_text"] = raw_text
    await update.message.reply_text(
        "Ada file subtitle .srt yang mau di-burn ke klip?\n\n"
        "Kirim file .srt sekarang, atau tekan 'Lewati' kalau tidak pakai subtitle.",
        reply_markup=SRT_KEYBOARD,
    )
    return ASK_SRT


async def receive_srt_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    document = update.message.document
    if not document.file_name.lower().endswith(".srt"):
        await update.message.reply_text(
            "File itu bukan .srt. Kirim file .srt yang benar, atau tekan 'Lewati'."
        )
        return ASK_SRT

    tg_file = await document.get_file()
    file_bytes = await tg_file.download_as_bytearray()

    await update.message.reply_text("Subtitle diterima. Memulai proses...", reply_markup=MAIN_KEYBOARD)
    await start_processing(
        update, context,
        srt_bytes=bytes(file_bytes), srt_filename=document.file_name,
    )
    return MAIN_MENU


async def skip_srt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Oke, tanpa subtitle. Memulai proses...", reply_markup=MAIN_KEYBOARD)
    await start_processing(update, context, srt_bytes=None, srt_filename=None)
    return MAIN_MENU


async def start_processing(update: Update, context: ContextTypes.DEFAULT_TYPE, srt_bytes=None, srt_filename=None):
    video_path = context.user_data.get("video_path", "")
    raw_text = context.user_data.get("raw_text", "")
    chat_id = update.effective_chat.id

    status_msg = await update.message.reply_text("⏳ Menyiapkan klip...")

    jobs, _ = parse_lines(raw_text)
    session_id = uuid.uuid4().hex[:10]
    session_dir = os.path.join(OUTPUT_DIR, session_id)
    os.makedirs(session_dir, exist_ok=True)

    full_srt_path = None
    if srt_bytes:
        full_srt_path = os.path.join(session_dir, "uploaded_sub.srt")
        with open(full_srt_path, "wb") as f:
            f.write(srt_bytes)

    clips = []
    for idx, (start, end, title) in enumerate(jobs, start=1):
        safe_title = sanitize_filename(title)
        filename = f"{idx:02d} - {safe_title}.mp4"
        clips.append({
            "filename": filename,
            "start": start,
            "end": end,
            "status": "pending",
            "progress": 0,
            "log": "",
            "has_subtitle": False,
            "out_path": None,
        })

    with JOBS_LOCK:
        JOBS[session_id] = {
            "video_path": video_path,
            "clips": clips,
            "finished": False,
            "fatal_error": None,
            "subtitle_status": "skipped",
            "created": time.time(),
        }

    LAST_SESSION[chat_id] = session_id
    threading.Thread(target=run_job, args=(session_id, video_path, full_srt_path), daemon=True).start()

    await status_msg.edit_text(f"✅ Diterima. Memproses klip...\nSession: {session_id}")
    asyncio.create_task(watch_job(context, chat_id, session_id, status_msg.message_id))

    await update.message.reply_text(
        "Proses berjalan di latar belakang. Kamu akan diberi tahu begitu selesai.\n"
        "Bisa juga cek manual lewat menu 📊 Cek Status."
    )


async def watch_job(context: ContextTypes.DEFAULT_TYPE, chat_id: int, session_id: str, status_message_id: int):
    bot = context.bot
    last_text = ""

    while True:
        data = JOBS.get(session_id)
        if data is None:
            return

        clips = data["clips"]
        done = sum(1 for c in clips if c["status"] in ("done", "error"))
        text = f"⏳ Memproses klip... {done}/{len(clips)} selesai"
        if text != last_text:
            try:
                await bot.edit_message_text(chat_id=chat_id, message_id=status_message_id, text=text)
                last_text = text
            except Exception:
                pass

        if data["finished"]:
            break
        await asyncio.sleep(3)

    ok_count = 0
    for clip in clips:
        if clip["status"] != "done":
            continue
        try:
            size = os.path.getsize(clip["out_path"])
            if size > 49 * 1024 * 1024:
                await bot.send_message(
                    chat_id=chat_id,
                    text=f"⚠️ {clip['filename']} terlalu besar untuk dikirim via Telegram (>49MB).",
                )
                continue
            with open(clip["out_path"], "rb") as f:
                await bot.send_video(
                    chat_id=chat_id,
                    video=f,
                    filename=clip["filename"],
                    caption=clip["filename"],
                    supports_streaming=True,
                )
            ok_count += 1
        except Exception as e:
            await bot.send_message(chat_id=chat_id, text=f"⚠️ Gagal kirim {clip['filename']}: {e}")

    await bot.edit_message_text(
        chat_id=chat_id, message_id=status_message_id,
        text=f"✅ Semua selesai! {ok_count}/{len(clips)} klip terkirim.",
    )


async def menu_youtube(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "Kirim link video YouTube yang mau diunduh.\n"
        "Kualitas dikunci maksimal 1080p.",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ASK_YOUTUBE_URL


async def receive_youtube_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    url = update.message.text.strip()
    chat_id = update.effective_chat.id

    status_msg = await update.message.reply_text("⏳ Memulai download dari YouTube...")

    job_id = uuid.uuid4().hex[:10]
    with YT_JOBS_LOCK:
        YT_JOBS[job_id] = {
            "status": "downloading", "progress": 0,
            "filename": None, "path": None, "error": None, "url": url,
        }
    threading.Thread(target=run_youtube_download, args=(job_id, url), daemon=True).start()

    await status_msg.edit_text("⏳ Mengunduh dari YouTube... 0%")
    asyncio.create_task(watch_youtube_job(context, chat_id, job_id, status_msg.message_id))

    await update.message.reply_text(
        "Download berjalan di background. Kamu akan diberi tahu begitu selesai.",
        reply_markup=MAIN_KEYBOARD,
    )
    return MAIN_MENU


async def watch_youtube_job(context: ContextTypes.DEFAULT_TYPE, chat_id: int, job_id: str, status_message_id: int):
    bot = context.bot
    last_text = ""

    while True:
        data = YT_JOBS.get(job_id)
        if data is None:
            await asyncio.sleep(3)
            continue

        if data.get("status") == "error":
            err = (data.get("error") or "")[:500]
            await bot.edit_message_text(
                chat_id=chat_id, message_id=status_message_id,
                text=f"❌ Gagal mengunduh dari YouTube.\n{err}",
            )
            return

        if data.get("status") == "done":
            break

        text = f"⏳ Mengunduh dari YouTube... {data.get('progress', 0)}%"
        if text != last_text:
            try:
                await bot.edit_message_text(chat_id=chat_id, message_id=status_message_id, text=text)
                last_text = text
            except Exception:
                pass
        await asyncio.sleep(3)

    filename = data.get("filename") or "(nama tidak diketahui)"
    path = data.get("path") or ""
    await bot.edit_message_text(
        chat_id=chat_id, message_id=status_message_id,
        text=(
            f"✅ Selesai diunduh (max 1080p):\n{code(filename)}\n\n"
            f"Lokasi (tap untuk copy):\n{code(path)}\n\n"
            "Path ini bisa langsung dipakai di menu 'Potong Video Baru'."
        ),
        parse_mode="HTML",
    )


async def menu_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    data = list_downloaded_videos()
    if not data:
        await update.message.reply_text("Belum ada video yang pernah diunduh.")
        return MAIN_MENU

    lines = ["📃 Daftar video ter-download:\n"]
    for f in data:
        lines.append(f"• {html.escape(f['name'])} ({f['size_mb']} MB)\n  {code(f['path'])}")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")
    return MAIN_MENU


async def menu_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    data = list_downloaded_videos()
    if not data:
        await update.message.reply_text("Belum ada video yang bisa dihapus.")
        return MAIN_MENU

    lines = ["Ketik nama file (persis) yang mau dihapus, atau tap nama di bawah untuk copy:\n"]
    for f in data:
        lines.append(f"• {code(f['name'])}")
    await update.message.reply_text(
        "\n".join(lines), reply_markup=ReplyKeyboardRemove(), parse_mode="HTML"
    )
    return ASK_DELETE_NAME


async def receive_delete_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = update.message.text.strip()
    success, err = delete_downloaded_video(name)

    if success:
        await update.message.reply_text(f"🗑️ Berhasil dihapus: {name}", reply_markup=MAIN_KEYBOARD)
    else:
        await update.message.reply_text(f"❌ Gagal menghapus.\n{err}", reply_markup=MAIN_KEYBOARD)
    return MAIN_MENU


async def menu_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = update.effective_chat.id
    session_id = LAST_SESSION.get(chat_id)
    if not session_id:
        await update.message.reply_text("Belum ada proses yang pernah dijalankan dari chat ini.")
        return MAIN_MENU

    data = JOBS.get(session_id)
    if not data:
        await update.message.reply_text("Session tidak ditemukan (mungkin sudah kadaluarsa/dihapus).")
        return MAIN_MENU

    lines = [f"Session: {session_id}"]
    for c in data["clips"]:
        icon = {"pending": "⏳", "processing": "🔄", "done": "✅", "error": "❌"}.get(c["status"], "?")
        lines.append(f"{icon} {c['filename']} ({c['progress']}%)")
    await update.message.reply_text("\n".join(lines))
    return MAIN_MENU


async def menu_batal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("Dibatalkan. Kembali ke menu utama.", reply_markup=MAIN_KEYBOARD)
    return MAIN_MENU


async def fallback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Silakan pilih menu di bawah.", reply_markup=MAIN_KEYBOARD)
    return MAIN_MENU


def main():
    if not BOT_TOKEN:
        raise SystemExit(
            "BOT_TOKEN belum diset. Jalankan:\n"
            "  export BOT_TOKEN='isi_token_dari_BotFather'\n"
            "atau simpan token ke file ~/clipcutter/bot_token.txt"
        )

    threading.Thread(target=cleanup_old_outputs, daemon=True).start()

    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", cmd_start)],
        states={
            MAIN_MENU: [
                MessageHandler(filters.Regex(f"^{re.escape(BTN_POTONG)}$"), menu_potong),
                MessageHandler(filters.Regex(f"^{re.escape(BTN_STATUS)}$"), menu_status),
                MessageHandler(filters.Regex(f"^{re.escape(BTN_YOUTUBE)}$"), menu_youtube),
                MessageHandler(filters.Regex(f"^{re.escape(BTN_LIST)}$"), menu_list),
                MessageHandler(filters.Regex(f"^{re.escape(BTN_DELETE)}$"), menu_delete),
                MessageHandler(filters.Regex(f"^{re.escape(BTN_BATAL)}$"), menu_batal),
                MessageHandler(filters.TEXT & ~filters.COMMAND, fallback),
            ],
            ASK_PATH: [
                MessageHandler(filters.Regex(f"^{re.escape(BTN_BATAL)}$"), menu_batal),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_path),
            ],
            ASK_TIMESTAMPS: [
                MessageHandler(filters.Regex(f"^{re.escape(BTN_BATAL)}$"), menu_batal),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_timestamps),
            ],
            ASK_SRT: [
                MessageHandler(filters.Regex(f"^{re.escape(BTN_BATAL)}$"), menu_batal),
                MessageHandler(filters.Regex(f"^{re.escape(BTN_LEWATI)}$"), skip_srt),
                MessageHandler(filters.Document.ALL, receive_srt_file),
                MessageHandler(filters.Regex(r"(?i)^(skip|lewati)$"), skip_srt),
            ],
            ASK_YOUTUBE_URL: [
                MessageHandler(filters.Regex(f"^{re.escape(BTN_BATAL)}$"), menu_batal),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_youtube_url),
            ],
            ASK_DELETE_NAME: [
                MessageHandler(filters.Regex(f"^{re.escape(BTN_BATAL)}$"), menu_batal),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_delete_name),
            ],
        },
        fallbacks=[CommandHandler("start", cmd_start)],
    )

    app.add_handler(conv)
    print("Bot Telegram Clip Cutter (standalone, tanpa web) berjalan...")
    app.run_polling()


if __name__ == "__main__":
    main()
