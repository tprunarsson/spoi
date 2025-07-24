import sys
from datetime import datetime, timedelta
from sqlalchemy import text
import pandas as pd

from spoi.db.models import TimetablePlan, Event
from spoi.db.session import SessionLocal

# --- Term code inference (adjust as needed) ---
def term_code(year, term):
    if term.lower() in ('spring', 'vor'):
        return f"{year}0"
    elif term.lower() in ('fall', 'haust'):
        return f"{year}6"
    else:
        raise ValueError("Term must be 'spring' or 'fall'")

def get_sem_start(year, term):
    if term.lower() == "spring":
        jan1 = datetime(year, 1, 1)
        days_to_monday = (0 - jan1.weekday()) % 7
        return jan1 + timedelta(days=days_to_monday)
    elif term.lower() == "fall":
        last_aug = datetime(year, 8, 31)
        days_to_monday = (last_aug.weekday() - 0) % 7
        return last_aug - timedelta(days=days_to_monday)
    else:
        raise ValueError("Term must be 'spring' or 'fall'")

def project_event_date(orig_start, orig_sem_start, target_sem_start):
    week_num = orig_start.isocalendar()[1]
    dow = orig_start.isocalendar()[2]
    week_offset = week_num - orig_sem_start.isocalendar()[1]
    new_event_date = target_sem_start + timedelta(weeks=week_offset, days=(dow-1))
    new_event_start = datetime.combine(new_event_date, orig_start.time())
    return new_event_start

def main(from_year, from_term, from_version, to_year, to_version):
    from_termcode = term_code(from_year, from_term)
    to_termcode = term_code(to_year, from_term)  # clone "spring" to "spring" etc.

    session = SessionLocal()

    # Find all plans matching pattern *-{from_termcode}-{from_version}
    plan_pattern = f"%-{from_termcode}-{from_version}"
    plans = session.query(TimetablePlan).filter(TimetablePlan.timetablePlanId.like(plan_pattern)).all()
    if not plans:
        print(f"No plans found for {plan_pattern}")
        return

    print(f"Found {len(plans)} plans to clone from {from_termcode}-{from_version} to {to_termcode}-{to_version}")

    orig_sem_start = None  # will compute per plan
    target_sem_start = get_sem_start(to_year, from_term)
    for plan in plans:
        old_plan_id = plan.timetablePlanId
        # New plan id: replace year+term and version
        new_plan_id = old_plan_id.replace(f"{from_termcode}-{from_version}", f"{to_termcode}-{to_version}")
        print(f"  Cloning: {old_plan_id} -> {new_plan_id}")

        # Create new plan if needed
        new_plan = session.query(TimetablePlan).filter_by(timetablePlanId=new_plan_id).first()
        if not new_plan:
            new_plan = TimetablePlan(
                timetablePlanId=new_plan_id,
                scenarioId=plan.scenarioId,  # <-- FIX: copy from original plan!
                name=f"Projected from {old_plan_id}",
                type="projected",
                createdAt=datetime.now(),
                description=f"Projection of {old_plan_id} to {to_year} {from_term}, {to_version}",
                sourceInfo=f"Cloned from {old_plan_id}"
            )
            session.add(new_plan)
            session.commit()


        # Find original semester start (first event, Monday of that week)
        old_events = session.query(Event).filter_by(timetablePlanId=old_plan_id).all()
        if not old_events:
            print(f"    [!] No events in {old_plan_id}, skipping.")
            continue
        orig_sem_start = min([pd.to_datetime(e.start) for e in old_events])
        orig_sem_start = orig_sem_start - timedelta(days=orig_sem_start.weekday())

        # Project and clone each event
        count = 0
        for event in old_events:
            orig_start = pd.to_datetime(event.start)
            orig_end = pd.to_datetime(event.end)
            new_start = project_event_date(orig_start, orig_sem_start, target_sem_start)
            new_end = project_event_date(orig_end, orig_sem_start, target_sem_start)
            new_event = Event(
                timetablePlanId=new_plan.timetablePlanId,
                courseOfferingId=event.courseOfferingId.replace(from_termcode, to_termcode),
                componentOfferingId=event.componentOfferingId.replace(from_termcode, to_termcode) if event.componentOfferingId else None,
                eventGroupId=event.eventGroupId,
                start=new_start,
                end=new_end,
                location=event.location,
                roomId=event.roomId,
                type=event.type,
                group=event.group,
                note=event.note,
                teachers=event.teachers,
            )
            session.add(new_event)
            count += 1
        session.commit()
        print(f"    Added {count} events to {new_plan_id}")
    session.close()

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Batch-clone timetable plans from one year/term/version to another.")
    parser.add_argument("from_year", type=int, help="Source year, e.g. 2025")
    parser.add_argument("from_term", help="'spring' or 'fall'")
    parser.add_argument("from_version", help="Version string, e.g. 'historic'")
    parser.add_argument("to_year", type=int, help="Target year, e.g. 2026")
    parser.add_argument("to_version", help="Version string, e.g. 'version-1'")

    args = parser.parse_args()
    main(
        from_year=args.from_year,
        from_term=args.from_term,
        from_version=args.from_version,
        to_year=args.to_year,
        to_version=args.to_version,
    )
