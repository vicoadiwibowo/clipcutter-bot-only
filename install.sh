#!/data/data/com.termux/files/usr/bin/bash
set -e

export DEBIAN_FRONTEND=noninteractive

REPO_RAW="https://raw.githubusercontent.com/vicoadiwibowo/clipcutter-bot-only/main"
CLIPCUTTER_DIR=~/clipcutter

# ----------------------------------------------------------------------
# Helper tampilan rapi
# ----------------------------------------------------------------------
step() {
  echo ""
  echo "▶ $1"
}

ok() {
  echo "  ✓ $1"
}

fail() {
  echo "  ✗ $1"
}

echo "=================================================="
echo "   Clip Cutter Bot - Auto Installer (bot only)"
echo "=================================================="

# ----------------------------------------------------------------------
# [1/5] Update & paket dasar
# ----------------------------------------------------------------------
step "[1/5] Update paket Termux..."
yes n | apt-get update -y -qq >/dev/null 2>&1
ok "Update selesai"

step "[1/5] Upgrade paket Termux..."
yes n | apt-get upgrade -y -qq \
  -o Dpkg::Options::="--force-confdef" \
  -o Dpkg::Options::="--force-confold" >/dev/null 2>&1
ok "Upgrade selesai"

step "[1/5] Install python, ffmpeg, git..."
yes n | apt-get install -y -qq \
  -o Dpkg::Options::="--force-confdef" \
  -o Dpkg::Options::="--force-confold" \
  python ffmpeg git >/dev/null 2>&1
ok "python, ffmpeg, git terpasang"

# ----------------------------------------------------------------------
# [2/5] Library Python
# ----------------------------------------------------------------------
step "[2/5] Install python-telegram-bot..."
pip install -q python-telegram-bot --break-system-packages >/dev/null 2>&1 \
  || pip install -q python-telegram-bot >/dev/null 2>&1
ok "python-telegram-bot terpasang"

step "[2/5] Install yt-dlp..."
pip install -q yt-dlp --break-system-packages >/dev/null 2>&1 \
  || pip install -q yt-dlp >/dev/null 2>&1
ok "yt-dlp terpasang"

step "[2/5] Install requests..."
pip install -q requests --break-system-packages >/dev/null 2>&1 \
  || pip install -q requests >/dev/null 2>&1
ok "requests terpasang"

# ----------------------------------------------------------------------
# [3/5] Storage
# ----------------------------------------------------------------------
step "[3/5] Setup akses storage (izinkan lewat pop-up yang muncul)..."
termux-setup-storage
sleep 2
ok "Storage siap"

# ----------------------------------------------------------------------
# [4/5] Unduh bot.py
# ----------------------------------------------------------------------
step "[4/5] Mengunduh bot.py dari repo..."
mkdir -p "$CLIPCUTTER_DIR"
curl -fsSL "$REPO_RAW/bot.py" -o "$CLIPCUTTER_DIR/bot.py"

if [ ! -s "$CLIPCUTTER_DIR/bot.py" ]; then
  fail "bot.py tidak berhasil diunduh atau kosong. Cek REPO_RAW / nama file di repo."
  exit 1
fi
ok "bot.py berhasil diunduh"

python3 -c "import py_compile; py_compile.compile('$CLIPCUTTER_DIR/bot.py', doraise=True)" \
  >/dev/null 2>&1 \
  && ok "bot.py valid (sintaks OK)" \
  || { fail "bot.py punya error sintaks."; exit 1; }

# ----------------------------------------------------------------------
# [5/5] Token & auto-start
# ----------------------------------------------------------------------
step "[5/5] Cek token Bot Telegram..."
TOKEN_FILE="$CLIPCUTTER_DIR/bot_token.txt"
if [ -s "$TOKEN_FILE" ]; then
  ok "Token ditemukan"
else
  fail "Token belum ada. Isi nanti dengan:"
  echo "     echo 'TOKEN_KAMU' > $CLIPCUTTER_DIR/bot_token.txt"
fi

step "[5/5] Setup auto-start..."

# A. Auto-start tiap sesi Termux dibuka (lewat .bashrc)
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
  ok "Auto-start .bashrc terpasang"
else
  ok "Auto-start .bashrc sudah ada, dilewati"
fi

# B. Auto-start tiap HP restart (lewat Termux:Boot)
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
ok "Auto-start Termux:Boot terpasang"

# ----------------------------------------------------------------------
# Jalankan sekarang
# ----------------------------------------------------------------------
step "Menjalankan bot (jika token sudah ada)..."
pkill -9 -f "bot.py" 2>/dev/null || true
sleep 1

cd "$CLIPCUTTER_DIR"
if [ -s "$TOKEN_FILE" ]; then
  BOT_TOKEN="$(cat "$TOKEN_FILE")" nohup python bot.py > bot.log 2>&1 &
  disown
  BOT_STARTED=1
  ok "Bot dijalankan di background"
else
  BOT_STARTED=0
fi

echo ""
echo "=================================================="
echo "   SELESAI!"
echo "=================================================="
if [ "$BOT_STARTED" = "1" ]; then
  echo "Bot Telegram : aktif -- coba /start di chat bot kamu"
else
  echo "Bot Telegram : BELUM aktif (token belum diisi)"
  echo ""
  echo "Isi token dulu:"
  echo "  echo 'TOKEN_KAMU' > $CLIPCUTTER_DIR/bot_token.txt"
  echo ""
  echo "Lalu jalankan manual:"
  echo "  cd $CLIPCUTTER_DIR && BOT_TOKEN=\$(cat bot_token.txt) python bot.py"
fi
echo ""
echo "Mulai sekarang, setiap buka Termux, bot otomatis nyala di background."
echo ""
echo "Catatan:"
echo "- HP baru: install juga 'Termux:Boot' (F-Droid/Play Store), buka sekali,"
echo "  lalu set baterai Termux & Termux:Boot ke 'Unrestricted'."
echo "- Pastikan izin storage sudah di-Allow saat pop-up muncul tadi."
