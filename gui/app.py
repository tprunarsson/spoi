import streamlit as st
from sqlalchemy import func, text, or_, inspect
from datetime import datetime
from streamlit_calendar import calendar
import json
import pandas as pd
import re

from spoi.db.models import (
    Institution, Department, Program, ProgramOffering, FieldOfStudy,
    CurriculumComponent, CourseOffering, Event, Room, Course, TimetablePlan
)
from spoi.db.session import SessionLocal

from rag import (
    profile_sqlalchemy_row,
    extract_sql_from_llm_response,
    get_best_rag_example,
    build_llm_prompt_for_event_chat,
    query_ollama,
    smoke_test_sqlalchemy,
    add_rag_example,
    get_all_rags,
    update_rag_example,
    delete_rag_example,
    get_languages_from_rag, 
    get_statuses_from_rag,
    propose_rag_draft
)

EVENT_TYPE_COLOR = {
    "F": "#1976D2", "V": "#388E3C", "D": "#FBC02D", "A": "#7B1FA2", "L": "#E64A19", None: "#607D8B"
}
LANGUAGES = get_languages_from_rag()
STATUSES = get_statuses_from_rag()

# =========================
# Helper functions
# =========================

def extract_localized_name(name_json, lang="is"):
    """Extracts a localized name (Icelandic or fallback) from a JSON string."""
    try:
        d = json.loads(name_json)
        # Prefer requested language, fallback to any value
        return d.get(lang) or d.get("en") or next(iter(d.values()))
    except Exception:
        return str(name_json)

def get_db_schema_str(session):
    inspector = inspect(session.bind)
    tables = inspector.get_table_names()
    schema_lines = []
    for table in tables:
        columns = [col['name'] for col in inspector.get_columns(table)]
        schema_lines.append(f"- {table}: {', '.join(columns)}")
    return "\n".join(schema_lines)

def build_conversation_summary(chat, max_turns=4):
    summary = ""
    for msg in chat["messages"][-max_turns:]:
        role = "User" if msg["role"] == "user" else "Assistant"
        summary += f"{role}: {msg['content']}\n"
    return summary.strip()

def get_institutions(session):
    return session.query(Institution).all()

def get_departments(session, institution_id):
    return session.query(Department).filter_by(institutionId=institution_id).all()

def get_program_offerings(session, department_id, academic_year):
    offerings = (
        session.query(ProgramOffering)
        .join(Program)
        .filter(
            Program.departmentId == department_id,
            ProgramOffering.academicYear == str(academic_year)
        ).all()
    )
    filtered = []
    for o in offerings:
        try:
            t = json.loads(o.title)
            if "ph.d" in (t.get("is", "").lower()) or "ph.d" in (t.get("en", "").lower()):
                continue
        except Exception:
            pass
        filtered.append(o)
    return filtered

def get_fields(session, program_id):
    return session.query(FieldOfStudy).filter_by(programId=program_id).all()

def get_course_name_map(session):
    # Map courseOfferingId → canonicalName (with fallback to code)
    q = session.query(CourseOffering.courseOfferingId, Course.canonicalName).join(Course, CourseOffering.courseCode == Course.courseCode)
    mapping = {}
    for coid, name_json in q:
        try:
            name_dict = json.loads(name_json)
            name = name_dict.get("is") or name_dict.get("en") or list(name_dict.values())[0]
        except Exception:
            name = str(name_json)
        mapping[coid] = name
    return mapping


def get_academic_years(session):
    years = session.query(CourseOffering.academicYear).distinct().all()
    years = sorted([int(y[0]) for y in years if str(y[0]).isdigit()])
    pairs = [(y, f"{y}-{y+1}") for y in years]
    return pairs

def get_term_options(session, sel_year):
    q = session.query(CourseOffering.courseOfferingId).filter(CourseOffering.academicYear == str(sel_year)).all()
    suffixes = {str(coid[0])[-5:] for coid in q if str(coid[0]).isdigit() and len(str(coid[0])) >= 5}
    def label(term_code):
        if term_code.endswith("6"): return f"Haust ({term_code})"
        elif term_code.endswith("0"): return f"Vor ({term_code})"
        else: return f"Misseri {term_code}"
    term_options = {tc: label(tc) for tc in sorted(suffixes)}
    return term_options

def format_time_hhmm(dt):
    return pd.to_datetime(dt).strftime("%H:%M") if dt else ""

