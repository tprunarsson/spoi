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
    history = chat_manager.get_chat_history(event_id, timetable_version)

    # --- Deletion Confirmation State ---
    delete_key = f"confirm_delete_{event_id}_{timetable_version}"
    if delete_key not in st.session_state:
        st.session_state[delete_key] = False

    for msg in history:
        # If using HumanMessage/AIMessage from langchain_core.messages:
        if hasattr(msg, "type"):
            role = "user" if msg.type == "human" else "assistant"
        else:
            # fallback for dict-based messages if needed
            role = msg.get("role", "assistant")
        with st.chat_message(role):
            st.markdown(msg.content)

    # --- Delete Button & Confirmation ---
    if history:
        if not st.session_state[delete_key]:
            if st.button("🗑️ Delete Chat History for This Event"):
                st.session_state[delete_key] = True
                st.rerun()  # Rerun to show the confirmation form
        else:
            with st.form("delete_confirm_form"):
                st.warning("Are you sure you want to delete all chat history for this event?")
                confirm = st.form_submit_button("Yes, delete")
                cancel = st.form_submit_button("Cancel")
                if confirm:
                    num_deleted = chat_manager.delete_event_chat_history(event_id, timetable_version)
                    st.success(f"Deleted {num_deleted} chat messages for this event.")
                    st.session_state[delete_key] = False
                    st.rerun()
                elif cancel:
                    st.info("Deletion cancelled.")
                    st.session_state[delete_key] = False
                    st.rerun()


    # New user input
    user_input = st.chat_input("Ask a question or describe a change for this event:")
    if user_input:
        response = chat_manager.handle_event_query(event_id, timetable_version, user_input, props, chat_state)
        with st.chat_message("assistant"):
            st.markdown(response)
