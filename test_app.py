import streamlit as st

st.title("test")

try:
    from app_config import DEEPSEEK_MODEL
    st.success(f"app_config OK: {DEEPSEEK_MODEL}")
except Exception as e:
    st.error(f"app_config: {e}")

try:
    from scraper import fetch_today_news
    st.success("scraper OK")
except Exception as e:
    st.error(f"scraper: {e}")

try:
    from analyzer import analyze_news
    st.success("analyzer OK")
except Exception as e:
    st.error(f"analyzer: {e}")

try:
    from quota import get_quota
    st.success("quota OK")
except Exception as e:
    st.error(f"quota: {e}")
