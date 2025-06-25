import streamlit as st
from sqlalchemy import func, text, or_
from datetime import datetime, timedelta
from streamlit_calendar import calendar
import traceback
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
    find_best_example,
    extract_sql_from_llm_response,
    generate_suggested_instruction,
    generate_event_prompt,
    query_ollama,
    smoke_test_sqlalchemy,
)

EVENT_TYPE_COLOR = {
    "F": "#1976D2", "V": "#388E3C", "D": "#FBC02D", "A": "#7B1FA2", "L": "#E64A19", None: "#607D8B"
}

# =========================
# Helper functions
# =========================

def patch_sqlite_sql(sql):
    """Patch LLM-generated SQL for SQLite compatibility."""
    sql = re.sub(r"(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})", r"\1 \2", sql)
    # Ensure common id fields are quoted if not already
    for col in ["courseOfferingId", "timetablePlanId", "componentOfferingId"]:
        sql = re.sub(rf"{col}=([^\s'\";]+)", rf"{col}='\1'", sql)
    return sql

def fix_sqlite_datetime(dt):
    if isinstance(dt, str):
        d = pd.to_datetime(dt)
        return d.strftime('%Y-%m-%d %H:%M:%S.%f')
    elif isinstance(dt, datetime):
        return dt.strftime('%Y-%m-%d %H:%M:%S.%f')
    return dt


def preview_rows_before_after(session, sql):
    """Preview the rows that will be affected by the SQL's WHERE clause."""
    m = re.search(r"WHERE (.+)", sql, re.IGNORECASE)
    if not m:
        st.warning("Cannot preview affected rows, WHERE clause not found.")
        return []
    where = m.group(1)
    try:
        select_sql = f"SELECT * FROM events WHERE {where}"
        rows = session.execute(text(select_sql)).fetchall()
        if rows:
            st.info("Rows that will be affected (before update):")
            st.json([dict(row) for row in rows])
        else:
            st.warning("No matching rows found for this WHERE clause.")
        return rows
    except Exception as e:
        st.warning(f"Could not preview rows: {e}")
        return []

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

def get_course_name_map_(session):
    q = session.query(Course.courseCode, Course.canonicalName)
    mapping = {}
    for code, name_json in q:
        try:
            name_dict = json.loads(name_json)
            name = name_dict.get("is") or name_dict.get("en") or list(name_dict.values())[0]
        except Exception:
            name = str(name_json)
        mapping[code] = name
    return mapping

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

def event_to_calendar_format_(event, course_name_map, session):
    # Lookups
    course_code = getattr(event, "courseOfferingId", "")[0:8] if getattr(event, "courseOfferingId", "") else ""
    course_name = course_name_map.get(course_code, course_code)
    room = session.query(Room).filter_by(roomId=event.roomId).first()
    room_name = getattr(room, "name", "") if room else event.location or str(event.roomId)
    teachers = getattr(event, "teachers", "") or ""
    event_type = getattr(event, "type", "")
    color = EVENT_TYPE_COLOR.get(event_type, EVENT_TYPE_COLOR[None])
    title = f"{course_code} - {course_name} ({room_name}, {event_type})"
    if teachers:
        title += f" [{teachers}]"
    return {
        "id": f"{event.eventId}",
        "title": title,
        "start": event.start.isoformat(),
        "end": event.end.isoformat(),
        "backgroundColor": color,
        "extendedProps": {**event_to_dict(event), "course_code": course_code, "course_name": course_name, "room_name": room_name, "teachers_display": teachers,
            "start_time_hhmm": format_time_hhmm(event.start), "end_time_hhmm": format_time_hhmm(event.end),
            "weekday_en": get_weekday_name(event.start, "en"), "weekday_is": get_weekday_name(event.start, "is"),
        }
    }

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
        "Námsbraut (útgáfa)", list(offering_options.keys()),
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
    sel_version = st.sidebar.selectbox("Útgáfa (útgáfur fyrir þetta misseri)", available_versions)

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

    # --- Event click logic ---
    if selected and "eventClick" in selected:
        event_data = selected["eventClick"]["event"]
        props = event_data.get("extendedProps", {})
        suggested_nl = generate_suggested_instruction(props, event_data, language="is")
        pd.to_datetime(props.get('start')).strftime('%Y-%m-%d %H:%M')
        user_instruction = st.text_area(
            f"Lýstu hvernig þú vilt breyta þessum viðburði: {props.get('course_code','?')}: {props.get('course_name','?')} ({pd.to_datetime(props.get('start')).strftime('%Y-%m-%d %H:%M')}-{pd.to_datetime(props.get('end')).strftime('%Y-%m-%d %H:%M')})",
            #value=suggested_nl,
            placeholder="T.d. Færðu námskeiðið IÐN401G úr N-209 á þriðjudegi klukkan 10:20 í M-107 á föstudegi klukkan 14:00.",
            height=100, key="event_nl_instruction"
        )
        data_profile = profile_sqlalchemy_row(props)
        best_example, detected_language = find_best_example(user_instruction)
        populated_example = best_example if best_example else None
        llm_prompt = generate_event_prompt(
            event_data, user_instruction, data_profile,
            populated_example, language=detected_language
        )

        if st.button("Búa til SQL skipun fyrir þessa breytingu"):
            raw_sql = query_ollama(llm_prompt)
            sql_update = extract_sql_from_llm_response(raw_sql)
            sql_update = patch_sqlite_sql(sql_update)
            st.session_state['sql_update'] = sql_update
            with SessionLocal() as session:
                success, feedback = smoke_test_sqlalchemy(sql_update, session)
                st.session_state['sql_success'] = success
                st.session_state['sql_feedback'] = feedback

        if 'sql_update' in st.session_state:
            st.markdown("#### SQL skipunin sem var búin til")
            st.code(st.session_state['sql_update'], language="sql")
            if st.session_state.get('sql_success'):
                st.success(st.session_state.get('sql_feedback', ''))
            else:
                st.error(st.session_state.get('sql_feedback', ''))

        if st.session_state.get('sql_update') and st.session_state.get('sql_success'):
            if st.button("Framkvæma SQL skipun og uppfæra töflu", type="primary"):
                try:
                    sql_update = st.session_state['sql_update']
                    print("\n========== SQL EXECUTION DEBUG ==========")
                    print("SQL to be executed:")
                    print(sql_update)
                    with SessionLocal() as session:
                        preview_rows_before_after(session, sql_update)
                        result = session.execute(text(sql_update))
                        print(f"Rowcount affected: {getattr(result, 'rowcount', 'unknown')}")
                        session.commit()
                        preview_rows_before_after(session, sql_update)
                        print("=========================================\n")
                    st.success("SQL skipunin hefur verið framkvæmd og gagnagrunnurinn hefur verið uppfærður.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Villa kom upp við framkvæmd SQL skipunarinnar: {e}")
                    print("\n========== SQL EXECUTION ERROR ==========")
                    print(traceback.format_exc())
                    print("=========================================\n")

    if 'llm_prompt' in locals():
        with st.expander("Sýna fullan LLM skeyti (til villuleitar)", expanded=False):
            st.text_area("Prompt sent to Llama", llm_prompt, height=600)
