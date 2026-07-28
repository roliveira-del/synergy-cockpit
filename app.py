"""Synergy Cockpit - Team-Uebersicht (Home).

Pro-Person-Seiten liegen unter pages/.
"""
import streamlit as st
import datetime as dt
from lib import (
    USERS, TARGETS, METRICS, page_css, render_sidebar,
    load_all_data, aggregate_person, get_windows,
    status_color, status_color_pair, status_label,
    get_period_from_cache, get_day_from_cache,
    is_pause_day, pause_reason,
)

st.set_page_config(page_title="Synergy Cockpit", page_icon="🎯", layout="wide")
st.markdown(page_css(), unsafe_allow_html=True)
today = render_sidebar()

week_start, week_end, month_start, month_end = get_windows(today)
days_left = max(0, (month_end.date() - today.date()).days)

st.markdown(f"# Team-Übersicht · KW{week_start.isocalendar().week}")
st.caption(f"Woche {week_start.strftime('%d.%m.')} bis {week_end.strftime('%d.%m.%Y')} · Stand {today.strftime('%a %d.%m.%Y %H:%M')}")

st.markdown(
    """<div class="sbc-card" style="padding: 14px 18px; margin: 14px 0 22px; box-shadow: none;
    background: linear-gradient(90deg, #eff6ff, #ecfdf5); border-color: #dbe7f7; font-size: 13.5px; color: #334155;">
    ⬅️  In der Seitenleiste <b>Kevin</b> oder <b>Robin</b> wählen für die persönliche Seite.</div>""",
    unsafe_allow_html=True,
)

data = None  # lazy load - nur wenn Cache fehlt

# Zwei Spalten nebeneinander
col_kev, col_rob = st.columns(2)

def render_compact_card(col, person):
    global data
    week = get_period_from_cache(person, week_start, week_end)
    month = get_period_from_cache(person, month_start, month_end)
    if week is None or month is None:
        if data is None:
            data = load_all_data(today.replace(microsecond=0).isoformat())
        if week is None:
            week = aggregate_person(person, data, week_start, week_end)
        if month is None:
            month = aggregate_person(person, data, month_start, month_end)

    # Heute-Status: am Wochenende Rueckblick auf Freitag
    accent = USERS[person]["color"]
    todo_day = today if today.weekday() < 5 else today - dt.timedelta(days=today.weekday() - 4)
    todo_key = todo_day.strftime("%Y-%m-%d")
    day_agg = get_day_from_cache(person, todo_key)
    if day_agg is None:
        if data is None:
            data = load_all_data(today.replace(microsecond=0).isoformat())
        day_agg = aggregate_person(person, data,
                                   todo_day.replace(hour=0, minute=0, second=0, microsecond=0),
                                   todo_day.replace(hour=23, minute=59, second=59, microsecond=0))

    if is_pause_day(person, todo_key):
        today_html = f"""
        <div class="sbc-todo" style="font-size: 13px; color: #64748b; font-weight: 600;">
            ⏸️  Heute Pause: {pause_reason(person, todo_key)}
        </div>
        """
    else:
        done_today = 0
        chips = ""
        for key, icon, label in METRICS:
            target = TARGETS["daily"][key]
            val = (day_agg or {}).get(key, 0)
            ok = val >= target
            if ok:
                done_today += 1
            chips += f"""<div class="sbc-chip {'ok' if ok else 'off'}" title="{label}: {val} / {target}">{icon}<br>{val}/{target}</div>"""
        ring_color = "#10b981" if done_today == len(METRICS) else accent
        today_label = "HEUTE" if today.weekday() < 5 else f"FREITAG ({todo_day.strftime('%d.%m.')})"
        today_html = f"""
        <div class="sbc-todo">
            <div class="sbc-row" style="margin-bottom: 11px;">
                <div class="sbc-todo-head"><span class="sbc-ico-sm">✔️</span>{today_label} · TAGES-TO-DOS</div>
                <div style="font-size: 12px; font-weight: 800; color: {ring_color};">{done_today}/{len(METRICS)} Ziele</div>
            </div>
            <div class="sbc-chips">{chips}</div>
        </div>
        """

    deals = month["deals"]
    deal_pct = (deals / TARGETS["monthly"]["deals"]) * 100
    deal_color, deal_color_2 = status_color_pair(deal_pct / 100)
    deal_status = status_label(deal_pct / 100)

    rows = [
        ("📞", "Cold Calls", week["outbound"], TARGETS["weekly"]["outbound"]),
        ("☎️", "Wirk-Calls", week["wirk_calls"], TARGETS["weekly"]["wirk_calls"]),
        ("👥", "Kandidaten ins CRM", week["neue_kandidaten"], TARGETS["weekly"]["neue_kandidaten"]),
        ("🔗", "Kandidaten auf Jobs", week["assignments"], TARGETS["weekly"]["assignments"]),
        ("📤", "Sendouts", week["sendouts"], TARGETS["weekly"]["sendouts"]),
        ("📋", "Neue Jobs", week["neue_jobs"], TARGETS["weekly"]["neue_jobs"]),
    ]

    rows_html = ""
    for icon, label, val, target in rows:
        pct = (val / target * 100) if target else 0
        color, color_2 = status_color_pair(pct / 100)
        rows_html += f"""
        <div class="sbc-line" style="--c: {color}; --c2: {color_2};">
            <div class="sbc-line-l"><span class="sbc-ico-sm">{icon}</span>{label}</div>
            <div class="sbc-line-v">{val} / {target}</div>
            <div class="sbc-line-t"><div class="sbc-fill" style="width: {min(pct, 100)}%;"></div></div>
        </div>
        """

    html = f"""
    <div class="sbc-card sbc-person" style="--c: {accent}; --c2: {deal_color_2};">
        <div class="sbc-row" style="margin-bottom: 16px;">
            <div class="sbc-person-name">{person}</div>
            <div class="sbc-pill-solid" style="--c: {deal_color};">{deal_status}</div>
        </div>
        {today_html}
        <div class="sbc-mini" style="--c: {deal_color}; --c2: {deal_color_2};">
            <div class="sbc-mini-cap"><span class="sbc-ico-sm">🎯</span>DEALS MONAT</div>
            <div class="sbc-mini-num">{deals}<span> / {TARGETS['monthly']['deals']}</span></div>
            <div class="sbc-hero-track" style="height: 8px; margin-top: 11px;"><div class="sbc-fill" style="width: {min(deal_pct, 100)}%;"></div></div>
        </div>
        <div class="sbc-todo-head" style="margin-bottom: 6px;">DIESE WOCHE</div>
        {rows_html}
    </div>
    """
    # HTML-Whitespace strippen, sonst interpretiert Markdown >=4 Spaces als Code-Block
    html_clean = " ".join(line.strip() for line in html.split("\n") if line.strip())
    col.markdown(html_clean, unsafe_allow_html=True)

render_compact_card(col_kev, "Kevin")
render_compact_card(col_rob, "Robin")

st.markdown("---")
st.caption("Synergy Cockpit · Daten: Aircall + Recruit CRM · Auto-Refresh alle 15 Min")
