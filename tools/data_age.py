"""Gibt das Alter von cockpit_data.json in Minuten aus.

Liest bewusst den Repo-Head ueber raw.githubusercontent.com, also genau die
Quelle die auch die Streamlit-App nutzt. Ein Checkout im Runner wuerde nur
zeigen, was beim Job-Start da war, nicht was das Cockpit gerade ausliefert.

Nutzung:  python3 tools/data_age.py [repo]
Ausgabe:  Alter in Minuten auf stdout, oder Exit 2 wenn nicht abrufbar.
"""
import datetime as dt
import json
import sys
import time
import urllib.request
from zoneinfo import ZoneInfo

REPO = sys.argv[1] if len(sys.argv) > 1 else "roliveira-del/synergy-cockpit"
URL = f"https://raw.githubusercontent.com/{REPO}/main/cockpit_data.json"


def main():
    try:
        req = urllib.request.Request(
            f"{URL}?t={int(time.time())}",  # Cache-Buster gegen das raw-CDN
            headers={"Cache-Control": "no-cache", "User-Agent": "cockpit-waechter"},
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            gen = json.loads(r.read().decode("utf-8"))["generated_at"][:19]
    except Exception as e:
        print(f"cockpit_data.json nicht lesbar: {e}", file=sys.stderr)
        return 2

    gen_dt = dt.datetime.strptime(gen, "%Y-%m-%dT%H:%M:%S")
    jetzt = dt.datetime.now(ZoneInfo("Europe/Berlin")).replace(tzinfo=None)
    print(int((jetzt - gen_dt).total_seconds() // 60))
    return 0


if __name__ == "__main__":
    sys.exit(main())
