#!/data/data/com.termux/files/usr/bin/bash
set -e

export DEBIAN_FRONTEND=noninteractive

REPO_RAW="https://raw.githubusercontent.com/USERNAME/REPO-BARU/main"
CLIPCUTTER_DIR=~/clipcutter

echo "Clip Cutter Bot - Auto Installer (bot only, tanpa web)"
echo "======================================================="

echo "[1/5] Update & install paket dasar (python, ffmpeg, git)..."
yes n | apt-get update -y
yes n | apt-get upgrade -y \
  -o Dpkg::Options::="--force-confdef" \
  -o Dpkg::Options::="--force-confold"
yes n | apt-get install -y \
  -o Dpkg::Options::="--force-confdef" \
  -o Dpkg::Options::="--force-confold" \
  python ffmpeg git

echo "[2/5] Install library Python (python-telegram-bot, yt-dlp)..."
pip install python-telegram-bot yt-dlp requests --break-system-packages 2>/dev/null \
  || pip install python-telegram-bot yt-dlp requests

echo "[3/5] Setup akses storage (izinkan lewat pop-up yang muncul)..."
termux-setup-storage
sleep 2

echo "[4/5] Mengunduh bot.py terbaru dari repo..."
mkdir -p "$CLIPCUTTER_DIR"
curl -fsSL "$REPO_RAW/bot.py" -o "$CLIPCUTTER_DIR/bot.py"

if [ ! -s "$CLIPCUTTER_DIR/bot.py" ]; then
  echo "GAGAL: bot.py tidak berhasil diunduh atau kosong. Cek URL repo-nya."
  exit 1
fi
python3 -c "import py_compile; py_compile.compile('$CLIPCUTTER_DIR/bot.py', doraise=True)" \
  && echo "bot.py valid (sintaks OK)" \
  || { echo "GAGAL: bot.py yang diunduh punya error sintaks."; exit 1; }

echo "[5/5] Cek token Bot Telegram & setup auto-start..."
TOKEN_FILE="$CLIPCUTTER_DIR/bot_token.txt"
if [ -s "$TOKEN_FILE" ]; then
  echo "Token bot ditemukan, akan dipakai."
else
  echo "Token bot belum ada. Isi dulu sebelum bot bisa jalan:"
  echo "  echo 'TOKEN_KAMU' > $CLIPCUTTER_DIR/bot_token.txt"
fi

# --- A. Auto-start tiap sesi Termux dibuka (lewat .bashrc) ---
BASHRC=~/.bashrc
MARKER="# >>> clipcutter-autostart >>>"
if ! grep -qF "$MARKER" "$BASHRC" 2>/dev/null; then
  cat >> "$BASHRC" << EOF

$MARKER
if [ -s "$CLIPCUTTER_DIR/bot_token.txt" ] && ! pgrep -f "bot.py" > /dev/null 2>&1; then
  (cd "$CLIPCUTTER_DIR" && BOT_TOKEN="\$(cat bot_token.txt)" nohup python bot.py > bot.log 2>&1 &)
  echo "Bot Telegram Clip Cutter dijalankan di background"
fi
# <<< clipcutter-autostart <<<
EOF
  echo "Auto-start .bashrc terpasang."
else
  echo "Auto-start .bashrc sudah ada dari sebelumnya, dilewati."
fi

# --- B. Auto-start tiap HP restart (lewat Termux:Boot) ---
mkdir -p ~/.termux/boot
BOOT_SCRIPT=~/.termux/boot/start-clipcutter.sh
{
  echo '#!/data/data/com.termux/files/usr/bin/sh'
  echo 'termux-wake-lock'
  echo "cd $CLIPCUTTER_DIR"
  echo "if [ -s $CLIPCUTTER_DIR/bot_token.txt ]; then"
  echo "  BOT_TOKEN=\$(cat $CLIPCUTTER_DIR/bot_token.txt) nohup python bot.py > bot.log 2>&1 &"
  echo "fi"
} > "$BOOT_SCRIPT"
chmod +x "$BOOT_SCRIPT"

echo "Menjalankan bot sekarang (jika token sudah ada)..."
pkill -9 -f "bot.py" 2>/dev/null || true
sleep 1

cd "$CLIPCUTTER_DIR"
if [ -s "$TOKEN_FILE" ]; then
  BOT_TOKEN="$(cat "$TOKEN_FILE")" nohup python bot.py > bot.log 2>&1 &
  disown
  BOT_STARTED=1
else
  BOT_STARTED=0
fi

sleep 1
echo ""
echo "=================================="
echo "SELESAI!"
echo "=================================="
if [ "$BOT_STARTED" = "1" ]; then
  echo "Bot Telegram : aktif, coba /start di chat bot kamu"
else
  echo "Bot Telegram : BELUM aktif (token belum diisi)."
  echo "  Isi dengan:"
  echo "  echo 'TOKEN_KAMU' > $CLIPCUTTER_DIR/bot_token.txt"
  echo "  lalu buka Termux baru, atau jalankan manual:"
  echo "  cd $CLIPCUTTER_DIR && BOT_TOKEN=\$(cat bot_token.txt) python bot.py"
fi
echo ""
echo "Mulai sekarang, setiap kali kamu buka Termux, bot akan otomatis"
echo "nyala sendiri di background -- tidak perlu ketik apapun."
echo ""
echo "Catatan:"
echo "- Kalau ini HP baru: install juga aplikasi 'Termux:Boot' dari"
echo "  sumber yang sama dengan Termux (F-Droid/Play Store), buka sekali,"
echo "  lalu set baterai Termux & Termux:Boot ke 'Unrestricted'."
echo "- Pastikan izin storage sudah di-Allow saat pop-up muncul tadi."
