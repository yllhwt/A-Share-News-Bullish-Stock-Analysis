import streamlit as st

st.title("逐步加载测试")

tests = []

# 1
try:
    from app_config import DEEPSEEK_MODEL
    tests.append(("app_config", True))
except Exception as e:
    tests.append(("app_config", False, str(e)))

# 2
try:
    from quota import get_quota
    tests.append(("quota", True))
except Exception as e:
    tests.append(("quota", False, str(e)))

# 3
try:
    from scraper import fetch_today_news
    tests.append(("scraper", True))
except Exception as e:
    tests.append(("scraper", False, str(e)))

# 4
try:
    from analyzer import analyze_news
    tests.append(("analyzer", True))
except Exception as e:
    tests.append(("analyzer", False, str(e)))

for t in tests:
    if len(t) == 2:
        st.success(t[0])
    else:
        st.error(f"{t[0]}: {t[2][:200]}")