def get_weekday_name(dt, lang="en"):
    dt = pd.to_datetime(dt)
    if lang == "is":
        WEEKDAYS_IS = ["Mánudagur", "Þriðjudagur", "Miðvikudagur", "Fimmtudagur", "Föstudagur", "Laugardagur", "Sunnudagur"]
        return WEEKDAYS_IS[dt.weekday()]
    else:
        return dt.strftime("%A")

def safe_value(val):
    if isinstance(val, datetime): return val.isoformat()
    if isinstance(val, (str, int, float, bool)) or val is None: return val
    return str(val)

def event_to_dict(event):
    return {col.name: safe_value(getattr(event, col.name)) for col in event.__table__.columns}

def event_to_calendar_format(event, course_name_map, session):
    course_offering_id = getattr(event, "courseOfferingId", "")
    course_name = course_name_map.get(course_offering_id, course_offering_id)  # fallback to ID if not found
    room = session.query(Room).filter_by(roomId=event.roomId).first()
    room_name = getattr(room, "name", "") if room else event.location or str(event.roomId)
    teachers = getattr(event, "teachers", "") or ""
    event_type = getattr(event, "type", "")
    color = EVENT_TYPE_COLOR.get(event_type, EVENT_TYPE_COLOR[None])
    # Use only the name in the title if you want!
    title = f"{course_name} ({room_name}, {event_type})"
    if teachers:
        title += f" [{teachers}]"
    return {
        "id": f"{event.eventId}",
        "title": title,
        "start": event.start.isoformat(),
        "end": event.end.isoformat(),
        "backgroundColor": color,
        "extendedProps": {**event_to_dict(event), "course_offering_id": course_offering_id, "course_name": course_name, "room_name": room_name, "teachers_display": teachers,
            "start_time_hhmm": format_time_hhmm(event.start), "end_time_hhmm": format_time_hhmm(event.end),
            "weekday_en": get_weekday_name(event.start, "en"), "weekday_is": get_weekday_name(event.start, "is"),
        }
    }

def get_available_timetable_plans(session, sel_term_code):
    plans = session.query(TimetablePlan).filter(
        TimetablePlan.timetablePlanId.like(f"%{sel_term_code}-%")
    ).all()
    return plans

def get_available_versions(session, sel_term_code, selected_course_codes):
    patterns = [f"{code}-{sel_term_code}-%" for code in selected_course_codes]
    query = session.query(TimetablePlan.timetablePlanId)
    filters = [TimetablePlan.timetablePlanId.like(pattern) for pattern in patterns]
    plans = query.filter(or_(*filters)).all()
    versions = set()
    for (plan_id,) in plans:
        segments = plan_id.split('-')
        if len(segments) >= 3:
            version = '-'.join(segments[2:])
            versions.add(version)
    return sorted(list(versions))

# =========================
# Streamlit app
# =========================

st.set_page_config(page_title="Timetable Calendar", layout="wide")

