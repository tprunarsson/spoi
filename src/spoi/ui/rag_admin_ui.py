import streamlit as st
from rag import (
    add_rag_example,
    get_all_rags,
    update_rag_example,
    delete_rag_example,
    get_languages_from_rag,
    get_statuses_from_rag,
    propose_rag_draft
)

LANGUAGES = get_languages_from_rag()
STATUSES = get_statuses_from_rag()

def rag_admin_ui(session):
    """
    RAG example curation/admin UI (always available).
    Args:
        session: SQLAlchemy session
    """
    with st.expander("🗂️ Manage RAG Examples", expanded=False):
        tab_curate, tab_edit = st.tabs(["💬 Curate (Add New)", "✏️ Edit/Delete Existing"])

        # CURATE
        with tab_curate:
            st.subheader("💡 Add New RAG Example (with AI Curation Chat)")
            if 'rag_curation_state' not in st.session_state:
                st.session_state['rag_curation_state'] = {
                    "chat": [],
                    "curating": False,
                    "draft": {"intent": "", "nl": {"en": ""}, "sql": "", "status": "draft"},
                    "step": "chat"
                }
            state = st.session_state['rag_curation_state']

            def reset_rag_curation():
                state['chat'] = []
                state['curating'] = False
                state['draft'] = {"intent": "", "nl": {"en": ""}, "sql": "", "status": "draft"}
                state['step'] = "chat"

            # Chat/Refine loop
            if state['step'] == "chat":
                st.markdown("Describe what you want, then refine through conversation. When ready, accept and save as a draft.")
                for msg in state['chat']:
                    st.chat_message(msg['role']).markdown(msg['content'])
                user_msg = st.chat_input("Type your request or reply to the AI to refine (RAG curation):")
                if user_msg:
                    state['chat'].append({"role": "user", "content": user_msg})
                    draft, ai_message, is_json = propose_rag_draft(user_msg, chat_history=state['chat'])
                    if draft:
                        state['draft'] = draft
                    state['chat'].append({"role": "assistant", "content": ai_message})
                if state['draft'].get("intent") or state['draft'].get("sql"):
                    st.markdown("#### ✨ Current Draft Proposal")
                    st.info(f"**Intent:** {state['draft'].get('intent','')}\n\n"
                            f"**NL (English):** {state['draft']['nl'].get('en','')}\n\n"
                            f"**SQL:** {state['draft'].get('sql','')}")
                    if st.button("✅ Accept & Save as Draft"):
                        state['step'] = "editing"
                if st.button("Reset RAG Curation"):
                    reset_rag_curation()
                state['curating'] = True

            # Final Edit/Approve
            elif state['step'] == "editing":
                st.success("AI has proposed a draft. Please review, edit, or approve below.")
                with st.form("curate_rag_form", clear_on_submit=False):
                    intent = st.text_input("Intent", value=state['draft'].get('intent', ''), key="curate_intent")
                    nl_en = st.text_area("NL (English)", value=state['draft']['nl'].get('en', ''), key="curate_nl_en")
                    sql = st.text_area("SQL Template", value=state['draft'].get('sql', ''), key="curate_sql")
                    col1, col2 = st.columns([1,1])
                    with col1:
                        approve = st.form_submit_button("✅ Approve & Save as Draft")
                    with col2:
                        restart = st.form_submit_button("❌ Start Over")
                if approve:
                    try:
                        add_rag_example(intent, {"en": nl_en}, sql)
                        st.success("Draft RAG example saved! It will be available for future completions (pending verification).")
                        reset_rag_curation()
                    except Exception as e:
                        st.error(f"Error saving: {e}")
                elif restart:
                    reset_rag_curation()
            if state['curating'] and state['chat']:
                st.markdown("---")
                st.markdown("### Chat Log")
                for msg in state['chat']:
                    st.chat_message(msg['role']).markdown(msg['content'])

        # EDIT/DELETE
        with tab_edit:
            st.subheader("✏️ Edit or Delete Existing RAG Example")
            all_rags = get_all_rags()
            if not all_rags:
                st.info("No RAG examples found.")
            else:
                rag_options = [
                    f"{r['intent']} (id={r['id']}, status={r.get('status','')}, author={r.get('author','')}, created={r['created_at']})"
                    for r in all_rags
                ]
                idx = st.selectbox("Select a RAG example to edit", range(len(all_rags)), format_func=lambda i: rag_options[i], key="edit_rag_idx")
                selected = all_rags[idx]
                edit_lang = st.selectbox("Edit language", list(LANGUAGES.keys()), format_func=lambda k: LANGUAGES[k], key="edit_rag_lang")
                edit_nl_value = selected["nl"].get(edit_lang, "")
                edit_intent = st.text_input("Edit Intent", value=selected["intent"], key=f"edit_intent_{selected['id']}")
                edit_nl = st.text_area(f"Edit NL ({LANGUAGES[edit_lang]})", value=edit_nl_value, key=f"edit_nl_{selected['id']}_{edit_lang}")
                edit_sql = st.text_area("Edit SQL", value=selected["sql"], key=f"edit_sql_{selected['id']}")
                edit_author = st.text_input("Author", value=selected.get("author",""), key=f"edit_author_{selected['id']}")
                edit_version = st.number_input("Version", min_value=1, value=selected.get("version", 1), key=f"edit_version_{selected['id']}")
                edit_status = st.selectbox("Status", STATUSES, index=STATUSES.index(selected.get("status", "draft")), key=f"edit_status_{selected['id']}")
                col1, col2 = st.columns([2,1])
                with col1:
                    if st.button("Update Example", key=f"update_rag_{selected['id']}"):
                        nl_dict = dict(selected["nl"]) if isinstance(selected["nl"], dict) else {}
                        nl_dict[edit_lang] = edit_nl
                        update_rag_example(selected["id"], edit_intent, nl_dict, edit_sql)
                        st.success("Updated! Refresh the page to see changes.")
                with col2:
                    if st.button("Delete Example", key=f"delete_rag_{selected['id']}"):
                        delete_rag_example(selected["id"])
                        st.warning("Deleted! Refresh the page to see changes.")
