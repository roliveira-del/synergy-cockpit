"""Synergy Cockpit - Team-Uebersicht (Home).

Pro-Person-Seiten liegen unter pages/.
"""
import streamlit as st
import datetime as dt
from lib import (
    USERS, TARGETS, page_css, render_sidebar,
    load_all_data, aggregate_person, get_windows,
    status_color, status_label,
)

st.set_page_config(page_title="Synergy Cockpit", page_icon="🎯", layout="wide")
st.markdown(page_css(), unsafe_allow_html=True)
render_sidebar()

today = dt.datetime.now()
week_start, week_end, month_start, month_end = get_windows(today)
days_left = max(0, (month_end.date() - today.date()).days)

st.markdown(f"# Team-Uebersicht · KW{week_start.isocalendar().week}")
st.caption(f"Woche {week_start.strftime('%d.%m.')} bis {week_end.strftime('%d.%m.%Y')} · Stand {today.strftime('%a %d.%m.%Y %H:%M')}")

st.info("⬅️ Wähle in der Seitenleiste links **Kevin** oder **Robin**, um die persönliche Seite zu sehen.")

data = load_all_data(today.replace(microsecond=0).isoformat())

# Zwei Spalten nebeneinander
col_kev, col_rob = st.columns(2)

def render_compact_card(col, person):
    week = aggregate_person(person, data, week_start, week_end)
    month = aggregate_person(person, data, month_start, month_end)

    deals = month["deals"]
    deal_pct = (deals / TARGETS["monthly"]["deals"]) * 100
    deal_color = status_color(deal_pct / 100)
    deal_status = status_label(deal_pct / 100)

    rows = [
        ("📞 Cold Calls", week["outbound"], TARGETS["weekly"]["outbound"]),
        ("☎️ Wirk-Calls", week["wirk_calls"], TARGETS["weekly"]["wirk_calls"]),
        ("👥 Kandidaten ins CRM", week["neue_kandidaten"], TARGETS["weekly"]["neue_kandidaten"]),
        ("🔗 Kandidaten auf Jobs", week["assignments"], TARGETS["weekly"]["assignments"]),
        ("📤 Sendouts", week["sendouts"], TARGETS["weekly"]["sendouts"]),
        ("📋 Neue Jobs", week["neue_jobs"], TARGETS["weekly"]["neue_jobs"]),
    ]

    rows_html = ""
    for label, val, target in rows:
        pct = (val / target * 100) if target else 0
        color = status_color(pct / 100)
        rows_html += f"""
        <div style="display: flex; justify-content: space-between; align-items: center; padding: 8px 0; border-bottom: 1px solid #f1f5f9;">
            <div style="font-size: 13px; color: #475569; flex: 1;">{label}</div>
            <div style="font-size: 14px; font-weight: 700; color: #0f172a;">{val} / {target}</div>
            <div style="width: 80px; background: #f1f5f9; border-radius: 4px; height: 6px; margin-left: 10px; overflow: hidden;">
                <div style="background: {color}; width: {min(pct, 100)}%; height: 100%;"></div>
            </div>
        </div>
        """

    html = f"""
    <div style="background: white; padding: 24px; border-radius: 14px; box-shadow: 0 1px 4px rgba(0,0,0,0.06); margin-bottom: 16px;">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;">
            <div style="font-size: 22px; font-weight: 800; color: #0f172a;">{person}</div>
            <div style="font-size: 11px; font-weight: 700; color: white; background: {deal_color}; padding: 4px 12px; border-radius: 12px;">{deal_status}</div>
        </div>
        <div style="background: #0f172a; padding: 16px; border-radius: 10px; color: white; margin-bottom: 16px;">
            <div style="font-size: 11px; color: #94a3b8; letter-spacing: 0.8px; font-weight: 600;">🎯 DEALS MONAT</div>
            <div style="font-size: 40px; font-weight: 900; line-height: 1;">{deals}<span style="font-size: 18px; color: #94a3b8; font-weight: 500;"> / {TARGETS['monthly']['deals']}</span></div>
            <div style="background: rgba(255,255,255,0.1); border-radius: 6px; height: 8px; margin-top: 10px; overflow: hidden;">
                <div style="background: {deal_color}; width: {min(deal_pct, 100)}%; height: 100%;"></div>
            </div>
        </div>
        <div style="font-size: 11px; font-weight: 700; color: #64748b; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px;">DIESE WOCHE</div>
        {rows_html}
    </div>
    """
    col.markdown(html, unsafe_allow_html=True)

render_compact_card(col_kev, "Kevin")
render_compact_card(col_rob, "Robin")

st.markdown("---")
st.caption("Synergy Cockpit · Daten: Aircall + Recruit CRM · Auto-Refresh alle 15 Min")
