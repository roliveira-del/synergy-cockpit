"""Gemeinsame Logik fuer Synergy Cockpit (Daten-Pull, Aggregation, UI-Helper)."""
import os, json, base64, time, urllib.request, urllib.parse
import datetime as dt
from collections import Counter
from pathlib import Path
import streamlit as st

# Streamlit Cloud laeuft auf UTC. Ohne feste Zone waere abends ab 22 Uhr
# deutscher Zeit noch der Vortag "heute" -> falsche Tageszahlen.
try:
    from zoneinfo import ZoneInfo
    _TZ = ZoneInfo("Europe/Berlin")
except Exception:
    _TZ = None

def now_local():
    """Aktuelle Zeit in Europe/Berlin, naiv (alle Vergleiche hier sind naiv)."""
    if _TZ is None:
        return dt.datetime.now()
    return dt.datetime.now(_TZ).replace(tzinfo=None)

def ts_local(ts):
    """Unix-Timestamp -> naive Berliner Zeit."""
    if _TZ is None:
        return dt.datetime.fromtimestamp(ts)
    return dt.datetime.fromtimestamp(ts, _TZ).replace(tzinfo=None)

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

# Immer die Datei aus dem Repo-Head, nicht aus dem Container-Checkout.
CACHE_RAW_URL = "https://raw.githubusercontent.com/roliveira-del/synergy-cockpit/main/cockpit_data.json"