with SessionLocal() as session:
    academic_year_pairs = get_academic_years(session)
    if not academic_year_pairs:
        st.warning("No academic years found in database."); st.stop()
    year_options = {y: label for y, label in academic_year_pairs}
    sel_year = st.sidebar.selectbox(
        "Kennsluár", list(year_options.keys()),
        format_func=lambda y: year_options[y], key="year"
    )
    term_options = get_term_options(session, sel_year)
    if not term_options:
        st.warning("No terms found for this year."); st.stop()
    sel_term_code = st.sidebar.selectbox(
        "Misseri", list(term_options.keys()),
        format_func=lambda k: term_options[k], key="term"
    )

    institutions = get_institutions(session)
    inst_options = {i.institutionId: (i.get_name("is") or i.get_name("en") or i.institutionId) for i in institutions}
    inst_id = st.sidebar.selectbox("Svið", list(inst_options.keys()), format_func=lambda k: inst_options[k], key="inst")
    departments = get_departments(session, inst_id)
    dept_options = {d.departmentId: (d.get_name("is") or d.get_name("en") or d.departmentId) for d in departments}
    dept_id = st.sidebar.selectbox("Deild", list(dept_options.keys()), format_func=lambda k: dept_options[k], key="dept")
    offerings = get_program_offerings(session, dept_id, sel_year)
    if not offerings:
        st.warning("No program offerings for this year and department."); st.stop()
    def get_offering_label(offering):
        try:
            title = json.loads(offering.title)
            return title.get("is") or title.get("en") or offering.programOfferingId
        except Exception:
            return offering.programOfferingId
    offering_options = {o.programOfferingId: get_offering_label(o) for o in offerings}
    offering_id = st.sidebar.selectbox(
        "Námsbraut", list(offering_options.keys()),
        format_func=lambda k: offering_options[k], key="offering"
    )
    selected_offering = next((o for o in offerings if o.programOfferingId == offering_id), None)
    prog_id = selected_offering.programId if selected_offering else None
    fields = get_fields(session, prog_id)
    field_options = {f.fieldOfStudyId: (f.get_name("is") or f.get_name("en") or f.fieldOfStudyId) for f in fields}
    if not field_options: st.warning("No fields of study found for selected program."); st.stop()
    field_id = st.sidebar.selectbox(
        "Kjörsvið", list(field_options.keys()), format_func=lambda k: field_options[k], key="field"
    )
    study_years = sorted(
        {c.studyYear for c in session.query(CurriculumComponent).filter(CurriculumComponent.fieldOfStudyId == field_id)}
    )
    if not study_years: st.warning("No study years found for selected field."); st.stop()
    sel_study_year = st.sidebar.selectbox("Námsár", study_years, key="study_year")

    cc_rows = session.query(CurriculumComponent).filter(
        CurriculumComponent.fieldOfStudyId == field_id,
        CurriculumComponent.studyYear == sel_study_year
    ).all()
    course_base_codes = [r.courseId[4:-5] for r in cc_rows]

    available_versions = get_available_versions(session, sel_term_code, course_base_codes)
    if not available_versions:
        st.warning("No timetable versions found.")
        st.stop()
    sel_version = st.sidebar.selectbox("Stundatöflu-útgáfa:", available_versions)

    course_name_map = get_course_name_map(session)
    offerings = session.query(CourseOffering).filter(
        CourseOffering.courseCode.in_(course_base_codes),
        CourseOffering.academicYear.in_([str(sel_year), str(sel_year+1)]),
        CourseOffering.courseOfferingId.like(f"%{sel_term_code}")
    ).all()
    course_offering_ids = [o.courseOfferingId for o in offerings]

    plan_ids = [f"{code}-{sel_term_code}-{sel_version}" for code in course_base_codes]
    events = session.query(Event).filter(Event.timetablePlanId.in_(plan_ids)).all()
    projected_events = [event_to_calendar_format(e, course_name_map, session) for e in events]

    calendar_options = {
        "initialView": "timeGridWeek",
        "initialDate": str(min(e["start"] for e in projected_events))[:10] if projected_events else "",
        "editable": True, "eventDurationEditable": True, "eventStartEditable": True, "eventResizableFromStart": True,
        "snapDuration": "00:50:00", "slotDuration": "00:50:00",
        "slotMinTime": "08:20:00", "slotMaxTime": "20:00:00",
        "slotLabelFormat": {"hour": "numeric", "minute": "2-digit", "hour12": False},
        "locale": "is", "weekends": False, "firstDay": 1,
        "headerToolbar": {"left": "prev,next today", "center": "title", "right": "timeGridWeek,timeGridDay"},
        "dayHeaderFormat": {"weekday": "short"}, "allDaySlot": False, "height": "auto",
    }
    selected = calendar(
        events=projected_events or [], 
        options=calendar_options,
        key=f"calendar_{field_id}_{sel_year}_{sel_term_code}_{sel_study_year}"
    )

# Save the event click persistently
if selected and "eventClick" in selected:
    st.session_state["last_event_click"] = selected["eventClick"]

event_click = st.session_state.get("last_event_click", None)

