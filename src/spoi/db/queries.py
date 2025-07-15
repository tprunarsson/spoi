from sqlalchemy import func, and_, inspect
import pandas as pd
from datetime import time
import json

from spoi.db.models import (
    CurriculumComponent,
    CourseOffering,
    Course,
    Event,
    Room,
    CourseClashCount
)

def get_course_name_map(session):
    # Map courseCode → course name (IS, EN, or fallback)
    q = session.query(Course.courseCode, Course.canonicalName)
    mapping = {}
    import json
    for code, name_json in q:
        try:
            name_dict = json.loads(name_json)
            name = name_dict.get("is") or name_dict.get("en") or list(name_dict.values())[0]
        except Exception:
            name = str(name_json)
        mapping[code] = name
    return mapping

def get_calendar_events_for_field(session, field_of_study_id, academic_year=None):
    cc = CurriculumComponent
    co = CourseOffering
    e = Event
    r = Room

    # Map courseId to baseCode, as tested
    base_code = func.substr(
        cc.courseId,
        5,
        func.length(cc.courseId) - 9
    ).label('baseCode')

    # Subquery for curriculum_components with baseCode
    cc_sub = (
        session.query(
            cc.courseId,
            cc.requirementType,
            cc.studyYear,
            cc.semester,
            cc.fieldOfStudyId,
            base_code
        )
        .filter(cc.fieldOfStudyId == field_of_study_id)
        .subquery()
    )

    # Main query to get events
    query = (
        session.query(
            cc_sub.c.courseId,
            cc_sub.c.requirementType,
            cc_sub.c.studyYear,
            cc_sub.c.semester,
            co.courseCode,
            co.courseOfferingId,
            co.academicYear,
            e.start.label("event_start"),
            e.end.label("event_end"),
            e.roomId,
            r.name.label("room_name")
        )
        .join(co, co.courseCode == cc_sub.c.baseCode)
        .join(e, e.courseOfferingId == co.courseOfferingId)
        .outerjoin(r, e.roomId == r.roomId)
    )
    if academic_year:
        query = query.filter(co.academicYear == str(academic_year))
    query = query.order_by(cc_sub.c.studyYear, cc_sub.c.semester, cc_sub.c.courseId, e.start)

    results = query.all()
    if not results:
        return []

    # Build a name lookup (as you did before)
    course_name_map = {c.courseCode: c.get_canonical_name('is') for c in session.query(Course).all()}

    calendar_events = []
    for row in results:
        # Prepare event details
        start_dt = row.event_start
        end_dt = row.event_end
        course_name = course_name_map.get(row.courseCode, row.courseCode)
        calendar_events.append({
            "id": f"{row.courseOfferingId}_{start_dt.date()}_{start_dt.time()}",
            "title": f"{course_name} ({row.courseCode}) - {row.room_name or ''}",
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
            "studyYear": row.studyYear,
            "semester": row.semester,
        })

    return calendar_events

