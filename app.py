"""
Synergy Cockpit
Zielerreichung Kevin & Robin auf einer Seite.
"""
import os, json, base64, time, urllib.request, urllib.parse
import datetime as dt
from collections import defaultdict, Counter
from pathlib import Path
import streamlit as st

# ============== CONFIG ==============

USERS = {
    "Kevin": {"aircall_id": 1301014, "crm_id": 99698, "color": "#3b82f6"},
    "Robin": {"aircall_id": 1561108, "crm_id": 100690, "color": "#10b981"},
}

TARGETS = {
    "weekly": {
        "outbound": 175,
        "wirk_calls": 25,
        "neue_kandidaten": 18,
        "assignments": 8,
        "sendouts": 6,
        "interviews": 4,
        "neue_jobs": 4,
    },
    "monthly": {
        "deals": 2,
        "interviews": 16,
        "sendouts": 24,
        "neue_jobs": 16,
        "neue_kunden": 4,
    },
}

ASSIGNMENT_STATUS = {1, 613655, 613656, 613657, 613658, 613659, 613660, 613661, 613662, 8}
INTERVIEW_STATUS = {613655, 613656, 613657}
SENDOUT_STATUS = ASSIGNMENT_STATUS
DEAL_STATUS = {8}

# ============== CREDENTIALS ==============
# Streamlit Cloud: via st.secrets. Lokal: via Env-Datei.

def get_secret(key):
    try:
        return st.secrets[key]
    except (KeyError, FileNotFoundError, Exception):
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
    except: return None

# ============== DATA PULL (cached 15 min) ==============

@st.cache_data(ttl=900, show_spinner="Pull Aircall Calls...")
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
        time.sleep(0.6)
    return calls

@st.cache_data(ttl=900, show_spinner="Pull Recruit CRM Candidates...")
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
        time.sleep(0.6)
    return out

@st.cache_data(ttl=900, show_spinner="Pull Recruit CRM Companies...")
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
        time.sleep(0.6)
    return out

@st.cache_data(ttl=900, show_spinner="Pull Recruit CRM Jobs...")
def fetch_jobs(max_pages=10):
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
        time.sleep(0.6)
    return out

@st.cache_data(ttl=900, show_spinner="Pull Job Assignments...")
def fetch_assignments(jobs):
    out = []
    for j in jobs:
        slug = j.get("slug")
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
            time.sleep(0.5)
        time.sleep(0.5)
    return out

# ============== AGGREGATION ==============

def in_range(d, start, end):
    return d is not None and start <= d <= end

def aggregate_person(person, calls, candidates, companies, jobs, assignments, period_start, period_end):
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
    for c in calls:
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
    for cand in candidates:
        if cand.get("owner") != crm_id: continue
        cdt = parse_dt(cand.get("created_on"))
        if in_range(cdt, period_start, period_end):
            u["neue_kandidaten"] += 1
            u["per_day_kandidaten"][cdt.strftime("%Y-%m-%d")] += 1
    for comp in companies:
        if comp.get("owner") != crm_id: continue
        cdt = parse_dt(comp.get("created_on"))
        if in_range(cdt, period_start, period_end):
            u["neue_kunden"] += 1
    for job in jobs:
        if job.get("owner") != crm_id: continue
        jdt = parse_dt(job.get("created_on"))
        if in_range(jdt, period_start, period_end):
            u["neue_jobs"] += 1
    for asg in assignments:
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

# ============== UI HELPERS ==============

def status_color(pct):
    if pct >= 1.0: return "#22c55e"
    if pct >= 0.5: return "#f59e0b"
    return "#ef4444"

def status_label(pct):
    if pct >= 1.0: return "ON TRACK"
    if pct >= 0.5: return "ACHTUNG"
    return "ESKALATION"

def big_kpi_card(label, value, target, icon, person_color):
    pct = (value / target) if target else 0
    pct_w = min(pct * 100, 100)
    color = status_color(pct)
    label_status = status_label(pct)
    html = f"""
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
    """
    return html

def deals_hero(value, target, days_left):
    pct = (value / target) if target else 0
    pct_w = min(pct * 100, 100)
    color = status_color(pct)
    label_status = status_label(pct)
    html = f"""
    <div style="background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%); padding: 30px 28px; border-radius: 16px; color: white; box-shadow: 0 4px 16px rgba(0,0,0,0.15); margin-bottom: 20px;">
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
    """
    return html