if event_click is not None:
    event_data = event_click["event"]
    props = event_data.get("extendedProps", {})
    event_key = props.get("eventId", props.get("courseOfferingId", ""))
    chat_state_key = f"event_chat_state_{event_key}"
    chat_input_key = f"event_chat_input_{event_key}"

    if chat_state_key not in st.session_state:
        st.session_state[chat_state_key] = {
            "messages": [],
            "state": "show_initial",
            "last_user_instruction": "",
            "last_user_intent": "",
            "last_sql": "",
            "last_feedback": "",
            "data_profile": profile_sqlalchemy_row(props),
            "language": "is"
        }

    chat = st.session_state[chat_state_key]

    def show_chat_messages():
        for msg in chat["messages"]:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])

    show_chat_messages()

    if chat["state"] == "show_initial":
        #suggested_nl = generate_suggested_instruction(props, event_data, language=chat["language"])
        chat["messages"].append({
            "role": "assistant",
            "content": (
                "Here are the details of the event:\n"
                f"{chat['data_profile']}\n\n"
                #f"**For example:**\n*{suggested_nl}*\n\n"
                "How would you like to change or query this event?"
            )
        })
        chat["state"] = "waiting_for_instruction"
        st.rerun()

    user_msg = st.chat_input(
        "Ask a question or describe a change for this event:",
        key=chat_input_key
    )

    if user_msg:
        chat["messages"].append({"role": "user", "content": user_msg})

        if chat["state"] == "waiting_for_instruction":
            # Detect intent: info, change, or other            
            intent_prompt = (
                f"User input: '''{user_msg}'''.\n"
                "Does the user want to:\n"
                "A) Get information (like a SELECT SQL query),\n"
                "B) Request a change (like UPDATE/DELETE SQL),\n"
                "C) Something else?\n"
                "Reply ONLY with one word: 'info', 'change', or 'other'. Do not explain."
            )
            intent = query_ollama(intent_prompt).strip().lower()
            intent = intent.split()[0]  # Defensive: get only first word if model returns more
            if intent not in ("info", "change", "other"):
                intent = "other"
            chat["last_user_intent"] = intent

            from spoi.db.session import SessionLocal
            with SessionLocal() as session:
                db_schema_str = get_db_schema_str(session)
                rag_example_summary = get_best_rag_example(user_msg, props)
                prompt = build_llm_prompt_for_event_chat(
                    chat=chat,
                    user_msg=user_msg,
                    db_schema_str=db_schema_str,
                    event_data_profile=chat["data_profile"],
                    rag_example_summary=rag_example_summary,
                    props=props,
                    intent=intent,  # Pass the detected intent
                    language=chat.get("language", "en"),
                )

                if intent == "info":
                    # Get SQL SELECT, run, and display result
                    print(prompt)
                    sql_query = query_ollama(prompt)
                    sql_query = extract_sql_from_llm_response(sql_query)
                    chat["last_sql"] = sql_query
                    # In your chat code (after the LLM responds), save last user/llm output to session state:
                    st.session_state["last_rag_nl"] = user_msg
                    st.session_state["last_rag_sql"] = sql_query
                    # Then in the add example UI, pre-fill:
                    nl_en = st.text_area("NL (English)", value=st.session_state.get("last_rag_nl", ""), key="rag_nl_en")
                    sql = st.text_area("SQL Template", value=st.session_state.get("last_rag_sql", ""), key="rag_sql")
                    try:
                        print(f"Running SQL: {sql_query}")
                        result = session.execute(text(sql_query))
                        rows = result.fetchall()
                        if rows:
                            df = pd.DataFrame(rows, columns=result.keys())

                            # Use chat["language"] for localization (default to "is")
                            lang = chat.get("language", "is")

                            # Automatically decode any JSON string fields
                            for col in df.columns:
                                if df[col].dtype == object and df[col].apply(lambda x: isinstance(x, str) and x.strip().startswith("{")).any():
                                    df[col] = df[col].apply(lambda x: extract_localized_name(x, lang=lang))

                            answer = (
                                        f"Here is the SQL executed:\n```sql\n{sql_query}\n```\n"
                                        f"Here are the results:\n\n{df.to_markdown(index=False)}"
                                    )
                        else:
                            answer = "No results found."
                    except Exception as e:
                        answer = f"Error running SQL:\n\n{e}\n\n(SQL was: ```sql\n{sql_query}\n```)"
                    chat["messages"].append({
                        "role": "assistant",
                        "content": answer
                    })
                    chat["state"] = "waiting_for_instruction"
                    st.rerun()

                elif intent == "change":
                    paraphrased = query_ollama(prompt + "\n\nParaphrase what the user wants to do, in a short sentence.")
                    chat["messages"].append({
                        "role": "assistant",
                        "content": f"I understand you want to: **{paraphrased}**\n\nIs this correct? (Yes/No)"
                    })
                    chat["last_user_instruction"] = user_msg
                    chat["state"] = "waiting_for_confirmation"
                    st.rerun()

                else:
                    response = query_ollama(prompt)
                    chat["messages"].append({
                        "role": "assistant",
                        "content": response
                    })
                    chat["state"] = "waiting_for_instruction"
                    st.rerun()

        elif chat["state"] == "waiting_for_confirmation":
            if user_msg.lower().strip() in ["yes", "já", "y", "ok", "sure"]:
                user_instruction = chat["last_user_instruction"]
                #chat["language"] = "en" # optionally, use detect_language_llama(user_instruction)
                with SessionLocal() as session:
                    db_schema_str = get_db_schema_str(session)
                    rag_example_summary = get_best_rag_example(user_msg, props)
                    prompt = build_llm_prompt_for_event_chat(
                        chat=chat,
                        user_msg=user_msg,
                        db_schema_str=db_schema_str,
                        event_data_profile=chat["data_profile"],
                        rag_example_summary=rag_example_summary,
                        props=props,
                        intent = "change",
                        language=chat.get("language", "en"),
                    )
                    raw_llm_output = query_ollama(prompt)
                    sql_update = extract_sql_from_llm_response(raw_llm_output)
                    chat["last_sql"] = sql_update
                    ok, feedback = smoke_test_sqlalchemy(sql_update, session)
                if ok:
                    what_does_sql_do = query_ollama(
                        f"Explain what this SQL command does in a short sentence:\n```sql\n{sql_update}\n```"
                    )
                    chat["messages"].append({
                        "role": "assistant",
                        "content": (
                            f"Here is the SQL I would use:\n```sql\n{sql_update}\n```\n"
                            f"**What it does:** {what_does_sql_do}\n\n"
                            "Should I apply this change? (Yes/No)"
                        )
                    })
                    chat["state"] = "waiting_for_sql_confirmation"
                else:
                    chat["messages"].append({
                        "role": "assistant",
                        "content": (
                            "❌ I tried to generate a SQL command, but it failed with this error:\n"
                            f"`{feedback}`\n\n"
                            "Could you clarify your request or provide more details?"
                        )
                    })
                    chat["state"] = "waiting_for_instruction"
                st.rerun()
            else:
                chat["messages"].append({
                    "role": "assistant",
                    "content": "OK, please describe your desired change for this event."
                })
                chat["state"] = "waiting_for_instruction"
                st.rerun()

        elif chat["state"] == "waiting_for_sql_confirmation":
            if user_msg.lower().strip() in ["yes", "já", "y", "ok", "sure"]:
                from spoi.db.session import SessionLocal
                try:
                    with SessionLocal() as session:
                        session.execute(text(chat["last_sql"]))
                        session.commit()
                    chat["messages"].append({
                        "role": "assistant",
                        "content": "✅ The change has been applied! What would you like to do next?"
                    })
                except Exception as e:
                    chat["messages"].append({
                        "role": "assistant",
                        "content": f"❌ Error executing SQL: `{e}`\nNo changes were made."
                    })
                chat["state"] = "waiting_for_instruction"
                st.rerun()
            else:
                chat["messages"].append({
                    "role": "assistant",
                    "content": "OK, no changes made. Please describe your next request for this event."
                })
                chat["state"] = "waiting_for_instruction"
                st.rerun()

