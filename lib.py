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
}

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

# ============== DATA PULL (cached, geteilt zwischen Pages) ==============

@st.cache_data(ttl=900, show_spinner="Daten werden geladen (Aircall)...")
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

@st.cache_data(ttl=900, show_spinner="Daten werden geladen (Kandidaten)...")
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
        time.sleep(0.5)
    return out

@st.cache_data(ttl=900, show_spinner="Daten werden geladen (Companies)...")
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
        time.sleep(0.5)
    return out

@st.cache_data(ttl=900, show_spinner="Daten werden geladen (Jobs)...")
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
        time.sleep(0.5)
    return out

@st.cache_data(ttl=900, show_spinner="Daten werden geladen (Assignments)...")
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
    relevant_slugs = [j.get("slug") for j in jobs if j.get("slug") and (
        (parse_dt(j.get("created_on")) and parse_dt(j.get("created_on")) >= cutoff)
        or (j.get("job_status") or {}).get("label") == "Open"
    )][:90]
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

def render_day_heatmap(per_day_kandidaten, per_day_wirk, week_start, today):
    days = [week_start + dt.timedelta(days=i) for i in range(5)]
    cells = []
    for d in days:
        key = d.strftime("%Y-%m-%d")
        kand = per_day_kandidaten.get(key, 0)
        wirk = per_day_wirk.get(key, 0)
        is_future = d.date() > today.date()
        if is_future:
            bg, txt_color = "#f1f5f9", "#cbd5e1"
            content = f"<div style='font-size: 28px; color: {txt_color};'>·</div>"
        elif kand == 0 and wirk == 0:
            bg, txt_color = "#fee2e2", "#991b1b"
            content = f"<div style='font-size: 24px;'>🔴</div><div style='font-size: 11px; margin-top: 4px;'>Null-Tag</div>"
        elif wirk >= 5:
            bg, txt_color = "#dcfce7", "#166534"
            content = f"<div style='font-size: 28px; font-weight: 900;'>{wirk}</div><div style='font-size: 11px;'>Wirk-Calls</div><div style='font-size: 12px; margin-top: 4px;'>{kand} ins CRM</div>"
        elif wirk >= 2:
            bg, txt_color = "#fef3c7", "#854d0e"
            content = f"<div style='font-size: 28px; font-weight: 900;'>{wirk}</div><div style='font-size: 11px;'>Wirk-Calls</div><div style='font-size: 12px; margin-top: 4px;'>{kand} ins CRM</div>"
        else:
            bg, txt_color = "#fee2e2", "#991b1b"
            content = f"<div style='font-size: 28px; font-weight: 900;'>{wirk}</div><div style='font-size: 11px;'>Wirk-Calls</div><div style='font-size: 12px; margin-top: 4px;'>{kand} ins CRM</div>"
        cells.append(clean_html(f"""<div style="background: {bg}; padding: 14px; border-radius: 10px; text-align: center; color: {txt_color};">
            <div style="font-size: 11px; font-weight: 700; color: #475569; text-transform: uppercase; margin-bottom: 4px;">{d.strftime('%a %d.%m')}</div>
            {content}
        </div>"""))
    return "<div style='display: grid; grid-template-columns: repeat(5, 1fr); gap: 10px;'>" + "".join(cells) + "</div>"

def page_css():
    return """
<style>
    .main { background: #f8fafc; }
    .block-container { padding-top: 1.5rem; padding-bottom: 2rem; max-width: 1200px; }
    [data-testid="stHeader"] { background: transparent; }
    [data-testid="stSidebar"] { background: #ffffff; }
    h1 { font-size: 28px; font-weight: 800; color: #0f172a; margin-bottom: 0; }
    h2 { font-size: 18px; font-weight: 700; color: #334155; margin-top: 1.5rem; margin-bottom: 0.75rem; text-transform: uppercase; letter-spacing: 0.5px; }
</style>
"""

def render_person_page(person, today=None):
    """Volle Person-Seite mit Hero + KPIs + Heatmap."""
    if today is None:
        today = dt.datetime.now()
    week_start, week_end, month_start, month_end = get_windows(today)
    days_left = max(0, (month_end.date() - today.date()).days)

    data = load_all_data(today.replace(microsecond=0).isoformat())
    week = aggregate_person(person, data, week_start, week_end)
    month = aggregate_person(person, data, month_start, month_end)

    st.markdown(f"# {person} · Wo stehst du?")
    st.caption(f"KW{week_start.isocalendar().week} · {week_start.strftime('%d.%m.')} bis {week_end.strftime('%d.%m.%Y')} · Stand {today.strftime('%H:%M')}")

    # Hero
    st.markdown(deals_hero(person, month["deals"], TARGETS["monthly"]["deals"], days_left), unsafe_allow_html=True)

    # Wochen-KPIs
    st.markdown("## Diese Woche")
    col1, col2 = st.columns(2)
    with col1:
        st.markdown(big_kpi_card("Cold Calls", week["outbound"], TARGETS["weekly"]["outbound"], "📞"), unsafe_allow_html=True)
        st.markdown(big_kpi_card("Kandidaten ins CRM", week["neue_kandidaten"], TARGETS["weekly"]["neue_kandidaten"], "👥"), unsafe_allow_html=True)
        st.markdown(big_kpi_card("Sendouts", week["sendouts"], TARGETS["weekly"]["sendouts"], "📤"), unsafe_allow_html=True)
    with col2:
        st.markdown(big_kpi_card("Wirk-Calls (≥2 Min)", week["wirk_calls"], TARGETS["weekly"]["wirk_calls"], "☎️"), unsafe_allow_html=True)
        st.markdown(big_kpi_card("Kandidaten auf Jobs", week["assignments"], TARGETS["weekly"]["assignments"], "🔗"), unsafe_allow_html=True)
        st.markdown(big_kpi_card("Neue Jobs angelegt", week["neue_jobs"], TARGETS["weekly"]["neue_jobs"], "📋"), unsafe_allow_html=True)

    # Tagesverteilung
    st.markdown("## Deine Woche Tag für Tag")
    st.markdown(render_day_heatmap(week["per_day_kandidaten"], week["per_day_wirk"], week_start, today), unsafe_allow_html=True)
    st.caption("Pro Tag: Wirk-Calls und Kandidaten ins CRM. Null-Tage sind rot, weil kein Tag auf 0 stehen darf.")

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
        if st.button("🔄 Daten jetzt neu laden", use_container_width=True):
            st.cache_data.clear()
            st.rerun()
        st.caption("Auto-Refresh alle 15 Min")
        st.markdown("---")
        st.caption("**Daten-Quellen:** Aircall · Recruit CRM")
        st.caption("**Kandidaten-Calls per Handy** sind im Tracking nicht erfasst.")
    return today_effective