def render_day_heatmap(per_day_kandidaten, per_day_wirk, week_start, today):
    days = []
    d = week_start
    while d <= week_start + dt.timedelta(days=4):
        days.append(d)
        d += dt.timedelta(days=1)
    cells = []
    for d in days:
        key = d.strftime("%Y-%m-%d")
        kand = per_day_kandidaten.get(key, 0)
        wirk = per_day_wirk.get(key, 0)
        is_future = d.date() > today.date()
        if is_future:
            bg = "#f1f5f9"; emoji = ""; txt_color = "#cbd5e1"
            content = "·"
        elif kand == 0 and wirk == 0:
            bg = "#fee2e2"; emoji = "🔴"; txt_color = "#991b1b"
            content = f"<div style='font-size: 24px;'>{emoji}</div><div style='font-size: 11px; margin-top: 4px;'>Null-Tag</div>"
        elif wirk >= 5:
            bg = "#dcfce7"; emoji = "🟢"; txt_color = "#166534"
            content = f"<div style='font-size: 28px; font-weight: 900;'>{wirk}</div><div style='font-size: 11px;'>Wirk-Calls</div><div style='font-size: 12px; margin-top: 4px; color: #14532d;'>{kand} ins CRM</div>"
        elif wirk >= 2:
            bg = "#fef3c7"; emoji = "🟡"; txt_color = "#854d0e"
            content = f"<div style='font-size: 28px; font-weight: 900;'>{wirk}</div><div style='font-size: 11px;'>Wirk-Calls</div><div style='font-size: 12px; margin-top: 4px; color: #713f12;'>{kand} ins CRM</div>"
        else:
            bg = "#fee2e2"; emoji = "🔴"; txt_color = "#991b1b"
            content = f"<div style='font-size: 28px; font-weight: 900;'>{wirk}</div><div style='font-size: 11px;'>Wirk-Calls</div><div style='font-size: 12px; margin-top: 4px; color: #7f1d1d;'>{kand} ins CRM</div>"
        cells.append(f"""<div style="background: {bg}; padding: 14px; border-radius: 10px; text-align: center; color: {txt_color};">
            <div style="font-size: 11px; font-weight: 700; color: #475569; text-transform: uppercase; margin-bottom: 4px;">{d.strftime('%a %d.%m')}</div>
            {content}
        </div>""")
    return "<div style='display: grid; grid-template-columns: repeat(5, 1fr); gap: 10px;'>" + "".join(cells) + "</div>"

# ============== APP LAYOUT ==============

st.set_page_config(page_title="Synergy Cockpit", page_icon="🎯", layout="wide")

# Global CSS
st.markdown("""
<style>
    .main { background: #f8fafc; }
    .block-container { padding-top: 1.5rem; padding-bottom: 2rem; max-width: 1200px; }
    [data-testid="stHeader"] { background: transparent; }
    .stTabs [data-baseweb="tab-list"] { gap: 4px; background: white; padding: 6px; border-radius: 12px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }
    .stTabs [data-baseweb="tab"] { padding: 12px 24px; font-weight: 600; border-radius: 8px; font-size: 16px; }
    .stTabs [aria-selected="true"] { background: #0f172a !important; color: white !important; }
    h1 { font-size: 28px; font-weight: 800; color: #0f172a; margin-bottom: 0; }
    h2 { font-size: 18px; font-weight: 700; color: #334155; margin-top: 1.5rem; margin-bottom: 0.75rem; text-transform: uppercase; letter-spacing: 0.5px; }
</style>
""", unsafe_allow_html=True)

