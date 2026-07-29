#!/usr/bin/env python3
"""
Pullt alle Aircall + Recruit CRM Daten und schreibt cockpit_data.json.
Aufruf via cron (alle 15 Min) oder manuell.

Streamlit liest dann nur die JSON in 1 Sek statt live zu pullen.
"""
import os, sys, json, base64, time, urllib.request, urllib.parse, urllib.error
import datetime as dt
from collections import Counter
from pathlib import Path

# Setup
COCKPIT_DIR = Path(__file__).parent

# Zugangsdaten: lokal aus ~/.tracker.env, in GitHub Actions aus den Secrets
# (Umgebungsvariablen). Umgebungsvariablen haben Vorrang.
KEYS = ("AIRCALL_API_ID", "AIRCALL_API_TOKEN", "RECRUITCRM_API_TOKEN")
ENV = {}
env_file = Path.home() / ".tracker.env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1); ENV[k] = v.strip()
for k in KEYS:
    if os.environ.get(k):
        ENV[k] = os.environ[k].strip()

fehlend = [k for k in KEYS if not ENV.get(k)]
if fehlend:
    sys.exit(f"Zugangsdaten fehlen: {', '.join(fehlend)}. "
             f"Lokal in ~/.tracker.env eintragen, in GitHub Actions als Repository-Secret.")

AIRCALL_AUTH = base64.b64encode(f"{ENV['AIRCALL_API_ID']}:{ENV['AIRCALL_API_TOKEN']}".encode()).decode()
CRM_TOKEN = ENV['RECRUITCRM_API_TOKEN']

USERS = {
    "Kevin": {"aircall_id": 1301014, "crm_id": 99698},
    "Robin": {"aircall_id": 1561108, "crm_id": 100690},
}
ASSIGNMENT_STATUS = {1, 613655, 613656, 613657, 613658, 613659, 613660, 613661, 613662, 8}
INTERVIEW_STATUS = {613655, 613656, 613657}
DEAL_STATUS = {8}

