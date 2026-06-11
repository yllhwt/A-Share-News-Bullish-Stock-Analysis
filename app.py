import streamlit as st
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app_config import DEEPSEEK_MODEL
st.success(f"DEEPSEEK_MODEL={DEEPSEEK_MODEL}")