def get_weekly_timetable_pivot(session, field_of_study_id, academic_year=None):
    """
    Returns a dictionary mapping (calendar_week, semester, studyYear) to a Pandas DataFrame.
    Each DataFrame is a weekly timetable with time blocks as rows and days as columns.
    """
    cc = CurriculumComponent
    co = CourseOffering
    e = Event
    r = Room

    # Extract baseCode from courseId for join
    base_code = func.substr(
        cc.courseId,
        5,
        func.length(cc.courseId) - 9  # adjust if your codes are a different length!
    ).label('baseCode')

    # Subquery to get the mapping (for clarity, but can be inlined)
    cc_sub = (
        session.query(
            cc.courseId,
            cc.requirementType,
            cc.studyYear,
            cc.semester,
            cc.fieldOfStudyId,
            base_code
        )
        .subquery()
    )

    # Main query
    query = (
        session.query(
            cc_sub.c.courseId,
            cc_sub.c.requirementType,
            cc_sub.c.studyYear,
            cc_sub.c.semester,
            co.academicYear,
            e.start.label("event_start"),
            e.end.label("event_end"),
            e.roomId,
            r.name.label("room_name")
        )
        .join(co, co.courseCode == cc_sub.c.baseCode)
        .join(e, e.courseOfferingId == co.courseOfferingId)
        .outerjoin(r, e.roomId == r.roomId)
        .filter(cc_sub.c.fieldOfStudyId == field_of_study_id)
    )
    if academic_year:
        query = query.filter(co.academicYear == str(academic_year))
    query = query.order_by(cc_sub.c.studyYear, cc_sub.c.semester, cc_sub.c.courseId, e.start)

    # Build DataFrame
    results = query.all()
    if not results:
        return {}

    import pandas as pd
    df = pd.DataFrame([row._asdict() for row in results])

    # Parse datetimes
    df['event_start'] = pd.to_datetime(df['event_start'])
    df['event_end'] = pd.to_datetime(df['event_end'])

    # Add useful columns
    df['week'] = df['event_start'].dt.isocalendar().week
    df['day_of_week'] = df['event_start'].dt.day_name()
    df['calendar_year'] = df['event_start'].dt.isocalendar().year

    # Assign to 50-minute blocks starting at 8:20
    from datetime import time
    block_start = time(8, 20)
    def block_index(row):
        start = row['event_start'].time()
        minutes_since_midnight = start.hour * 60 + start.minute
        block_start_minutes = block_start.hour * 60 + block_start.minute
        block = (minutes_since_midnight - block_start_minutes) // 50
        return int(block) if block >= 0 else None  # None if before first block

    df['block'] = df.apply(block_index, axis=1)
    df = df[df['block'].notnull()]  # Remove any events before 8:20

    # Calculate block start time as string (e.g., '08:20', '09:10')
    df['block_start_time'] = df['block'].apply(lambda b: (8*60 + 20) + b*50)
    df['block_start_time_str'] = df['block_start_time'].apply(lambda m: f"{int(m)//60:02d}:{int(m)%60:02d}")

    # Display value: course (room)
    df['course_display'] = df['courseId'] + ' (' + df['room_name'].fillna('') + ')'

    # Group and pivot
    timetable_dict = {}
    group_cols = ['week', 'semester', 'studyYear']
    for keys, group in df.groupby(group_cols):
        # Pivot table: time block rows, days columns, showing courses
        pivot = group.pivot_table(
            index='block_start_time_str',
            columns='day_of_week',
            values='course_display',
            aggfunc=lambda x: ', '.join(x.dropna())
        )
        timetable_dict[keys] = pivot

    return timetable_dict


def get_latest_clash_counts(session, filters=None):
    # Optionally filter by some criteria (e.g., for a specific academic year)
    filters = filters or []

    subquery = (
        session.query(
            CourseClashCount.courseA,
            CourseClashCount.courseB,
            CourseClashCount.programId,
            CourseClashCount.fieldOfStudyId,
            CourseClashCount.academicYear,
            func.max(CourseClashCount.fetched_at).label('max_fetched_at')
        )
        .group_by(
            CourseClashCount.courseA,
            CourseClashCount.courseB,
            CourseClashCount.programId,
            CourseClashCount.fieldOfStudyId,
            CourseClashCount.academicYear
        )
        .subquery()
    )

    query = (
        session.query(CourseClashCount)
        .join(subquery, and_(
            CourseClashCount.courseA == subquery.c.courseA,
            CourseClashCount.courseB == subquery.c.courseB,
            CourseClashCount.programId == subquery.c.programId,
            CourseClashCount.fieldOfStudyId == subquery.c.fieldOfStudyId,
            CourseClashCount.academicYear == subquery.c.academicYear,
            CourseClashCount.fetched_at == subquery.c.max_fetched_at
        ))
    )
    if filters:
        query = query.filter(*filters)

    return query.all()

def get_db_schema_str(session):
    """Return a text description of all tables/columns in the DB."""
    inspector = inspect(session.bind)
    tables = inspector.get_table_names()
    schema_lines = []
    for table in tables:
        columns = [col['name'] for col in inspector.get_columns(table)]
        schema_lines.append(f"- {table}: {', '.join(columns)}")
    return "\n".join(schema_lines)

def extract_localized_name(name_json, lang="is"):
    """Extract localized name from JSON string."""
    try:
        d = json.loads(name_json)
        return d.get(lang) or d.get("en") or next(iter(d.values()))
    except Exception:
        return str(name_json)