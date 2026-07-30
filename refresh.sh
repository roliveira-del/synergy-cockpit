#!/bin/zsh
# Baut cockpit_data.json neu, committet und pusht.
#
# WICHTIG: Der regulaere Refresh laeuft seit 29.07.2026 als GitHub Action
# (.github/workflows/refresh.yml), nicht mehr auf diesem Rechner. Dieses
# Skript ist nur noch der manuelle Notnagel und laeuft ausschliesslich mit
# --force.
#
# Grund fuer die Sperre: In der crontab steht noch ein Aufruf alle 15 Minuten,
# der sich vom Terminal aus nicht loeschen laesst (crontab verweigert den
# Schreibzugriff ohne Full Disk Access). Lief er weiter, committete er lokal
# gegen die Cloud-Commits und die Historien liefen auseinander; genau das ist
# am 30.07. passiert. Ohne --force tut das Skript deshalb nichts.

cd /Users/ricooliveira/cockpit || exit 1
LOG=/Users/ricooliveira/cockpit/refresh.log

if [[ "$1" != "--force" ]]; then
  # Bewusst still, damit der Alt-cron das Log nicht vollschreibt.
  exit 0
fi

# --- Lock gegen parallele Laeufe ---
LOCK=/tmp/sbc-cockpit-refresh.lock
if ! mkdir "$LOCK" 2>/dev/null; then
  if [[ -n $(find "$LOCK" -maxdepth 0 -mmin +30 2>/dev/null) ]]; then
    rmdir "$LOCK" 2>/dev/null && mkdir "$LOCK" 2>/dev/null || exit 0
  else
    echo "[$(date '+%Y-%m-%d %H:%M')] laeuft bereits, uebersprungen" >> "$LOG"
    exit 0
  fi
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT INT TERM

# Erst auf den Cloud-Stand aufsetzen, sonst divergieren die Historien.
git pull --rebase --autostash -q origin main >> "$LOG" 2>&1

/usr/bin/python3 build_cache.py >> "$LOG" 2>&1

if ! git diff --quiet cockpit_data.json 2>/dev/null; then
  git add cockpit_data.json
  git -c commit.gpgsign=false commit -m "Auto-refresh cockpit data $(date '+%Y-%m-%d %H:%M')" >> "$LOG" 2>&1
  git pull --rebase --autostash -q origin main >> "$LOG" 2>&1
  git push >> "$LOG" 2>&1
  echo "[$(date '+%Y-%m-%d %H:%M')] pushed" >> "$LOG"
else
  echo "[$(date '+%Y-%m-%d %H:%M')] no changes" >> "$LOG"
fi
