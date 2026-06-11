"""Gemeinsame Logik fuer Synergy Cockpit (Daten-Pull, Aggregation, UI-Helper)."""
import os, json, base64, time, urllib.request, urllib.parse
import datetime as dt
from collections import Counter
from pathlib import Path
import streamlit as st

# ============== CONFIG ==============

USERS = {
    "Kevin": {"aircall_id": 1301014, "crm_id": 99698, "color": "#3b82f6", "emoji": "👤"},
    "Robin": {"aircall_id": 1561108, "crm_id": 100690, "color": "#10b981", "emoji": "👤"},
}

TARGETS = {
    "weekly": {
        "outbound": 225, "wirk_calls": 25, "neue_kandidaten": 18,
        "assignments": 8, "sendouts": 6, "interviews": 4, "neue_jobs": 5,
    },
    "monthly": {
        "deals": 2, "interviews": 16, "sendouts": 24,
        "neue_jobs": 20, "neue_kunden": 4,
    },
    # Wochenziel / 5 Arbeitstage, krumme Werte aufgerundet
    "daily": {
        "outbound": 45, "wirk_calls": 5, "neue_kandidaten": 4,
        "assignments": 2, "sendouts": 1, "neue_jobs": 1,
    },
}

# Reihenfolge + Labels fuer Tages-To-Dos und Wochenansicht
METRICS = [
    ("outbound", "📞", "Cold Calls"),
    ("wirk_calls", "☎️", "Wirk-Calls"),
    ("neue_kandidaten", "👥", "Kandidaten ins CRM"),
    ("assignments", "🔗", "Kandidaten auf Jobs"),
    ("sendouts", "📤", "Sendouts"),
    ("neue_jobs", "📋", "Neue Jobs"),
]

ASSIGNMENT_STATUS = {1, 613655, 613656, 613657, 613658, 613659, 613660, 613661, 613662, 8}
INTERVIEW_STATUS = {613655, 613656, 613657}
SENDOUT_STATUS = ASSIGNMENT_STATUS
DEAL_STATUS = {8}

# ============== CREDENTIALS ==============

def get_secret(key):
    try:
        return st.secrets[key]
    except Exception:
        pass
    env_path = Path.home() / ".tracker.env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip()
    return os.environ.get(key)

AIRCALL_ID = get_secret("AIRCALL_API_ID")
AIRCALL_TOKEN = get_secret("AIRCALL_API_TOKEN")
CRM_TOKEN = get_secret("RECRUITCRM_API_TOKEN")

# ============== HTTP ==============

def aircall_get(url):
    auth = base64.b64encode(f"{AIRCALL_ID}:{AIRCALL_TOKEN}".encode()).decode()
    req = urllib.request.Request(url, headers={"Authorization": f"Basic {auth}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)

def crm_get(url, retries=3):
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {CRM_TOKEN}",
        "Accept": "application/json",
    })
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
    try:
        return dt.datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")
    except Exception:
        return None

@st.cache_data(ttl=120)
def load_pauses():
    """Laedt Abwesenheits-Tage pro Person aus pauses.json."""
    p = Path(__file__).parent / "pauses.json"
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text())
        # entfernt _help-Key
        return {k: v for k, v in data.items() if not k.startswith("_")}
    except Exception:
        return {}

@st.cache_data(ttl=120)
def load_cockpit_cache():
    """Laedt vorberechnete Aggregate aus cockpit_data.json. Gibt None zurueck wenn nicht da."""
    p = Path(__file__).parent / "cockpit_data.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None

def get_period_from_cache(person, period_start, period_end):
    """Holt Aggregate fuer eine Person/Periode aus dem Cache. None wenn nicht gefunden.

    Wenn der Nutzer 'Daten jetzt live ziehen' geklickt hat (force_live), geben wir
    bewusst None zurueck, damit der Live-Pull-Fallback (load_all_data) greift.
    """
    if st.session_state.get("force_live"):
        return None
    data = load_cockpit_cache()
    if not data:
        return None
    start_d = period_start.strftime("%Y-%m-%d")
    end_d = period_end.strftime("%Y-%m-%d")
    # KW-Match
    for key, w in data.get("weeks", {}).items():
        if w["start"][:10] == start_d and w["end"][:10] == end_d:
            return w.get(person)
    # Monatsmatch
    m = data.get("month", {})
    if m.get("start", "")[:10] == start_d and m.get("end", "")[:10] == end_d:
        return m.get(person)
    return None

