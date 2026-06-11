import streamlit as st
from app_config import DEEPSEEK_MODEL, DEEPSEEK_BASE_URL
st.success(f"OK: {DEEPSEEK_MODEL[:10]}... {DEEPSEEK_BASE_URL}")
