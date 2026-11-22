"""
style.py
Shared CSS injection, kept as its own module so App.py stays focused on
logic — same split used in the WS Marker Tool.
"""

import streamlit as st

CUSTOM_CSS = """
<style>
    .stApp {
        font-family: -apple-system, "Segoe UI", sans-serif;
    }
    div[data-testid="stDataFrame"] {
        border: 1px solid #2a2e37;
        border-radius: 6px;
    }
    .stButton > button[kind="primary"] {
        background-color: #e63946;
        border: none;
    }
    .stButton > button[kind="primary"]:hover {
        background-color: #c92e3a;
    }
    footer {visibility: hidden;}
</style>
"""


def inject():
    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)