if event_click is not None:
    with st.expander("🗂️ Manage RAG Examples"):
        tab_curate, tab_edit = st.tabs(["💬 Curate (Add New)", "✏️ Edit/Delete Existing"])

        # ----------------------
        # Tab 1: CURATE (Add New via Chat)
        # ----------------------
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

            # ----- Step 1: Chat/Refine loop -----
            if state['step'] == "chat":
                st.markdown("Describe what you want, then refine through conversation. When ready, accept and save as a draft.")
                # Show chat history
                for msg in state['chat']:
                    st.chat_message(msg['role']).markdown(msg['content'])
                user_msg = st.chat_input("Type your request or reply to the AI to refine:")
                if user_msg:
                    state['chat'].append({"role": "user", "content": user_msg})
                    draft, ai_message, is_json = propose_rag_draft(user_msg, chat_history=state['chat'])
                    if draft:
                        state['draft'] = draft
                    state['chat'].append({"role": "assistant", "content": ai_message})

                # Show draft and Accept button if available
                if state['draft'].get("intent") or state['draft'].get("sql"):
                    st.markdown("#### ✨ Current Draft Proposal")
                    st.info(f"**Intent:** {state['draft'].get('intent','')}\n\n"
                            f"**NL (English):** {state['draft']['nl'].get('en','')}\n\n"
                            f"**SQL:** {state['draft'].get('sql','')}")
                    if st.button("✅ Accept & Save as Draft"):
                        state['step'] = "editing"

                # Manual reset
                if st.button("Reset RAG Curation"):
                    reset_rag_curation()
                state['curating'] = True

            # ----- Step 2: Final Edit/Approve -----
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

            # --- Always show chat log at the bottom while curating ---
            if state['curating'] and state['chat']:
                st.markdown("---")
                st.markdown("### Chat Log")
                for msg in state['chat']:
                    st.chat_message(msg['role']).markdown(msg['content'])

        # ----------------------
        # Tab 2: EDIT/DELETE
        # ----------------------
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

                # Get NL text in selected language, fallback to empty
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
