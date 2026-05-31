"""Eigene Seite fuer Robin."""
import streamlit as st
from lib import page_css, render_sidebar, render_person_page

st.set_page_config(page_title="Robin - Synergy Cockpit", page_icon="👤", layout="wide")
st.markdown(page_css(), unsafe_allow_html=True)
render_sidebar()
render_person_page("Robin")
