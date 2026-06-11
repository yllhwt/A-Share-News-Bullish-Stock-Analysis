import streamlit as st
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

st.title("逐步排查")

steps = []

# Step 1
try:
    from app_config import DEEPSEEK_MODEL
    steps.append(("app_config", True, str(DEEPSEEK_MODEL)))
except Exception as e:
    steps.append(("app_config", False, str(e)))

# Step 2
try:
    from quota import get_quota
    steps.append(("quota", True, "OK"))
except Exception as e:
    steps.append(("quota", False, str(e)))

# Step 3
try:
    from scraper import fetch_today_news
    steps.append(("scraper", True, "OK"))
except Exception as e:
    steps.append(("scraper", False, str(e)))

# Step 4
try:
    from analyzer import analyze_news
    steps.append(("analyzer", True, "OK"))
except Exception as e:
    steps.append(("analyzer", False, str(e)))

for name, ok, msg in steps:
    if ok:
        st.success(f"**{name}**: {msg}")
    else:
        st.error(f"**{name}**: {msg}")