# Sidebar
with st.sidebar:
    st.markdown("### Synergy Cockpit")
    st.caption("Zielerreichung Kevin & Robin")
    st.markdown("---")
    as_of_input = st.date_input("Stichtag", value=dt.date.today())
    today = dt.datetime.combine(as_of_input, dt.time(23, 59, 59))
    st.caption(f"Letzter Daten-Pull: vor max. 15 Min (auto-refresh)")
    if st.button("🔄 Daten jetzt neu laden", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    st.markdown("---")
    st.caption("**Daten-Quellen:** Aircall · Recruit CRM")
    st.caption("**Kandidaten-Calls per Handy** sind im Tracking nicht erfasst.")

# Period
week_start = (today - dt.timedelta(days=today.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
week_end = week_start + dt.timedelta(days=6, hours=23, minutes=59, seconds=59)
month_start = today.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
next_month = month_start.replace(day=28) + dt.timedelta(days=4)
month_end = (next_month - dt.timedelta(days=next_month.day)).replace(hour=23, minute=59, second=59)
days_left = max(0, (month_end.date() - today.date()).days)

# Header
st.markdown(f"# Wo stehen wir? · KW{week_start.isocalendar().week}")
st.caption(f"Woche {week_start.strftime('%d.%m.')} bis {week_end.strftime('%d.%m.%Y')} · Stand {today.strftime('%a %d.%m.%Y %H:%M')}")

# Data Pull
from_ts = int(month_start.timestamp())
to_ts = int(month_end.timestamp())
calls = fetch_aircall(from_ts, to_ts)
candidates = fetch_candidates()
companies = fetch_companies()
jobs = fetch_jobs()
cutoff = (today - dt.timedelta(days=90))
relevant_jobs = [j for j in jobs if parse_dt(j.get("created_on")) and parse_dt(j.get("created_on")) >= cutoff or (j.get("job_status") or {}).get("label") == "Open"]
assignments = fetch_assignments(relevant_jobs[:90])  # safety cap

# Tabs
tab_kev, tab_rob = st.tabs(["KEVIN", "ROBIN"])

def render_person(person):
    week = aggregate_person(person, calls, candidates, companies, jobs, assignments, week_start, week_end)
    month = aggregate_person(person, calls, candidates, companies, jobs, assignments, month_start, month_end)

    # Hero
    st.markdown(deals_hero(month["deals"], TARGETS["monthly"]["deals"], days_left), unsafe_allow_html=True)

    # Wochen-KPIs
    st.markdown("## Diese Woche")
    col1, col2 = st.columns(2)
    with col1:
        st.markdown(big_kpi_card("Cold Calls", week["outbound"], TARGETS["weekly"]["outbound"], "📞", USERS[person]["color"]), unsafe_allow_html=True)
        st.markdown(big_kpi_card("Kandidaten ins CRM", week["neue_kandidaten"], TARGETS["weekly"]["neue_kandidaten"], "👥", USERS[person]["color"]), unsafe_allow_html=True)
        st.markdown(big_kpi_card("Sendouts", week["sendouts"], TARGETS["weekly"]["sendouts"], "📤", USERS[person]["color"]), unsafe_allow_html=True)
    with col2:
        st.markdown(big_kpi_card("Wirk-Calls (≥2 Min)", week["wirk_calls"], TARGETS["weekly"]["wirk_calls"], "☎️", USERS[person]["color"]), unsafe_allow_html=True)
        st.markdown(big_kpi_card("Kandidaten auf Jobs", week["assignments"], TARGETS["weekly"]["assignments"], "🔗", USERS[person]["color"]), unsafe_allow_html=True)
        st.markdown(big_kpi_card("Neue Jobs angelegt", week["neue_jobs"], TARGETS["weekly"]["neue_jobs"], "📋", USERS[person]["color"]), unsafe_allow_html=True)

    # Tagesverteilung
    st.markdown("## Deine Woche Tag für Tag")
    st.markdown(render_day_heatmap(week["per_day_kandidaten"], week["per_day_wirk"], week_start, today), unsafe_allow_html=True)
    st.caption("Pro Tag: Wirk-Calls und Kandidaten ins CRM. Null-Tage sind rot, weil kein Tag auf 0 stehen darf.")

    # Gesprächsqualität
    st.markdown("## Gesprächsqualität")
    qcol1, qcol2, qcol3 = st.columns(3)
    qcol1.metric("Connect-Rate", f"{week['connect_rate']:.0f} %", help="Wieviel Prozent deiner Outbound-Calls angenommen werden. Ziel: 65 % +")
    qcol2.metric("Ø Gesprächsdauer", f"{week['avg_dur_min']:.1f} Min", help="Durchschnittliche Dauer. Unter 1 Min = überwiegend Mailbox.")
    qcol3.metric("Interviews scheduled", f"{week['interviews']} / {TARGETS['weekly']['interviews']}")

with tab_kev:
    render_person("Kevin")
with tab_rob:
    render_person("Robin")

# Footer
st.markdown("---")
st.caption("Synergy Cockpit · Daten: Aircall + Recruit CRM · Auto-Refresh alle 15 Min · Build: 2026-05-31")