def get_day_from_cache(person, day_str):
    """Holt das Tagesaggregat einer Person aus cockpit_data.json. None wenn nicht da."""
    if st.session_state.get("force_live"):
        return None
    data = load_cockpit_cache()
    if not data:
        return None
    return (data.get("days", {}).get(day_str) or {}).get(person)

def get_week_days(person, week_start, today):
    """Tagesaggregate Mo-Fr einer Woche als dict {YYYY-MM-DD: agg|None}.

    Cache zuerst, Live-Pull als Fallback fuer fehlende vergangene Tage.
    Zukunftstage bleiben None.
    """
    days = {}
    missing = []
    for i in range(5):
        d = week_start + dt.timedelta(days=i)
        key = d.strftime("%Y-%m-%d")
        if d.date() > today.date():
            days[key] = None
            continue
        agg = get_day_from_cache(person, key)
        if agg is None:
            missing.append((key, d))
        days[key] = agg
    if missing:
        data = load_all_data(today.replace(microsecond=0).isoformat())
        for key, d in missing:
            day_start = d.replace(hour=0, minute=0, second=0, microsecond=0)
            day_end = d.replace(hour=23, minute=59, second=59, microsecond=0)
            days[key] = aggregate_person(person, data, day_start, day_end)
    return days

def is_pause_day(person, day_str):
    """True wenn die Person an diesem Tag (YYYY-MM-DD) eine Pause hat."""
    pauses = load_pauses()
    person_pauses = pauses.get(person, [])
    return any(p.get("date") == day_str for p in person_pauses)

def pause_reason(person, day_str):
    pauses = load_pauses()
    for p in pauses.get(person, []):
        if p.get("date") == day_str:
            return p.get("reason", "Pause")
    return ""

# ============== DATA PULL (cached, geteilt zwischen Pages) ==============

@st.cache_data(ttl=3600, show_spinner="Daten werden geladen (Aircall)...")
def fetch_aircall(from_ts, to_ts):
    calls = []
    page = 1
    while True:
        params = urllib.parse.urlencode({"from": from_ts, "to": to_ts, "per_page": 50, "page": page, "order": "asc"})
        try:
            d = aircall_get(f"https://api.aircall.io/v1/calls?{params}")
        except Exception:
            break
        chunk = d.get("calls", [])
        if not chunk: break
        calls.extend(chunk)
        if not d.get("meta", {}).get("next_page_link"): break
        page += 1
        time.sleep(0.4)
    return calls

@st.cache_data(ttl=3600, show_spinner="Daten werden geladen (Kandidaten)...")
def fetch_candidates(max_pages=20):
    out = []
    page = 1
    while page <= max_pages:
        try:
            d = crm_get(f"https://api.recruitcrm.io/v1/candidates?page={page}&per_page=100")
        except Exception:
            break
        chunk = d.get("data", [])
        if not chunk: break
        out.extend(chunk)
        if not d.get("next_page_url"): break
        page += 1
        time.sleep(0.3)
    return out

@st.cache_data(ttl=3600, show_spinner="Daten werden geladen (Companies)...")
def fetch_companies(max_pages=10):
    out = []
    page = 1
    while page <= max_pages:
        try:
            d = crm_get(f"https://api.recruitcrm.io/v1/companies?page={page}&per_page=100")
        except Exception:
            break
        chunk = d.get("data", [])
        if not chunk: break
        out.extend(chunk)
        if not d.get("next_page_url"): break
        page += 1
        time.sleep(0.3)
    return out

@st.cache_data(ttl=3600, show_spinner="Daten werden geladen (Jobs)...")
def fetch_jobs(max_pages=5):
    out = []
    page = 1
    while page <= max_pages:
        try:
            d = crm_get(f"https://api.recruitcrm.io/v1/jobs?page={page}&per_page=100")
        except Exception:
            break
        chunk = d.get("data", [])
        if not chunk: break
        out.extend(chunk)
        if not d.get("next_page_url"): break
        page += 1
        time.sleep(0.3)
    return out

@st.cache_data(ttl=3600, show_spinner="Daten werden geladen (Assignments)...")
def fetch_assignments(job_slugs):
    out = []
    for slug in job_slugs:
        if not slug: continue
        page = 1
        while page <= 3:
            try:
                d = crm_get(f"https://api.recruitcrm.io/v1/jobs/{slug}/assigned-candidates?page={page}&per_page=50")
            except Exception:
                break
            chunk = d.get("data", [])
            if not chunk: break
            out.extend(chunk)
            if not d.get("next_page_url"): break
            page += 1
            time.sleep(0.4)
        time.sleep(0.4)
    return out