def aircall_get(url):
    req = urllib.request.Request(url, headers={"Authorization": f"Basic {AIRCALL_AUTH}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)

def crm_get(url, retries=3):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {CRM_TOKEN}", "Accept": "application/json"})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(20 * (attempt + 1))
                continue
            raise
    return {"data": []}

def parse_dt(s):
    if not s: return None
    try: return dt.datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")
    except: return None

def log(msg):
    print(f"[{dt.datetime.now().isoformat(timespec='seconds')}] {msg}", file=sys.stderr)

# Pull
log("Pull Aircall (last 60 days)...")
today = dt.datetime.now()
start_60 = (today - dt.timedelta(days=60)).replace(hour=0, minute=0, second=0, microsecond=0)
end_today = today.replace(hour=23, minute=59, second=59)
calls = []
page = 1
while True:
    url = f"https://api.aircall.io/v1/calls?from={int(start_60.timestamp())}&to={int(end_today.timestamp())}&per_page=50&page={page}&order=asc"
    try:
        d = aircall_get(url)
    except Exception as e:
        log(f"Aircall error: {e}")
        break
    chunk = d.get("calls", [])
    if not chunk: break
    calls.extend(chunk)
    if not d.get("meta", {}).get("next_page_link"): break
    page += 1
    time.sleep(0.5)
log(f"  -> {len(calls)} Calls")

log("Pull CRM Candidates...")
candidates = []
page = 1
while page <= 20:
    try:
        d = crm_get(f"https://api.recruitcrm.io/v1/candidates?page={page}&per_page=100")
    except: break
    chunk = d.get("data", [])
    if not chunk: break
    candidates.extend(chunk)
    if not d.get("next_page_url"): break
    page += 1
    time.sleep(0.5)
log(f"  -> {len(candidates)} Candidates")

log("Pull CRM Companies...")
companies = []
page = 1
while page <= 10:
    try:
        d = crm_get(f"https://api.recruitcrm.io/v1/companies?page={page}&per_page=100")
    except: break
    chunk = d.get("data", [])
    if not chunk: break
    companies.extend(chunk)
    if not d.get("next_page_url"): break
    page += 1
    time.sleep(0.5)
log(f"  -> {len(companies)} Companies")

log("Pull CRM Jobs...")
jobs = []
page = 1
while page <= 5:
    try:
        d = crm_get(f"https://api.recruitcrm.io/v1/jobs?page={page}&per_page=100")
    except: break
    chunk = d.get("data", [])
    if not chunk: break
    jobs.extend(chunk)
    if not d.get("next_page_url"): break
    page += 1
    time.sleep(0.5)
log(f"  -> {len(jobs)} Jobs")

# Assignments fuer letzte 60 Tage + Open Jobs
cutoff = today - dt.timedelta(days=60)
relevant_jobs = [j for j in jobs if j.get("slug") and (
    (parse_dt(j.get("created_on")) and parse_dt(j.get("created_on")) >= cutoff)
    or (j.get("job_status") or {}).get("label") == "Open"
)]
relevant_jobs.sort(key=lambda j: j.get("created_on") or "", reverse=True)
relevant_jobs = relevant_jobs[:50]  # cap

log(f"Pull Assignments fuer {len(relevant_jobs)} Jobs...")
assignments = []
for j in relevant_jobs:
    slug = j.get("slug")
    try:
        d = crm_get(f"https://api.recruitcrm.io/v1/jobs/{slug}/assigned-candidates?page=1&per_page=50")
        assignments.extend(d.get("data", []))
    except: pass
    time.sleep(0.5)
log(f"  -> {len(assignments)} Assignments")

# Aggregate fuer Streamlit
def aggregate_for_period(person, start, end):
    ac_id = USERS[person]["aircall_id"]
    crm_id = USERS[person]["crm_id"]
    u = {
        "outbound": 0, "wirk_calls": 0, "answered_outbound": 0,
        "duration_sec": 0, "total_calls": 0,
        "neue_kandidaten": 0, "neue_jobs": 0, "neue_kunden": 0,
        "assignments": 0, "sendouts": 0, "interviews": 0, "deals": 0,
        "per_day_calls": {}, "per_day_wirk": {}, "per_day_kandidaten": {},
    }
    for c in calls:
        user = c.get("user") or {}
        if user.get("id") != ac_id: continue
        started = c.get("started_at")
        if not started: continue
        d_call = dt.datetime.fromtimestamp(started)
        if not (start <= d_call <= end): continue
        day = d_call.strftime("%Y-%m-%d")
        u["total_calls"] += 1
        u["per_day_calls"][day] = u["per_day_calls"].get(day, 0) + 1
        u["duration_sec"] += c.get("duration") or 0
        if c.get("direction") == "outbound":
            u["outbound"] += 1
            if c.get("answered_at"): u["answered_outbound"] += 1
        if (c.get("duration") or 0) >= 120:
            u["wirk_calls"] += 1
            u["per_day_wirk"][day] = u["per_day_wirk"].get(day, 0) + 1
    for cand in candidates:
        if cand.get("owner") != crm_id: continue
        cdt = parse_dt(cand.get("created_on"))
        if cdt and start <= cdt <= end:
            u["neue_kandidaten"] += 1
            u["per_day_kandidaten"][cdt.strftime("%Y-%m-%d")] = u["per_day_kandidaten"].get(cdt.strftime("%Y-%m-%d"), 0) + 1
    for comp in companies:
        if comp.get("owner") != crm_id: continue
        cdt = parse_dt(comp.get("created_on"))
        if cdt and start <= cdt <= end:
            u["neue_kunden"] += 1
    for job in jobs:
        if job.get("owner") != crm_id: continue
        jdt = parse_dt(job.get("created_on"))
        if jdt and start <= jdt <= end:
            u["neue_jobs"] += 1
    for asg in assignments:
        cand = asg.get("candidate") or {}
        if cand.get("owner") != crm_id: continue
        sdt = parse_dt(asg.get("stage_date"))
        if not sdt or not (start <= sdt <= end): continue
        sid = (asg.get("status") or {}).get("status_id")
        if sid in ASSIGNMENT_STATUS: u["assignments"] += 1
        if sid in ASSIGNMENT_STATUS: u["sendouts"] += 1
        if sid in INTERVIEW_STATUS: u["interviews"] += 1
        if sid in DEAL_STATUS: u["deals"] += 1
    u["connect_rate"] = (u["answered_outbound"] / u["outbound"] * 100) if u["outbound"] else 0
    u["avg_dur_min"] = (u["duration_sec"] / u["total_calls"] / 60) if u["total_calls"] else 0
    return u

# Mehrere KWs vorberechnen (KW23 = aktuelle, KW22 = letzte, KW21 = vor 2, KW20)
out_data = {
    "generated_at": today.replace(microsecond=0).isoformat(),
    "weeks": {},
    "month": {},
}
for offset in range(0, 5):
    week_anchor = today - dt.timedelta(weeks=offset)
    week_start = (week_anchor - dt.timedelta(days=week_anchor.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    week_end = week_start + dt.timedelta(days=6, hours=23, minutes=59, seconds=59)
    kw = week_start.isocalendar().week
    year = week_start.isocalendar().year
    key = f"{year}-KW{kw:02d}"
    out_data["weeks"][key] = {
        "start": week_start.isoformat(),
        "end": week_end.isoformat(),
        "Kevin": aggregate_for_period("Kevin", week_start, week_end),
        "Robin": aggregate_for_period("Robin", week_start, week_end),
    }
# Tagesaggregate fuer alle Tage der vorberechneten Wochen (Tages-To-Dos + Wochenansicht)
out_data["days"] = {}
for key, w in out_data["weeks"].items():
    ws = dt.datetime.fromisoformat(w["start"])
    for i in range(7):
        day_start = ws + dt.timedelta(days=i)
        if day_start.date() > today.date():
            break
        day_end = day_start.replace(hour=23, minute=59, second=59)
        dkey = day_start.strftime("%Y-%m-%d")
        out_data["days"][dkey] = {
            "Kevin": aggregate_for_period("Kevin", day_start, day_end),
            "Robin": aggregate_for_period("Robin", day_start, day_end),
        }

# Monatsdaten (current)
month_start = today.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
next_month = month_start.replace(day=28) + dt.timedelta(days=4)
month_end = (next_month - dt.timedelta(days=next_month.day)).replace(hour=23, minute=59, second=59)
out_data["month"] = {
    "start": month_start.isoformat(),
    "end": month_end.isoformat(),
    "Kevin": aggregate_for_period("Kevin", month_start, month_end),
    "Robin": aggregate_for_period("Robin", month_start, month_end),
}

# Save
out_path = COCKPIT_DIR / "cockpit_data.json"
out_path.write_text(json.dumps(out_data, indent=2))
log(f"OK: cockpit_data.json geschrieben ({out_path.stat().st_size} Bytes)")
print(json.dumps({"generated_at": out_data["generated_at"], "weeks": list(out_data["weeks"].keys())}))
