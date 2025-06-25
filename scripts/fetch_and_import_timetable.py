import requests
import json
import datetime
from collections import defaultdict
from bs4 import BeautifulSoup
from spoi.db.models import (
    TimetablePlan, Event, CourseOffering, CurriculumComponent, Component, ComponentOffering
)
from spoi.db.session import SessionLocal

TIMETABLE_URL = (
    "https://ugla.hi.is/kennsluskra/index.php"
    "?tab=nam&chapter=namskeid_stundartafla_ajax"
    "&start={year}-01-01T00:00:00Z"
    "&end={year}-12-31T23:59:59Z"
    "&id={course_offering_id}"
)

def parse_tooltip_fields(tooltip_html):
    soup = BeautifulSoup(tooltip_html, "html.parser")
    def get_field(label):
        for div in soup.find_all("div"):
            if label in div.text:
                spans = div.find_all("span")
                if len(spans) >= 2:
                    return spans[1].text.strip()
        return ""
    return {
        "type": get_field("Tegund"),
        "location": get_field("Staður"),
        "group": get_field("Hópur"),
        "note": get_field("Athugasemd"),
        "teachers": get_field("Kennarar"),
    }

def fetch_and_store_historic_timetable(target_year):
    session = SessionLocal()

    # Fetch all offerings for the given year
    courses = session.query(CourseOffering).filter_by(academicYear=str(target_year)).all()
    print(f"Found {len(courses)} course offerings for {target_year}")

    for co in courses:
        term = co.courseOfferingId[-1]
        # For historic import, fetch previous year’s events
        hist_year = target_year - 1
        hist_co_id = co.courseOfferingId[:-5] + f"{hist_year}{term}"
        print(f"Fetching events for historic course offering: {hist_co_id} (year: {hist_year})")
        url = TIMETABLE_URL.format(year=hist_year, course_offering_id=hist_co_id)
        print(f"URL: {url}")

        try:
            r = requests.get(url, timeout=10)
            r.raise_for_status()
            timetable_data = r.json()
        except Exception as e:
            print(f"  [ERROR] Could not fetch timetable: {e}")
            continue

        if not timetable_data:
            print(f"  No events for {hist_co_id}")
            continue

        # --- Parse and group events ---
        event_groups = defaultdict(list)
        events_with_fields = []
        for entry in timetable_data:
            tooltip = entry.get("tooltip", "")
            fields = parse_tooltip_fields(tooltip)
            key = (fields["type"], fields["group"] or None)
            event_dict = {
                "start": entry["start"],
                "end": entry["end"],
                "location": fields["location"],
                "type": fields["type"],
                "group": fields["group"] or None,
                "note": fields["note"],
                "teachers": fields["teachers"],
            }
            event_groups[key].append(event_dict)
            events_with_fields.append(event_dict)

        # --- Use the actual event year for plan id/name ---
        starts = [datetime.datetime.fromisoformat(e["start"]) for e in events_with_fields]
        if starts:
            min_start = min(starts)
            event_year = min_start.year
            plan_created = min_start
        else:
            event_year = hist_year  # fallback
            plan_created = datetime.datetime.now(datetime.timezone.utc)

        plan_id = f"{co.courseCode}-{event_year}{term}-historic"
        plan_name = f"Historic {event_year} term {term}"

        plan = session.query(TimetablePlan).filter_by(timetablePlanId=plan_id).first()
        if not plan:
            plan = TimetablePlan(
                timetablePlanId=plan_id,
                name=plan_name,
                type="historic",
                createdAt=plan_created,
                description=f"Imported historic timetable for {co.courseCode} {event_year}{term}",
                sourceInfo=url,
            )
            session.add(plan)
            session.flush()

        # --- ENSURE COMPONENT & CREATE COMPONENT OFFERINGS ---
        component_offering_map = {}
        for (event_type, group), group_events in event_groups.items():
            component = (
                session.query(Component)
                .filter_by(name=event_type)
                .first()
            )
            if not component:
                component = Component(name=event_type)
                session.add(component)
                session.flush()

            component_offering_id = f"{co.courseOfferingId}-{event_type or 'None'}-{group or 'None'}"
            component_offering = (
                session.query(ComponentOffering)
                .filter_by(componentOfferingId=component_offering_id)
                .first()
            )
            if not component_offering:
                name_map = json.dumps({"en": event_type, "is": event_type})
                component_offering = ComponentOffering(
                    componentOfferingId=component_offering_id,
                    courseOfferingId=co.courseOfferingId,
                    componentId=component.componentId if hasattr(component, "componentId") else event_type,
                    type=event_type,
                    name=name_map,
                    group=group,
                    maxGroupSize=None,
                    usesOverflowRooms=None,
                )
                session.add(component_offering)
                session.flush()
            component_offering_map[(event_type, group)] = component_offering

        # --- ADD EVENTS AND LINK TO COMPONENT OFFERINGS ---
        added = 0
        for e in events_with_fields:
            component_offering = component_offering_map[(e["type"], e["group"] or None)]
            event_obj = Event(
                timetablePlanId=plan.timetablePlanId,
                courseOfferingId=co.courseOfferingId,
                componentOfferingId=component_offering.componentOfferingId,
                start=datetime.datetime.fromisoformat(e["start"]),
                end=datetime.datetime.fromisoformat(e["end"]),
                location=e["location"],
                type=e["type"],
                group=e["group"],
                note=e["note"],
                teachers=e["teachers"] or None,
            )
            session.add(event_obj)
            added += 1
        session.commit()
        print(f"  Added {added} events to {plan_id} (and populated ComponentOfferings)")
    session.close()

if __name__ == "__main__":
    fetch_and_store_historic_timetable(2025)
    fetch_and_store_historic_timetable(2024)
    print("Historic timetable import completed.")