@st.cache_data(ttl=900)
def load_all_data(today_iso):
    """Pullt alle Daten in einem Aufwasch, gibt strukturierte Form zurueck."""
    today = dt.datetime.strptime(today_iso, "%Y-%m-%dT%H:%M:%S")
    month_start = today.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    next_month = month_start.replace(day=28) + dt.timedelta(days=4)
    month_end = (next_month - dt.timedelta(days=next_month.day)).replace(hour=23, minute=59, second=59)
    calls = fetch_aircall(int(month_start.timestamp()), int(month_end.timestamp()))
    candidates = fetch_candidates()
    companies = fetch_companies()
    jobs = fetch_jobs()
    cutoff = (today - dt.timedelta(days=90))
    # Sort by created_on desc, take top 30 (most recent) - schneller Pull
    relevant_jobs = [j for j in jobs if j.get("slug") and (
        (parse_dt(j.get("created_on")) and parse_dt(j.get("created_on")) >= cutoff)
        or (j.get("job_status") or {}).get("label") == "Open"
    )]
    relevant_jobs.sort(key=lambda j: j.get("created_on") or "", reverse=True)
    relevant_slugs = [j.get("slug") for j in relevant_jobs[:50]]
    assignments = fetch_assignments(tuple(relevant_slugs))
    return {
        "calls": calls,
        "candidates": candidates,
        "companies": companies,
        "jobs": jobs,
        "assignments": assignments,
    }

# ============== AGGREGATION ==============

def in_range(d, start, end):
    return d is not None and start <= d <= end

def aggregate_person(person, data, period_start, period_end):
    ac_id = USERS[person]["aircall_id"]
    crm_id = USERS[person]["crm_id"]
    u = {
        "outbound": 0, "wirk_calls": 0, "answered_outbound": 0,
        "duration_sec": 0, "total_calls": 0,
        "neue_kandidaten": 0, "neue_jobs": 0, "neue_kunden": 0,
        "assignments": 0, "sendouts": 0, "interviews": 0, "deals": 0,
        "per_day_calls": Counter(), "per_day_wirk": Counter(),
        "per_day_kandidaten": Counter(),
    }
    for c in data["calls"]:
        user = c.get("user") or {}
        if user.get("id") != ac_id: continue
        started = c.get("started_at")
        if not started: continue
        d = dt.datetime.fromtimestamp(started)
        if not in_range(d, period_start, period_end): continue
        day = d.strftime("%Y-%m-%d")
        u["total_calls"] += 1
        u["per_day_calls"][day] += 1
        u["duration_sec"] += c.get("duration") or 0
        if c.get("direction") == "outbound":
            u["outbound"] += 1
            if c.get("answered_at"): u["answered_outbound"] += 1
        if (c.get("duration") or 0) >= 120:
            u["wirk_calls"] += 1
            u["per_day_wirk"][day] += 1
    for cand in data["candidates"]:
        if cand.get("owner") != crm_id: continue
        cdt = parse_dt(cand.get("created_on"))
        if in_range(cdt, period_start, period_end):
            u["neue_kandidaten"] += 1
            u["per_day_kandidaten"][cdt.strftime("%Y-%m-%d")] += 1
    for comp in data["companies"]:
        if comp.get("owner") != crm_id: continue
        cdt = parse_dt(comp.get("created_on"))
        if in_range(cdt, period_start, period_end):
            u["neue_kunden"] += 1
    for job in data["jobs"]:
        if job.get("owner") != crm_id: continue
        jdt = parse_dt(job.get("created_on"))
        if in_range(jdt, period_start, period_end):
            u["neue_jobs"] += 1
    for asg in data["assignments"]:
        cand = asg.get("candidate") or {}
        if cand.get("owner") != crm_id: continue
        sdt = parse_dt(asg.get("stage_date"))
        if not in_range(sdt, period_start, period_end): continue
        sid = (asg.get("status") or {}).get("status_id")
        if sid in ASSIGNMENT_STATUS: u["assignments"] += 1
        if sid in SENDOUT_STATUS: u["sendouts"] += 1
        if sid in INTERVIEW_STATUS: u["interviews"] += 1
        if sid in DEAL_STATUS: u["deals"] += 1
    u["connect_rate"] = (u["answered_outbound"] / u["outbound"] * 100) if u["outbound"] else 0
    u["avg_dur_min"] = (u["duration_sec"] / u["total_calls"] / 60) if u["total_calls"] else 0
    return u

# ============== TIME WINDOWS ==============

