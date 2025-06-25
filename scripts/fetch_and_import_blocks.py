import json
from collections import Counter, defaultdict
from spoi.db.models import (
    CourseOffering, Event,
    TimeBlock, BlockInterval, CourseOfferingBlock, Room, Building
)
from spoi.db.session import SessionLocal

MIN_OCCURRENCES = 4  # You can adjust this as needed

def intervals_key(intervals):
    # intervals: list of (weekday, start, end)
    return tuple(sorted(intervals))

def find_or_create_block_(session, intervals):
    """
    intervals: list of (weekday, start_time, end_time)
    Returns block (object) of matching block, or creates a new one.
    """
    key = intervals_key(intervals)
    for block in session.query(TimeBlock).all():
        block_intervals = [(i.day, i.start_time, i.end_time) for i in block.intervals]
        if intervals_key(block_intervals) == key:
            return block

    desc = " + ".join(f"{d} {s}-{e}" for d, s, e in key)
    block = TimeBlock(description=desc)
    session.add(block)
    session.flush()
    for day, start, end in key:
        interval = BlockInterval(blockId=block.blockId, day=day, start_time=start, end_time=end)
        session.add(interval)
    session.flush()
    return block

def find_or_create_block(session, intervals):
    """
    intervals: list of (weekday, start_time, end_time)
    Returns block (object) of matching block, or creates a new one.
    """
    key = intervals_key(intervals)
    # DEDUPLICATE intervals for block description
    desc_intervals = sorted(set(key))  # Unique and sorted
    desc = " + ".join(f"{d} {s}-{e}" for d, s, e in desc_intervals)
    # Check for existing block
    for block in session.query(TimeBlock).all():
        block_intervals = [(i.day, i.start_time, i.end_time) for i in block.intervals]
        if intervals_key(block_intervals) == key:
            return block
    # Otherwise, create
    block = TimeBlock(description=desc)
    session.add(block)
    session.flush()
    for day, start, end in desc_intervals:   # Use deduped!
        interval = BlockInterval(blockId=block.blockId, day=day, start_time=start, end_time=end)
        session.add(interval)
    session.flush()
    return block

def weekday_name(dt):
    return dt.strftime("%a")  # e.g., "Mon", "Tue", etc.

def extract_room_building(session, location):
    if not location or "-" not in location:
        return (None, None)
    building_part, room_part = [s.strip() for s in location.split("-", 1)]
    room = session.query(Room).filter(Room.name == room_part).first()
    building = session.query(Building).filter(Building.name.like(f"%{building_part}%")).first()
    room_id = room.roomId if room else None
    building_id = building.buildingId if building else None
    return (room_id, building_id)

def process_course(session, co, min_occurrences=MIN_OCCURRENCES):
    # Find historic timetable events for this course offering
    events = session.query(Event).filter(
        Event.courseOfferingId == co.courseOfferingId,
        Event.timetablePlanId.like("%-historic")
    ).all()
    if not events:
        print(f"[SKIP] No events for {co.courseOfferingId}")
        return

    # Group by type
    by_type = defaultdict(list)
    for ev in events:
        by_type[ev.type or ""].append(ev)

    for event_type, events in by_type.items():
        # Count (weekday, start, end, location) occurrences
        counter = Counter()
        all_intervals = []
        for ev in events:
            wd = weekday_name(ev.start)
            start = ev.start.strftime("%H:%M")
            end = ev.end.strftime("%H:%M")
            key = (wd, start, end, ev.location)
            counter[key] += 1
            all_intervals.append(key)
        # For each key repeated at least N times, collect intervals (ignoring location for block, but use for association)
        intervals = []
        for (wd, start, end, location), count in counter.items():
            if count >= min_occurrences:
                intervals.append((wd, start, end, location))

        if not intervals:
            print(f"  [SKIP] No blocks with >= {min_occurrences} repeats for type {event_type} in {co.courseOfferingId}")
            continue

        # Build (wd, start, end) list for block key
        block_intervals = [(wd, start, end) for (wd, start, end, location) in intervals]
        block = find_or_create_block(session, block_intervals)

        # Associate each (room, building) for each interval to this CourseOfferingBlock, and set the type
        used_room_building = set()
        for (wd, start, end, location) in intervals:
            room_id, building_id = extract_room_building(session, location)
            key = (room_id, building_id)
            if key in used_room_building:
                continue
            used_room_building.add(key)
            exists = session.query(CourseOfferingBlock).filter_by(
                courseOfferingId=co.courseOfferingId,
                blockId=block.blockId,
                roomId=room_id,
                buildingId=building_id,
                type=event_type  # <-- type column
            ).first()
            if not exists:
                link = CourseOfferingBlock(
                    courseOfferingId=co.courseOfferingId,
                    blockId=block.blockId,
                    roomId=room_id,
                    buildingId=building_id,
                    type=event_type  # <-- type column
                )
                session.add(link)
        session.commit()
        print(f"[OK] Linked {co.courseOfferingId} to block {block.blockId} (type: {event_type})")

def main():
    session = SessionLocal()
    all_courses = session.query(CourseOffering).all()
    total = len(all_courses)
    print(f"Found {total} course offerings.")
    for idx, co in enumerate(all_courses, 1):
        print(f"[{idx}/{total}] CourseOffering: {co.courseOfferingId} ({co.courseCode} {co.academicYear}{co.term})")
        process_course(session, co, min_occurrences=MIN_OCCURRENCES)
    session.close()
    print("All done.")

if __name__ == "__main__":
    main()
