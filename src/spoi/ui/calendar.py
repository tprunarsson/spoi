import streamlit as st
from sqlalchemy import or_
import json
import pandas as pd
from spoi.db.models import (
    Institution, Department, Program, ProgramOffering, FieldOfStudy,
    CurriculumComponent, CourseOffering, Event, Room, TimetablePlan, Course
)
from spoi.db.queries import get_db_schema_str, extract_localized_name

EVENT_TYPE_COLOR = {
    "F": "#1976D2", "V": "#388E3C", "D": "#FBC02D", "A": "#7B1FA2", "L": "#E64A19", None: "#607D8B"
}

# --- Helper functions ---

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
    # Filter out PhD programs
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

def get_course_name_map(session):
    # Map courseOfferingId → canonicalName (with fallback to code)
    q = session.query(
        CourseOffering.courseOfferingId,
        Course.canonicalName
    ).join(
        Course, CourseOffering.courseCode == Course.courseCode
    )
    mapping = {}
    for coid, name_json in q:
        try:
            name_dict = json.loads(name_json)
            name = name_dict.get("is") or name_dict.get("en") or list(name_dict.values())[0]
        except Exception:
            name = str(name_json)
        mapping[coid] = name
    return mapping


def event_to_dict(event):
    from datetime import datetime
    def safe_value(val):
        if isinstance(val, datetime): return val.isoformat()
        if isinstance(val, (str, int, float, bool)) or val is None: return val
        return str(val)
    return {col.name: safe_value(getattr(event, col.name)) for col in event.__table__.columns}

def format_time_hhmm(dt):
    import pandas as pd
    return pd.to_datetime(dt).strftime("%H:%M") if dt else ""

def get_weekday_name(dt, lang="en"):
    import pandas as pd
    dt = pd.to_datetime(dt)
    if lang == "is":
        WEEKDAYS_IS = ["Mánudagur", "Þriðjudagur", "Miðvikudagur", "Fimmtudagur", "Föstudagur", "Laugardagur", "Sunnudagur"]
        return WEEKDAYS_IS[dt.weekday()]
    else:
        return dt.strftime("%A")

def event_to_calendar_format(event, course_name_map, session):
    course_offering_id = getattr(event, "courseOfferingId", "")
    course_name = course_name_map.get(course_offering_id, course_offering_id)
    room = session.query(Room).filter_by(roomId=event.roomId).first()
    room_name = getattr(room, "name", "") if room else event.location or str(event.roomId)
    teachers = getattr(event, "teachers", "") or ""
    event_type = getattr(event, "type", "")
    color = EVENT_TYPE_COLOR.get(event_type, EVENT_TYPE_COLOR[None])
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


def timetable_calendar_ui(session):
    """Main entry for calendar selection & event UI. Returns state dict."""

    from streamlit_calendar import calendar  # only needed here

    # --- (All your selection widgets) ---

    academic_year_pairs = get_academic_years(session)
    if not academic_year_pairs:
        st.warning("No academic years found in database."); st.stop()
    year_options = {y: label for y, label in academic_year_pairs}
    sel_year = st.sidebar.selectbox("Kennsluár", list(year_options.keys()), format_func=lambda y: year_options[y], key="year")

    term_options = get_term_options(session, sel_year)
    if not term_options:
        st.warning("No terms found for this year."); st.stop()
    sel_term_code = st.sidebar.selectbox("Misseri", list(term_options.keys()), format_func=lambda k: term_options[k], key="term")

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
    offering_id = st.sidebar.selectbox("Námsbraut", list(offering_options.keys()), format_func=lambda k: offering_options[k], key="offering")

    selected_offering = next((o for o in offerings if o.programOfferingId == offering_id), None)
    prog_id = selected_offering.programId if selected_offering else None
    fields = get_fields(session, prog_id)
    field_options = {f.fieldOfStudyId: (f.get_name("is") or f.get_name("en") or f.fieldOfStudyId) for f in fields}
    if not field_options: st.warning("No fields of study found for selected program."); st.stop()
    field_id = st.sidebar.selectbox("Kjörsvið", list(field_options.keys()), format_func=lambda k: field_options[k], key="field")

    study_years = sorted({c.studyYear for c in session.query(CurriculumComponent).filter(CurriculumComponent.fieldOfStudyId == field_id)})
    if not study_years: st.warning("No study years found for selected field."); st.stop()
    sel_study_year = st.sidebar.selectbox("Námsár", study_years, key="study_year")

    cc_rows = session.query(CurriculumComponent).filter(
        CurriculumComponent.fieldOfStudyId == field_id,
        CurriculumComponent.studyYear == sel_study_year
    ).all()
    course_base_codes = [r.courseId[4:-5] for r in cc_rows]

    available_versions = get_available_versions(session, sel_term_code, course_base_codes)
    if not available_versions:
        st.warning("No timetable versions found."); st.stop()
    sel_version = st.sidebar.selectbox("Stundatöflu-útgáfa:", available_versions)

    course_name_map = get_course_name_map(session)
    offerings = session.query(CourseOffering).filter(
        CourseOffering.courseCode.in_(course_base_codes),
        CourseOffering.academicYear.in_([str(sel_year), str(sel_year+1)]),
        CourseOffering.courseOfferingId.like(f"%{sel_term_code}")
    ).all()
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

    # Persist selection
    if selected and "eventClick" in selected:
        st.session_state["last_event_click"] = selected["eventClick"]

    event_click = st.session_state.get("last_event_click", None)
    # Always return a dict (possibly empty)
    return {"event_click": event_click} if event_click else {}

# End of file