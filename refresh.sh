#!/bin/zsh
# Pullt frische Daten und pusht cockpit_data.json ins Repo.
# Streamlit Cloud zieht den Commit automatisch und zeigt frische Zahlen.
cd /Users/ricooliveira/cockpit || exit 1

LOG=/Users/ricooliveira/cockpit/refresh.log

# --- Zeitfenster: nur Mo-Fr, 7-20 Uhr (ausser --force) ---
if [[ "$1" != "--force" ]]; then
  DOW=$(date +%u)   # 1=Mo .. 7=So
  HOUR=$(date +%H)
  if (( DOW > 5 )) || (( 10#$HOUR < 7 )) || (( 10#$HOUR > 20 )); then
    exit 0
  fi
fi

# --- Lock: verhindert parallele Laeufe (launchd-Nachholer + regulaerer Lauf) ---
LOCK=/tmp/sbc-cockpit-refresh.lock
if ! mkdir "$LOCK" 2>/dev/null; then
  # Stale Lock nach 30 Min aufraeumen, sonst ueberspringen
  if [[ -n $(find "$LOCK" -maxdepth 0 -mmin +30 2>/dev/null) ]]; then
    rmdir "$LOCK" 2>/dev/null && mkdir "$LOCK" 2>/dev/null || exit 0
  else
    echo "[$(date '+%Y-%m-%d %H:%M')] laeuft bereits, uebersprungen" >> "$LOG"
    exit 0
  fi
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT INT TERM

/usr/bin/python3 build_cache.py >> "$LOG" 2>&1

# Nur committen wenn sich was geaendert hat
if ! git diff --quiet cockpit_data.json 2>/dev/null; then
  git add cockpit_data.json
  git -c commit.gpgsign=false commit -m "Auto-refresh cockpit data $(date '+%Y-%m-%d %H:%M')" >> "$LOG" 2>&1
  git push >> "$LOG" 2>&1
  echo "[$(date '+%Y-%m-%d %H:%M')] pushed" >> "$LOG"
else
  echo "[$(date '+%Y-%m-%d %H:%M')] no changes" >> "$LOG"
fi