@st.cache_data(ttl=120)
def load_cockpit_cache():
    """Laedt vorberechnete Aggregate aus cockpit_data.json. Gibt None zurueck wenn nicht da.

    Streamlit Cloud zieht neue Commits nur beim Redeploy. Schlaeft die App ein,
    bleibt der Checkout im Container auf dem Stand von vorgestern stehen und das
    Cockpit zeigt Nullen, obwohl der GitHub-Action-Refresh sauber laeuft. Deshalb
    holen wir die Datei direkt von raw.githubusercontent.com (Repo ist public)
    und nutzen den lokalen Checkout nur noch als Notfall-Fallback.
    """
    try:
        # Cache-Buster, sonst liefert das raw-CDN bis zu 5 Minuten alte Staende.
        req = urllib.request.Request(
            f"{CACHE_RAW_URL}?t={int(time.time())}",
            headers={"Cache-Control": "no-cache", "User-Agent": "synergy-cockpit"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        pass
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
        d = ts_local(started)
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
    if pct >= 1.0: return "#10b981"
    if pct >= 0.5: return "#f59e0b"
    return "#f43f5e"

def status_color_pair(pct):
    """(Basisfarbe, hellere Variante) fuer die Verlaeufe der Fortschrittsbalken."""
    if pct >= 1.0: return "#10b981", "#4ade80"
    if pct >= 0.5: return "#f59e0b", "#fbbf24"
    return "#f43f5e", "#fb7185"

def status_label(pct):
    if pct >= 1.0: return "ON TRACK"
    if pct >= 0.5: return "ACHTUNG"
    return "ESKALATION"

def big_kpi_card(label, value, target, icon):
    pct = (value / target) if target else 0
    pct_w = min(pct * 100, 100)
    color, color_2 = status_color_pair(pct)
    label_status = status_label(pct)
    rest = max(0, target - value)
    foot_right = "Ziel erreicht ✓" if rest == 0 else f"noch {rest} bis Ziel"
    return clean_html(f"""
    <div class="sbc-card sbc-kpi" style="--c: {color}; --c2: {color_2};">
      <div class="sbc-row">
        <div class="sbc-kpi-label"><span class="sbc-ico">{icon}</span>{label}</div>
        <div class="sbc-pill">{label_status}</div>
      </div>
      <div class="sbc-kpi-num">{value}<span class="sbc-kpi-target"> / {target}</span></div>
      <div class="sbc-track"><div class="sbc-fill" style="width: {pct_w}%;"></div></div>
      <div class="sbc-kpi-foot"><span>{int(pct*100)} % erreicht</span><span>{foot_right}</span></div>
    </div>
    """)

def deals_hero(person, value, target, days_left):
    pct = (value / target) if target else 0
    pct_w = min(pct * 100, 100)
    color, color_2 = status_color_pair(pct)
    label_status = status_label(pct)
    name = person.upper()
    rest = max(0, target - value)
    rest_txt = "Monatsziel geschafft 🎉" if rest == 0 else f"noch {rest} bis zum Monatsziel"
    return clean_html(f"""
    <div class="sbc-hero" style="--c: {color}; --c2: {color_2};">
      <div class="sbc-hero-eyebrow">{name}</div>
      <div class="sbc-row" style="margin-top: 6px;">
        <div class="sbc-hero-title"><span class="sbc-ico-sm">🎯</span>Deals diesen Monat</div>
        <div class="sbc-pill-solid">{label_status}</div>
      </div>
      <div class="sbc-hero-num">{value}<span> / {target}</span></div>
      <div class="sbc-hero-track"><div class="sbc-fill" style="width: {pct_w}%;"></div></div>
      <div class="sbc-hero-foot">{int(pct*100)} % erreicht · {rest_txt} · noch {days_left} Tage im Monat</div>
    </div>
    """)

DAY_NAMES = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag"]
DAY_SHORT = ["Mo", "Di", "Mi", "Do", "Fr"]

def render_today_banner(person, day_agg, day_dt, is_today=True):
    """Schmaler Heute-Banner: Fortschritts-Ring + Tagesstand. Die grossen Tageszielkarten folgen darunter."""
    accent = USERS[person]["color"]
    daily = TARGETS["daily"]
    day_str = day_dt.strftime("%Y-%m-%d")
    day_name = DAY_NAMES[day_dt.weekday()] if day_dt.weekday() < 5 else day_dt.strftime("%d.%m.")
    title = f"HEUTE · {day_name}, {day_dt.strftime('%d.%m.')}" if is_today else f"{day_name}, {day_dt.strftime('%d.%m.')} (Rückblick)".upper()

    if is_pause_day(person, day_str):
        reason = pause_reason(person, day_str)
        return clean_html(f"""
        <div class="sbc-hero" style="padding: 26px 30px;">
          <div class="sbc-hero-eyebrow">{title}</div>
          <div class="sbc-today-h" style="margin-top: 8px;">⏸️ Pause: {reason}</div>
          <div class="sbc-today-sub">Keine Tagesziele für diesen Tag. Gute Erholung!</div>
        </div>
        """)

    total = len(METRICS)
    done = sum(1 for key, _, _ in METRICS if (day_agg or {}).get(key, 0) >= daily[key])
    ring_pct = done / total * 100
    ring_color = "#4ade80" if done == total else ("#f59e0b" if done >= total / 2 else "#ef4444")
    if done == total:
        headline, sub = "Alle Tagesziele erledigt! 🎉", "Starker Tag. Weiter so."
    elif done >= total / 2:
        headline, sub = f"Noch {total - done} Tagesziele offen", "Gut dabei, der Rest ist machbar."
    elif done > 0:
        headline, sub = f"Noch {total - done} Tagesziele offen", "Zeit, Gas zu geben!"
    else:
        headline, sub = "Noch kein Tagesziel erreicht", "Jetzt schlagartig loslegen! 📞"

    return clean_html(f"""
    <div class="sbc-hero sbc-today" style="--c: {accent}; --p: {ring_pct}%;">
      <div class="sbc-ring" style="--c: {ring_color};">
        <div class="sbc-ring-in">
          <div class="sbc-ring-num">{done}<span>/{total}</span></div>
          <div class="sbc-ring-cap">ZIELE</div>
        </div>
      </div>
      <div style="flex: 1; min-width: 220px;">
        <div class="sbc-today-head">{title}</div>
        <div class="sbc-today-h">{headline}</div>
        <div class="sbc-today-sub">{sub} Unten siehst du jeden Tagesstand live.</div>
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
        cls = "sbc-hcell today" if is_today else "sbc-hcell"
        header_cells += f"""<div class="{cls}">{DAY_SHORT[i]}<small>{d.strftime('%d.%m.')}</small></div>"""
    header_cells += """<div class="sbc-hcell" style="color: #0b1220;">Σ Woche</div>"""

    body = ""
    for key, icon, label in METRICS:
        cells = f"""<div class="sbc-mlabel"><span class="sbc-ico-sm">{icon}</span>{label}</div>"""
        for d in day_dates:
            dkey = d.strftime("%Y-%m-%d")
            agg = days.get(dkey)
            today_cls = " today" if dkey == today_str else ""
            if d.date() > today.date():
                cells += """<div class="sbc-cell void">·</div>"""
                continue
            if is_pause_day(person, dkey):
                cells += f"""<div class="sbc-cell pause" title="{pause_reason(person, dkey)}">⏸️</div>"""
                continue
            val = (agg or {}).get(key, 0)
            target = daily[key]
            pct = val / target if target else 0
            tone = "ok" if pct >= 1.0 else ("mid" if pct >= 0.5 else "low")
            cells += f"""<div class="sbc-cell {tone}{today_cls}">{val}</div>"""
        wval = week_agg.get(key, 0)
        wtarget = weekly[key]
        wpct = (wval / wtarget) if wtarget else 0
        wcolor, wcolor_2 = status_color_pair(wpct)
        cells += f"""<div class="sbc-sum" style="--c: {wcolor}; --c2: {wcolor_2};"><div class="sbc-sum-num">{wval}<span> / {wtarget}</span></div><div class="sbc-sum-track"><div class="sbc-fill" style="width: {min(wpct, 1) * 100}%;"></div></div></div>"""
        body += f"""<div class="sbc-grid" style="margin-bottom: 7px;">{cells}</div>"""

    return clean_html(f"""
    <div class="sbc-card" style="padding: 24px 26px;">
      <div class="sbc-grid" style="margin-bottom: 9px;">{header_cells}</div>
      {body}
      <div class="sbc-legend">Ampel pro Tag gegen das Tagesziel (grün = geschafft, gelb = über 50 %, rot = drunter). Heutiger Tag ist umrandet, ⏸️  = Pause.</div>
    </div>
    """)

def page_css():
    return """
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap');

    /* ---------- Design-Tokens ---------- */
    :root {
        --ink:      #0b1220;
        --ink-2:    #33415580;
        --body:     #334155;
        --muted:    #64748b;
        --faint:    #94a3b8;
        --line:     #e9eef6;
        --surface:  #ffffff;
        --surface-2:#f6f9fd;
        --radius:   20px;
        --radius-s: 12px;
        --shadow-s: 0 1px 2px rgba(16,24,40,.04), 0 1px 3px rgba(16,24,40,.05);
        --shadow-m: 0 1px 2px rgba(16,24,40,.04), 0 18px 36px -20px rgba(16,24,40,.32);
        --shadow-l: 0 2px 4px rgba(16,24,40,.06), 0 32px 60px -28px rgba(15,23,42,.55);
    }

    html, body, [data-testid="stAppViewContainer"] * {
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
        -webkit-font-smoothing: antialiased;
    }
    /* Zahlen mit fester Breite, damit nichts springt wenn Werte hochzaehlen */
    .sbc-num, .sbc-kpi-num, .sbc-hero-num, .sbc-cell, .sbc-sum-num { font-variant-numeric: tabular-nums; }

    [data-testid="stAppViewContainer"] {
        background:
            radial-gradient(1100px 520px at 12% -12%, rgba(59,130,246,.13), transparent 62%),
            radial-gradient(900px 460px at 92% -4%,  rgba(16,185,129,.13), transparent 58%),
            linear-gradient(180deg, #f2f6fc 0%, #f8fafc 420px);
    }
    .block-container { padding-top: 1.6rem; padding-bottom: 3rem; max-width: 1240px; }
    [data-testid="stHeader"] { background: transparent; }
    [data-testid="stSidebar"] {
        background: rgba(255,255,255,.82);
        backdrop-filter: blur(14px);
        border-right: 1px solid var(--line);
    }
    [data-testid="stSidebar"] hr { margin: .9rem 0; border-color: var(--line); }

    h1 { font-size: 32px; font-weight: 900; color: var(--ink); margin-bottom: 0; letter-spacing: -.9px; }
    [data-testid="stHeading"] h2, .stMarkdown h2, h2 {
        font-size: 12.5px !important; font-weight: 800; color: var(--muted);
        margin-top: 2rem; margin-bottom: .8rem; text-transform: uppercase; letter-spacing: 1.6px;
    }
    h2 img { width: 15px !important; height: 15px !important; vertical-align: -2px; }
    [data-testid="stCaptionContainer"] p { color: var(--muted); }

    /* Streamlit-Metriken an das Kartendesign angleichen */
    [data-testid="stMetric"] {
        background: var(--surface); padding: 18px 20px; border-radius: var(--radius-s);
        border: 1px solid var(--line); box-shadow: var(--shadow-s);
    }
    [data-testid="stMetricLabel"] { font-weight: 600; color: var(--muted); }
    [data-testid="stMetricValue"] { font-weight: 800; letter-spacing: -.5px; color: var(--ink); }

    button[kind="secondary"] {
        border-radius: 12px !important; border: 1px solid var(--line) !important;
        font-weight: 700 !important; transition: transform .15s ease, box-shadow .15s ease;
    }
    button[kind="secondary"]:hover { transform: translateY(-1px); box-shadow: var(--shadow-s); }

    /* ---------- Karten ---------- */
    .sbc-card {
        background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius);
        box-shadow: var(--shadow-m); padding: 24px; margin-bottom: 16px; position: relative; overflow: hidden;
        animation: sbcIn .45s cubic-bezier(.22,1,.36,1) both;
    }
    @keyframes sbcIn { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: none; } }
    @media (prefers-reduced-motion: reduce) { .sbc-card, .sbc-fill { animation: none !important; transition: none !important; } }

    /* ---------- KPI-Karte ---------- */
    .sbc-kpi { padding: 20px 22px 18px 22px; }
    .sbc-kpi::before {
        content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 4px;
        background: linear-gradient(180deg, var(--c2), var(--c));
    }
    .sbc-row { display: flex; justify-content: space-between; align-items: center; gap: 12px; }
    .sbc-kpi-label {
        display: flex; align-items: center; gap: 9px; font-size: 12.5px; font-weight: 700;
        color: var(--muted); text-transform: uppercase; letter-spacing: .7px;
    }
    .sbc-ico {
        width: 30px; height: 30px; border-radius: 9px; background: var(--surface-2);
        border: 1px solid var(--line); display: inline-flex; align-items: center; justify-content: center; font-size: 15px;
    }
    .sbc-pill {
        font-size: 10.5px; font-weight: 800; letter-spacing: .6px; padding: 4px 11px; border-radius: 999px;
        color: var(--c); background: #f6f9fd; border: 1px solid var(--line); white-space: nowrap;
        background: color-mix(in srgb, var(--c) 11%, white);
        border-color: color-mix(in srgb, var(--c) 24%, white);
    }
    .sbc-kpi-num { font-size: 46px; font-weight: 850; color: var(--ink); line-height: 1; margin: 12px 0 0; letter-spacing: -2px; }
    .sbc-kpi-target { font-size: 20px; color: var(--faint); font-weight: 600; letter-spacing: -.5px; }
    .sbc-track { background: #eef2f7; border-radius: 999px; height: 8px; overflow: hidden; margin-top: 16px; }
    .sbc-fill {
        height: 100%; border-radius: 999px; background: linear-gradient(90deg, var(--c2), var(--c));
        animation: sbcGrow .7s cubic-bezier(.22,1,.36,1) both;
    }
    @keyframes sbcGrow { from { width: 0 !important; } }
    .sbc-kpi-foot {
        display: flex; justify-content: space-between; font-size: 12px; color: var(--muted);
        margin-top: 9px; font-weight: 600;
    }

    /* ---------- Hero (Deals) ---------- */
    .sbc-hero {
        border-radius: 24px; padding: 30px 32px; color: #fff; margin-bottom: 22px; position: relative; overflow: hidden;
        background:
            radial-gradient(760px 300px at 88% -30%, rgba(56,189,248,.28), transparent 60%),
            radial-gradient(520px 260px at 8% 120%, rgba(129,140,248,.24), transparent 62%),
            linear-gradient(135deg, #0b1220 0%, #16233b 58%, #1d2c48 100%);
        box-shadow: var(--shadow-l);
        animation: sbcIn .45s cubic-bezier(.22,1,.36,1) both;
    }
    .sbc-hero-eyebrow { font-size: 11px; font-weight: 800; color: #8ca3c4; letter-spacing: 2.4px; }
    .sbc-hero-title { display: inline-flex; align-items: center; gap: 7px; font-size: 13px; font-weight: 700; color: #cbd5e1; text-transform: uppercase; letter-spacing: 1px; }
    .sbc-hero-num { font-size: 74px; font-weight: 900; line-height: 1; margin: 6px 0 0; letter-spacing: -3.5px; }
    .sbc-hero-num span { font-size: 30px; color: #8ca3c4; font-weight: 700; letter-spacing: -1px; }
    .sbc-hero-track { background: rgba(255,255,255,.12); border-radius: 999px; height: 10px; overflow: hidden; margin-top: 20px; }
    .sbc-hero-foot { font-size: 13.5px; color: #b6c4d8; margin-top: 11px; font-weight: 500; }
    .sbc-pill-solid {
        font-size: 10.5px; font-weight: 800; letter-spacing: .6px; padding: 5px 12px; border-radius: 999px;
        color: #fff; background: var(--c); box-shadow: 0 6px 18px -6px var(--c);
    }

    /* ---------- Heute-Banner ---------- */
    .sbc-today { display: flex; align-items: center; gap: 26px; flex-wrap: wrap; padding: 24px 30px; }
    .sbc-ring {
        width: 90px; height: 90px; border-radius: 50%; flex: 0 0 auto; display: flex; align-items: center; justify-content: center;
        background: conic-gradient(var(--c) var(--p), rgba(255,255,255,.10) 0);
    }
    .sbc-ring-in {
        width: 68px; height: 68px; border-radius: 50%; background: #0e1727;
        display: flex; flex-direction: column; align-items: center; justify-content: center;
    }
    .sbc-ring-num { font-size: 23px; font-weight: 900; line-height: 1; color: #fff; }
    .sbc-ring-num span { font-size: 13px; color: #7d90ad; font-weight: 700; }
    .sbc-ring-cap { font-size: 8px; font-weight: 800; color: #8ca3c4; letter-spacing: 1.4px; margin-top: 3px; }
    .sbc-today-head { font-size: 11.5px; font-weight: 800; letter-spacing: 2.2px; color: var(--c); }
    .sbc-today-h { font-size: 25px; font-weight: 900; margin-top: 5px; line-height: 1.2; letter-spacing: -.6px; }
    .sbc-today-sub { font-size: 13px; color: #93a4bd; margin-top: 4px; }

    /* ---------- Wochenraster ---------- */
    .sbc-grid { display: grid; grid-template-columns: 190px repeat(5, 1fr) 1.45fr; gap: 7px; }
    .sbc-hcell { text-align: center; padding: 9px 2px; font-size: 12px; font-weight: 800; color: var(--muted); border-radius: 10px; }
    .sbc-hcell.today { background: var(--ink); color: #fff; }
    .sbc-hcell small { display: block; font-size: 10px; font-weight: 600; opacity: .65; }
    .sbc-mlabel { display: flex; align-items: center; gap: 8px; font-size: 13px; font-weight: 650; color: var(--body); padding: 4px 6px 4px 0; }
    .sbc-ico-sm { flex: 0 0 auto; font-size: 14px; line-height: 1; }
    .sbc-ico-sm img { width: 15px !important; height: 15px !important; }
    .sbc-cell {
        text-align: center; padding: 11px 2px; border-radius: 11px; font-size: 15px; font-weight: 800;
        border: 1px solid transparent; transition: transform .12s ease;
    }
    .sbc-cell:hover { transform: translateY(-1px); }
    .sbc-cell.today { box-shadow: 0 0 0 2px var(--ink); }
    .sbc-cell.ok   { background: #e7f8ef; color: #0d7a48; border-color: #c9eeda; }
    .sbc-cell.mid  { background: #fef4e0; color: #92600b; border-color: #fae4bb; }
    .sbc-cell.low  { background: #feecef; color: #a8203c; border-color: #fbd3da; }
    .sbc-cell.void { background: var(--surface-2); color: #cbd5e1; font-weight: 600; }
    .sbc-cell.pause{ background: #e8edf5; }
    .sbc-sum { text-align: center; padding: 11px 4px; border-radius: 11px; background: var(--ink); color: #fff; }
    .sbc-sum-num { font-size: 14px; font-weight: 800; }
    .sbc-sum-num span { font-size: 11px; color: #8ca3c4; font-weight: 600; }
    .sbc-sum-track { height: 4px; background: rgba(255,255,255,.16); border-radius: 999px; margin: 6px 6px 0; overflow: hidden; }
    .sbc-legend { font-size: 11.5px; color: var(--faint); margin-top: 14px; }

    /* ---------- Team-Karte (Startseite) ---------- */
    .sbc-person { border-top: 4px solid var(--c); padding-top: 22px; }
    .sbc-person-name { font-size: 23px; font-weight: 850; color: var(--ink); letter-spacing: -.6px; }
    .sbc-chips { display: grid; grid-template-columns: repeat(6, 1fr); gap: 7px; }
    .sbc-chip { border-radius: 10px; padding: 8px 4px; text-align: center; font-size: 11px; font-weight: 800; line-height: 1.5; }
    .sbc-chip.ok  { background: #e7f8ef; color: #0d7a48; }
    .sbc-chip.off { background: var(--surface-2); color: var(--muted); border: 1px solid var(--line); }
    .sbc-todo {
        background: var(--surface-2); border: 1px solid var(--line); padding: 15px 17px;
        border-radius: 14px; margin-bottom: 18px;
    }
    .sbc-todo-head { display: inline-flex; align-items: center; gap: 7px; font-size: 10.5px; font-weight: 800; color: var(--muted); letter-spacing: 1.3px; }
    .sbc-mini {
        background: linear-gradient(135deg, #0b1220, #1b2942); padding: 18px; border-radius: 14px; color: #fff; margin-bottom: 18px;
    }
    .sbc-mini-cap { display: inline-flex; align-items: center; gap: 7px; font-size: 10.5px; color: #8ca3c4; letter-spacing: 1.2px; font-weight: 700; }
    .sbc-mini-num { font-size: 42px; font-weight: 900; line-height: 1; letter-spacing: -2px; }
    .sbc-mini-num span { font-size: 18px; color: #8ca3c4; font-weight: 600; }
    .sbc-line {
        display: flex; justify-content: space-between; align-items: center; gap: 10px;
        padding: 9px 0; border-bottom: 1px solid #f2f5fa;
    }
    .sbc-line:last-child { border-bottom: 0; }
    .sbc-line-l { font-size: 13px; color: var(--body); flex: 1; display: flex; align-items: center; gap: 8px; }
    .sbc-line-v { font-size: 14px; font-weight: 750; color: var(--ink); font-variant-numeric: tabular-nums; }
    .sbc-line-t { width: 84px; background: #eef2f7; border-radius: 999px; height: 6px; overflow: hidden; }

    @media (max-width: 900px) {
        .sbc-grid { grid-template-columns: 130px repeat(5, 1fr) 1.4fr; gap: 5px; }
        .sbc-mlabel { font-size: 11.5px; }
        .sbc-hero-num { font-size: 58px; }
    }
</style>
"""

def render_person_page(person, today=None):
    """Volle Person-Seite mit Hero + KPIs + Heatmap."""
    if today is None:
        today = now_local()
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

    # HEUTE = Hauptansicht. In der aktuellen Woche = heute (am Wochenende
    # Rueckblick auf Freitag), in einer Vergangenheits-Woche = deren Freitag.
    now = now_local()
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
    is_today = is_current_week and now.date() == todo_day.date()
    st.markdown(render_today_banner(person, day_agg, todo_day, is_today=is_today), unsafe_allow_html=True)

    # Grosse Tageszielkarten: das, was als erstes ins Auge springen soll
    daily = TARGETS["daily"]
    suffix = " heute" if is_today else ""
    col1, col2 = st.columns(2)
    with col1:
        st.markdown(big_kpi_card(f"Cold Calls{suffix}", (day_agg or {}).get("outbound", 0), daily["outbound"], "📞"), unsafe_allow_html=True)
        st.markdown(big_kpi_card(f"Kandidaten ins CRM{suffix}", (day_agg or {}).get("neue_kandidaten", 0), daily["neue_kandidaten"], "👥"), unsafe_allow_html=True)
        st.markdown(big_kpi_card(f"Sendouts{suffix}", (day_agg or {}).get("sendouts", 0), daily["sendouts"], "📤"), unsafe_allow_html=True)
    with col2:
        st.markdown(big_kpi_card(f"Wirk-Calls (≥2 Min){suffix}", (day_agg or {}).get("wirk_calls", 0), daily["wirk_calls"], "☎️"), unsafe_allow_html=True)
        st.markdown(big_kpi_card(f"Kandidaten auf Jobs{suffix}", (day_agg or {}).get("assignments", 0), daily["assignments"], "🔗"), unsafe_allow_html=True)
        st.markdown(big_kpi_card(f"Neue Jobs angelegt{suffix}", (day_agg or {}).get("neue_jobs", 0), daily["neue_jobs"], "📋"), unsafe_allow_html=True)

    # Wochenansicht: jeder Tag der Woche gegen das Tagesziel, rechts Wochensumme vs. Wochenziel
    week_label = "Diese Woche" if is_current_week else f"KW{week_start.isocalendar().week}"
    st.markdown(f"## 📅  {week_label} · Tag für Tag und Wochenziel")
    week_days = get_week_days(person, week_start, today)
    st.markdown(render_week_view(person, week_days, week_start, today, week), unsafe_allow_html=True)

    # Monats-Hero
    st.markdown(deals_hero(person, month["deals"], TARGETS["monthly"]["deals"], days_left), unsafe_allow_html=True)

    # Gespraechsqualitaet
    st.markdown("## Gesprächsqualität")
    qcol1, qcol2, qcol3 = st.columns(3)
    qcol1.metric("Connect-Rate", f"{week['connect_rate']:.0f} %", help="Wieviel Prozent deiner Outbound-Calls angenommen werden. Ziel: 65 % +")
    qcol2.metric("Ø Gesprächsdauer", f"{week['avg_dur_min']:.1f} Min", help="Durchschnittliche Dauer. Unter 1 Min = überwiegend Mailbox.")
    qcol3.metric("Interviews scheduled", f"{week['interviews']} / {TARGETS['weekly']['interviews']}")

def render_sidebar():
    """Sidebar mit Wochen-Selector. Gibt den gewaehlten 'today' (datetime) zurueck."""
    today = now_local()
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
        if st.button("🔄 Daten jetzt live ziehen", use_container_width=True):
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