def get_windows(today):
    week_start = (today - dt.timedelta(days=today.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    week_end = week_start + dt.timedelta(days=6, hours=23, minutes=59, seconds=59)
    month_start = today.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    next_month = month_start.replace(day=28) + dt.timedelta(days=4)
    month_end = (next_month - dt.timedelta(days=next_month.day)).replace(hour=23, minute=59, second=59)
    return week_start, week_end, month_start, month_end

# ============== UI HELPERS ==============

def clean_html(html):
    """Entfernt Leading-Whitespace pro Zeile, sonst rendert Streamlit-Markdown das als Code-Block."""
    return " ".join(line.strip() for line in html.split("\n") if line.strip())

def status_color(pct):
    if pct >= 1.0: return "#22c55e"
    if pct >= 0.5: return "#f59e0b"
    return "#ef4444"

def status_label(pct):
    if pct >= 1.0: return "ON TRACK"
    if pct >= 0.5: return "ACHTUNG"
    return "ESKALATION"

def big_kpi_card(label, value, target, icon):
    pct = (value / target) if target else 0
    pct_w = min(pct * 100, 100)
    color = status_color(pct)
    label_status = status_label(pct)
    return clean_html(f"""
    <div style="background: white; padding: 22px; border-radius: 14px; box-shadow: 0 1px 4px rgba(0,0,0,0.06); margin-bottom: 14px; border-left: 5px solid {color};">
      <div style="display: flex; justify-content: space-between; align-items: flex-start;">
        <div style="font-size: 13px; font-weight: 600; color: #64748b; text-transform: uppercase; letter-spacing: 0.6px;">{icon} {label}</div>
        <div style="font-size: 11px; font-weight: 700; color: {color}; background: {color}22; padding: 3px 10px; border-radius: 10px;">{label_status}</div>
      </div>
      <div style="font-size: 44px; font-weight: 800; color: #0f172a; margin: 6px 0 0 0; line-height: 1;">
        {value}<span style="font-size: 22px; color: #94a3b8; font-weight: 500;"> / {target}</span>
      </div>
      <div style="background: #f1f5f9; border-radius: 8px; height: 10px; overflow: hidden; margin-top: 14px;">
        <div style="background: {color}; width: {pct_w}%; height: 100%; transition: width 0.5s;"></div>
      </div>
      <div style="font-size: 12px; color: #64748b; margin-top: 6px; font-weight: 500;">{int(pct*100)} % erreicht</div>
    </div>
    """)

def deals_hero(person, value, target, days_left):
    pct = (value / target) if target else 0
    pct_w = min(pct * 100, 100)
    color = status_color(pct)
    label_status = status_label(pct)
    name = person.upper()
    return clean_html(f"""
    <div style="background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%); padding: 32px 28px; border-radius: 16px; color: white; box-shadow: 0 4px 16px rgba(0,0,0,0.15); margin-bottom: 24px;">
      <div style="font-size: 12px; font-weight: 700; color: #94a3b8; letter-spacing: 2px; margin-bottom: 4px;">{name}</div>
      <div style="display: flex; justify-content: space-between; align-items: center;">
        <div style="font-size: 14px; font-weight: 600; color: #cbd5e1; text-transform: uppercase; letter-spacing: 0.8px;">🎯 DEALS DIESEN MONAT</div>
        <div style="font-size: 11px; font-weight: 700; color: white; background: {color}; padding: 4px 12px; border-radius: 12px;">{label_status}</div>
      </div>
      <div style="font-size: 72px; font-weight: 900; margin: 4px 0; line-height: 1;">
        {value}<span style="font-size: 32px; color: #94a3b8; font-weight: 600;"> / {target}</span>
      </div>
      <div style="background: rgba(255,255,255,0.1); border-radius: 8px; height: 12px; overflow: hidden; margin-top: 18px;">
        <div style="background: {color}; width: {pct_w}%; height: 100%;"></div>
      </div>
      <div style="font-size: 14px; color: #cbd5e1; margin-top: 10px; font-weight: 500;">
        {int(pct*100)} % erreicht · noch {days_left} Tage im Monat
      </div>
    </div>
    """)

DAY_NAMES = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag"]
DAY_SHORT = ["Mo", "Di", "Mi", "Do", "Fr"]

def render_daily_todos(person, day_agg, day_dt, is_today=True):
    """Tages-To-Do-Karte: Checkliste aller 6 Tagesziele + Fortschritts-Ring."""
    accent = USERS[person]["color"]
    daily = TARGETS["daily"]
    day_str = day_dt.strftime("%Y-%m-%d")

    day_name = DAY_NAMES[day_dt.weekday()] if day_dt.weekday() < 5 else day_dt.strftime("%d.%m.")

    if is_pause_day(person, day_str):
        reason = pause_reason(person, day_str)
        return clean_html(f"""
        <div style="background: linear-gradient(135deg, #334155 0%, #475569 100%); padding: 28px; border-radius: 18px; color: white; margin-bottom: 20px; box-shadow: 0 8px 24px rgba(15,23,42,0.18);">
          <div style="font-size: 12px; font-weight: 700; color: #cbd5e1; letter-spacing: 2px;">{'HEUTE' if is_today else (day_name + ' ' + day_dt.strftime('%d.%m.')).upper()}</div>
          <div style="font-size: 28px; font-weight: 800; margin-top: 6px;">⏸️ Pause: {reason}</div>
          <div style="font-size: 14px; color: #cbd5e1; margin-top: 4px;">Keine Tagesziele für diesen Tag. Gute Erholung!</div>
        </div>
        """)

    done = 0
    rows_html = ""
    for key, icon, label in METRICS:
        target = daily[key]
        val = (day_agg or {}).get(key, 0)
        ok = val >= target
        if ok:
            done += 1
        pct = min(val / target * 100, 100) if target else 0
        check = "✅" if ok else "⬜"
        val_color = "#4ade80" if ok else "white"
        bar_color = "#4ade80" if ok else (accent if pct > 0 else "rgba(255,255,255,0.15)")
        rows_html += f"""
        <div style="display: flex; align-items: center; gap: 12px; padding: 9px 0; border-bottom: 1px solid rgba(255,255,255,0.08);">
          <div style="font-size: 17px; width: 24px;">{check}</div>
          <div style="flex: 1; font-size: 14px; font-weight: 600; color: {'#94a3b8' if ok else '#e2e8f0'}; {'text-decoration: line-through; text-decoration-color: rgba(148,163,184,0.6);' if ok else ''}">{icon} {label}</div>
          <div style="width: 90px; background: rgba(255,255,255,0.1); border-radius: 4px; height: 7px; overflow: hidden;">
            <div style="background: {bar_color}; width: {pct}%; height: 100%;"></div>
          </div>
          <div style="font-size: 15px; font-weight: 800; color: {val_color}; width: 64px; text-align: right;">{val} <span style="font-size: 12px; color: #64748b; font-weight: 600;">/ {target}</span></div>
        </div>
        """

    total = len(METRICS)
    ring_pct = done / total * 100
    ring_color = "#4ade80" if done == total else accent
    if done == total:
        headline, sub = "Alle Tagesziele erledigt! 🎉", "Starker Tag. Weiter so."
    elif done >= total / 2:
        headline, sub = f"{done} von {total} Zielen geschafft", "Gut dabei, der Rest ist machbar."
    else:
        headline, sub = f"{done} von {total} Zielen geschafft", "Da geht heute noch was!"
    title = f"HEUTE · {day_name}, {day_dt.strftime('%d.%m.')}" if is_today else f"{day_name}, {day_dt.strftime('%d.%m.')}".upper()

    return clean_html(f"""
    <div style="background: linear-gradient(135deg, #0f172a 0%, #1e293b 60%, #243049 100%); padding: 26px 28px; border-radius: 18px; color: white; margin-bottom: 20px; box-shadow: 0 8px 24px rgba(15,23,42,0.22); border: 1px solid rgba(255,255,255,0.06);">
      <div style="display: flex; gap: 28px; align-items: center; flex-wrap: wrap;">
        <div style="flex: 0 0 auto; text-align: center;">
          <div style="font-size: 11px; font-weight: 700; color: {accent}; letter-spacing: 2px; margin-bottom: 12px;">{title}</div>
          <div style="width: 118px; height: 118px; border-radius: 50%; background: conic-gradient({ring_color} {ring_pct}%, rgba(255,255,255,0.1) 0); display: flex; align-items: center; justify-content: center; margin: 0 auto;">
            <div style="width: 92px; height: 92px; border-radius: 50%; background: #0f172a; display: flex; flex-direction: column; align-items: center; justify-content: center;">
              <div style="font-size: 28px; font-weight: 900; line-height: 1;">{done}<span style="font-size: 16px; color: #64748b; font-weight: 700;">/{total}</span></div>
              <div style="font-size: 9px; font-weight: 700; color: #94a3b8; letter-spacing: 1.5px; margin-top: 3px;">ZIELE</div>
            </div>
          </div>
          <div style="font-size: 13px; font-weight: 700; margin-top: 12px;">{headline}</div>
          <div style="font-size: 11px; color: #94a3b8; margin-top: 2px;">{sub}</div>
        </div>
        <div style="flex: 1; min-width: 320px;">
          <div style="font-size: 11px; font-weight: 700; color: #94a3b8; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 4px;">✔️ Deine Tages-To-Dos</div>
          {rows_html}
        </div>
      </div>
    </div>
    """)

def render_week_view(person, days, week_start, today, week_agg):
    """Wochenansicht: Grid Metrik x Wochentag mit Ampel-Zellen, Wochensumme und Ziel."""
    daily = TARGETS["daily"]
    weekly = TARGETS["weekly"]
    day_dates = [week_start + dt.timedelta(days=i) for i in range(5)]
    today_str = today.strftime("%Y-%m-%d")

    header_cells = "<div></div>"
    for i, d in enumerate(day_dates):
        is_today = d.strftime("%Y-%m-%d") == today_str
        bg = "background: #0f172a; color: white; border-radius: 8px;" if is_today else "color: #64748b;"
        header_cells += f"""<div style="text-align: center; padding: 8px 2px; font-size: 12px; font-weight: 800; {bg}">{DAY_SHORT[i]}<div style="font-size: 10px; font-weight: 600; opacity: 0.7;">{d.strftime('%d.%m.')}</div></div>"""
    header_cells += """<div style="text-align: center; padding: 8px 2px; font-size: 12px; font-weight: 800; color: #0f172a;">Σ Woche</div>"""

    body = ""
    for key, icon, label in METRICS:
        cells = f"""<div style="display: flex; align-items: center; font-size: 13px; font-weight: 600; color: #334155; padding: 4px 6px 4px 0;">{icon}&nbsp;{label}</div>"""
        for d in day_dates:
            dkey = d.strftime("%Y-%m-%d")
            agg = days.get(dkey)
            is_today = dkey == today_str
            border = f"box-shadow: inset 0 0 0 2px #0f172a;" if is_today else ""
            if d.date() > today.date():
                cells += f"""<div style="text-align: center; padding: 10px 2px; background: #f8fafc; border-radius: 8px; color: #cbd5e1; font-size: 14px;">·</div>"""
                continue
            if is_pause_day(person, dkey):
                cells += f"""<div style="text-align: center; padding: 10px 2px; background: #e2e8f0; border-radius: 8px; font-size: 13px;" title="{pause_reason(person, dkey)}">⏸️</div>"""
                continue
            val = (agg or {}).get(key, 0)
            target = daily[key]
            pct = val / target if target else 0
            if pct >= 1.0:
                bg, fg = "#dcfce7", "#166534"
            elif pct >= 0.5:
                bg, fg = "#fef3c7", "#854d0e"
            else:
                bg, fg = "#fee2e2", "#991b1b"
            cells += f"""<div style="text-align: center; padding: 10px 2px; background: {bg}; border-radius: 8px; color: {fg}; font-size: 15px; font-weight: 800; {border}">{val}</div>"""
        wval = week_agg.get(key, 0)
        wtarget = weekly[key]
        wcolor = status_color((wval / wtarget) if wtarget else 0)
        cells += f"""<div style="text-align: center; padding: 10px 2px; background: #0f172a; border-radius: 8px; color: white; font-size: 14px; font-weight: 800;">{wval}<span style="font-size: 11px; color: #94a3b8; font-weight: 600;"> / {wtarget}</span><div style="height: 4px; background: rgba(255,255,255,0.15); border-radius: 2px; margin: 5px 6px 0 6px; overflow: hidden;"><div style="background: {wcolor}; width: {min((wval / wtarget) if wtarget else 0, 1) * 100}%; height: 100%;"></div></div></div>"""
        body += f"""<div style="display: grid; grid-template-columns: 190px repeat(5, 1fr) 1.4fr; gap: 6px; margin-bottom: 6px;">{cells}</div>"""

    return clean_html(f"""
    <div style="background: white; padding: 22px 24px; border-radius: 18px; box-shadow: 0 1px 4px rgba(0,0,0,0.06); margin-bottom: 20px;">
      <div style="display: grid; grid-template-columns: 190px repeat(5, 1fr) 1.4fr; gap: 6px; margin-bottom: 8px;">{header_cells}</div>
      {body}
      <div style="font-size: 11px; color: #94a3b8; margin-top: 10px;">Ampel pro Tag gegen das Tagesziel (grün = geschafft, gelb = über 50 %, rot = drunter). Heutiger Tag ist umrandet, ⏸️ = Pause.</div>
    </div>
    """)

def page_css():
    return """
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap');
    html, body, [data-testid="stAppViewContainer"] * { font-family: 'Inter', -apple-system, sans-serif; }
    .main { background: #f8fafc; }
    [data-testid="stAppViewContainer"] { background: linear-gradient(180deg, #eef2f7 0%, #f8fafc 240px); }
    .block-container { padding-top: 1.5rem; padding-bottom: 2rem; max-width: 1200px; }
    [data-testid="stHeader"] { background: transparent; }
    [data-testid="stSidebar"] { background: #ffffff; border-right: 1px solid #e2e8f0; }
    h1 { font-size: 30px; font-weight: 900; color: #0f172a; margin-bottom: 0; letter-spacing: -0.5px; }
    h2 { font-size: 15px; font-weight: 800; color: #475569; margin-top: 1.6rem; margin-bottom: 0.75rem; text-transform: uppercase; letter-spacing: 1.2px; }
    [data-testid="stMetric"] { background: white; padding: 16px 18px; border-radius: 14px; box-shadow: 0 1px 4px rgba(0,0,0,0.06); }
    [data-testid="stMetricLabel"] { font-weight: 600; color: #64748b; }
</style>
"""

def render_person_page(person, today=None):
    """Volle Person-Seite mit Hero + KPIs + Heatmap."""
    if today is None:
        today = dt.datetime.now()
    week_start, week_end, month_start, month_end = get_windows(today)
    days_left = max(0, (month_end.date() - today.date()).days)

    # Versuche zuerst aus dem Cache (cockpit_data.json) zu laden - instant
    week = get_period_from_cache(person, week_start, week_end)
    month = get_period_from_cache(person, month_start, month_end)

    # Fallback auf Live-Pull wenn Cache nicht da oder Periode nicht im Cache
    if week is None or month is None:
        data = load_all_data(today.replace(microsecond=0).isoformat())
        if week is None:
            week = aggregate_person(person, data, week_start, week_end)
        if month is None:
            month = aggregate_person(person, data, month_start, month_end)
        cache_info = "live"
    else:
        cache_info = "cache"

    st.markdown(f"# {person} · Wo stehst du?")
    st.caption(f"KW{week_start.isocalendar().week} · {week_start.strftime('%d.%m.')} bis {week_end.strftime('%d.%m.%Y')} · Stand {today.strftime('%H:%M')}")

    # Tages-To-Dos: immer ganz oben. In der aktuellen Woche = heute (am Wochenende
    # Rueckblick auf Freitag), in einer Vergangenheits-Woche = deren Freitag.
    now = dt.datetime.now()
    is_current_week = week_start.date() <= now.date() <= week_end.date()
    todo_day = today
    if todo_day.weekday() >= 5:
        todo_day = todo_day - dt.timedelta(days=todo_day.weekday() - 4)
    todo_key = todo_day.strftime("%Y-%m-%d")
    day_agg = get_day_from_cache(person, todo_key)
    if day_agg is None:
        data = load_all_data(today.replace(microsecond=0).isoformat())
        day_agg = aggregate_person(person, data,
                                   todo_day.replace(hour=0, minute=0, second=0, microsecond=0),
                                   todo_day.replace(hour=23, minute=59, second=59, microsecond=0))
    st.markdown(render_daily_todos(person, day_agg, todo_day, is_today=(is_current_week and now.date() == todo_day.date())), unsafe_allow_html=True)

    # Wochenansicht: jeder Tag der Woche gegen das Tagesziel
    st.markdown("## 📅 Wochenansicht · Tag für Tag")
    week_days = get_week_days(person, week_start, today)
    st.markdown(render_week_view(person, week_days, week_start, today, week), unsafe_allow_html=True)

    # Hero
    st.markdown(deals_hero(person, month["deals"], TARGETS["monthly"]["deals"], days_left), unsafe_allow_html=True)

    # Wochen-KPIs
    st.markdown("## Diese Woche gesamt")
    col1, col2 = st.columns(2)
    with col1:
        st.markdown(big_kpi_card("Cold Calls", week["outbound"], TARGETS["weekly"]["outbound"], "📞"), unsafe_allow_html=True)
        st.markdown(big_kpi_card("Kandidaten ins CRM", week["neue_kandidaten"], TARGETS["weekly"]["neue_kandidaten"], "👥"), unsafe_allow_html=True)
        st.markdown(big_kpi_card("Sendouts", week["sendouts"], TARGETS["weekly"]["sendouts"], "📤"), unsafe_allow_html=True)
    with col2:
        st.markdown(big_kpi_card("Wirk-Calls (≥2 Min)", week["wirk_calls"], TARGETS["weekly"]["wirk_calls"], "☎️"), unsafe_allow_html=True)
        st.markdown(big_kpi_card("Kandidaten auf Jobs", week["assignments"], TARGETS["weekly"]["assignments"], "🔗"), unsafe_allow_html=True)
        st.markdown(big_kpi_card("Neue Jobs angelegt", week["neue_jobs"], TARGETS["weekly"]["neue_jobs"], "📋"), unsafe_allow_html=True)

    # Gespraechsqualitaet
    st.markdown("## Gesprächsqualität")
    qcol1, qcol2, qcol3 = st.columns(3)
    qcol1.metric("Connect-Rate", f"{week['connect_rate']:.0f} %", help="Wieviel Prozent deiner Outbound-Calls angenommen werden. Ziel: 65 % +")
    qcol2.metric("Ø Gesprächsdauer", f"{week['avg_dur_min']:.1f} Min", help="Durchschnittliche Dauer. Unter 1 Min = überwiegend Mailbox.")
    qcol3.metric("Interviews scheduled", f"{week['interviews']} / {TARGETS['weekly']['interviews']}")

def render_sidebar():
    """Sidebar mit Wochen-Selector. Gibt den gewaehlten 'today' (datetime) zurueck."""
    today = dt.datetime.now()
    today_effective = today  # safe default
    # Standard: Snapshot aus cockpit_data.json. Wird nur fuer EINEN Lauf auf True
    # gesetzt, wenn der Live-Button geklickt wurde (siehe unten). Reset bei jedem Lauf.
    st.session_state["force_live"] = False
    with st.sidebar:
        st.markdown("### Synergy Cockpit")
        st.caption("Zielerreichung Kevin & Robin")
        st.markdown("---")

        # Wochen-Selector
        current_kw = today.isocalendar().week
        options = []
        for offset in range(0, 5):
            d = today - dt.timedelta(weeks=offset)
            kw = d.isocalendar().week
            week_start_d = (d - dt.timedelta(days=d.weekday())).date()
            label = f"KW{kw} ({week_start_d.strftime('%d.%m.')} bis {(week_start_d + dt.timedelta(days=6)).strftime('%d.%m.')})"
            if offset == 0:
                label = "Diese Woche · " + label
            elif offset == 1:
                label = "Letzte Woche · " + label
            options.append((label, offset))

        labels = [o[0] for o in options]
        choice = st.selectbox("Welche Woche?", labels, index=0)
        chosen_offset = dict(options)[choice]

        # today auf Freitag der gewaehlten Woche setzen, damit Wochen-View komplett ist
        if chosen_offset > 0:
            week_anchor = today - dt.timedelta(weeks=chosen_offset)
            week_monday = week_anchor - dt.timedelta(days=week_anchor.weekday())
            today_effective = week_monday + dt.timedelta(days=4, hours=23, minutes=59, seconds=59)
        else:
            today_effective = today

        st.markdown("---")
        if st.button("🔄 Daten jetzt live ziehen", use_container_width=True):
            # Cache leeren -> load_all_data pullt frisch. force_live -> Snapshot
            # wird umgangen. Kein rerun noetig: render_sidebar laeuft vor den
            # Karten, die das Flag im selben Lauf lesen.
            st.cache_data.clear()
            st.session_state["force_live"] = True

        # Daten-Stand sichtbar machen, damit klar ist wie frisch die Zahlen sind.
        if st.session_state.get("force_live"):
            st.caption("⏱️ Stand: gerade live aus Aircall + CRM gezogen")
        else:
            cache = load_cockpit_cache()
            gen = (cache or {}).get("generated_at", "")
            if gen:
                try:
                    gen_dt = dt.datetime.strptime(gen[:19], "%Y-%m-%dT%H:%M:%S")
                    st.caption(f"⏱️ Daten-Stand: {gen_dt.strftime('%d.%m. %H:%M')} · Auto-Refresh alle 15 Min")
                except Exception:
                    st.caption("Auto-Refresh alle 15 Min")
            else:
                st.caption("Auto-Refresh alle 15 Min")
        st.markdown("---")
        st.caption("**Daten-Quellen:** Aircall · Recruit CRM")
        st.caption("**Kandidaten-Calls per Handy** sind im Tracking nicht erfasst.")

        # Abwesenheiten
        pauses_data = load_pauses()
        any_pause = any(pauses_data.get(p, []) for p in pauses_data)
        if any_pause:
            st.markdown("---")
            st.markdown("**⏸️ Geplante Pausen**")
            for name, plist in pauses_data.items():
                if plist:
                    for p in plist:
                        st.caption(f"{name} · {p.get('date')} · {p.get('reason','Pause')}")
            st.caption("[Editieren auf GitHub](https://github.com/roliveira-del/synergy-cockpit/edit/main/pauses.json)")
    return today_effective
