# app.py
import streamlit as st
from spoi.db.session import SessionLocal
from spoi.ui.calendar import timetable_calendar_ui
from spoi.ui.event_chat import event_chat_ui
from spoi.ui.rag_admin_ui import rag_admin_ui

st.set_page_config(page_title="Timetable Calendar", layout="wide")

def main():
    with SessionLocal() as session:
        # 1. Main calendar UI (all the selection logic, shows events)
        calendar_state = timetable_calendar_ui(session)

        # 2. Event Chat UI (only shown if user clicks an event)
        if calendar_state.get("event_click"):
            event_chat_ui(session, calendar_state["event_click"])

        # 3. RAG admin UI (RAG example curation, always available)
        rag_admin_ui(session)

if __name__ == "__main__":
    main()
