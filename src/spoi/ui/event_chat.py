import streamlit as st
from spoi.db.session import SessionLocal
from spoi.event_chat.manager import EventChatManager
from rag import profile_sqlalchemy_row

def event_chat_ui(session, event_click):
    """
    Main chat UI for an event. To be called if event_click is present.
    Args:
        session: SQLAlchemy session
        event_click: dict as returned by timetable_calendar_ui (eventClick from calendar)
    """
    event_data = event_click["event"]
    props = event_data.get("extendedProps", {})
    event_id = props.get("eventId", "")
    timetable_version = props.get("timetable_version", "")

    # Unique key for chat state for this event/version
    chat_state_key = f"event_chat_state_{event_id}_{timetable_version}"
    if chat_state_key not in st.session_state:
        st.session_state[chat_state_key] = {
            "messages": [],
            "data_profile": profile_sqlalchemy_row(props),
            "language": "is"
        }
    chat_state = st.session_state[chat_state_key]
    chat_manager = EventChatManager(session)
    memory = chat_manager.get_langchain_memory(event_id, timetable_version)
    # Show chat history (from persistent DB via LangChain, not just session state)
    for msg in memory.chat_memory.messages:
        with st.chat_message(msg.type):
            st.markdown(msg.content)
    # New user input
    user_input = st.chat_input("Ask a question or describe a change for this event:")
    if user_input:
        response = chat_manager.handle_event_query(event_id, timetable_version, user_input, props, chat_state)
        with st.chat_message("assistant"):
            st.markdown(response)
