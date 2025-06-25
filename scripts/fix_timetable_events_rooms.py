import requests
import sqlite3
from datetime import datetime, timedelta

DB_PATH = "spoi.sqlite"
API_URL = "https://localhost:8443/service/proftafla/?request=courseRooms&course={cid}"

# How close do times have to be to count as "same slot"? (seconds)
MATCH_WINDOW = 60  # 1 minute window (can make larger if needed)

def normalize_cid(cid):
    """
    Given a courseOfferingId like '71122720256', return the previous year version for the API,
    e.g., '71122720246'. Works for 11-digit codes.
    """
    cid = str(cid).zfill(11)
    year = int(cid[-5:-1])
    term = cid[-1]
    prev_year = year - 1
    return cid[:-5] + f"{prev_year}{term}"

def to_dt(s):
    """Parse timestamp string (YYYY-MM-DD HH:MM:SS) to datetime."""
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")

def main(year):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Ensure events has roomId column (TEXT, nullable)
    cursor.execute("PRAGMA table_info(events)")
    cols = [row["name"] for row in cursor.fetchall()]
    if "roomId" not in cols:
        print("[INFO] Adding roomId column to events")
        cursor.execute("ALTER TABLE events ADD COLUMN roomId TEXT")
        conn.commit()

    cursor.execute(
        "SELECT DISTINCT courseOfferingId FROM course_offerings WHERE academicYear = ?", (year,)
    )
    all_cids = [r[0] for r in cursor.fetchall()]
    print(f"[INFO] Found {len(all_cids)} course offerings for year {year}")

    n_assign = 0
    n_no_match = 0

    for cid in all_cids:
        cid_padded = normalize_cid(cid)
        print(f"\n[PROCESSING] Course Offering ID: {cid} (normalized: {cid_padded})")
        url = API_URL.format(cid=cid_padded)
        try:
            resp = requests.get(url, verify=False, timeout=8)
            data = resp.json().get("data", [])
        except Exception as e:
            print(f"[ERROR] API for {cid}: {e}")
            continue
        if not data:
            print(f"[NO API DATA] {cid} -- {url}")
            continue

        for room in data:
            room_id = room.get("room_id")
            print(f"  Room: {room.get('name')} | ID: {room.get('room_id')}, Bookings: {len(room.get('bookings', []))}")
            for booking in room.get("bookings", []):
                print(f"    {booking['from']} -- {booking['to']}")
                start = booking["from"]
                end = booking["to"]
                start_dt = to_dt(start)
                end_dt = to_dt(end)
                # Find event(s) with same course, and similar times (within window)
                query = """
                SELECT eventId, start, end, location, roomId FROM events
                WHERE courseOfferingId = ?
                """
                candidates = []
                for row in cursor.execute(query, (cid,)):
                    try:
                        event_start = to_dt(row["start"])
                        event_end = to_dt(row["end"])
                    except Exception as e:
                        print(f"[WARN] Could not parse event times: {e}")
                        continue
                    if (abs((event_start - start_dt).total_seconds()) <= MATCH_WINDOW and
                        abs((event_end - end_dt).total_seconds()) <= MATCH_WINDOW):
                        candidates.append(row)
                if candidates:
                    for event in candidates:
                        event_id = event["eventId"]
                        if event_id is None:
                            print("[ERROR] No eventId found! Keys:", list(event.keys()))
                            print("[ERROR] Event row dump:", dict(event))
                            continue

                        print(f"      -> [UPDATE] eventId={event_id}: '{event['location']}' | {event['start']}–{event['end']} | roomId={room_id}")
                        cursor.execute(
                            "UPDATE events SET roomId = ? WHERE eventId = ?",
                            (room_id, event_id)
                        )
                        n_assign += 1
                else:
                    n_no_match += 1
                    print(
                        f"[NO-MATCH] {cid}: {room['name']} {start}–{end} -- not in events"
                    )
        conn.commit()
    print(f"\n[SUMMARY]")
    print(f"Assigned roomId to {n_assign} events.")
    print(f"Bookings not matched to events: {n_no_match}")

    conn.close()

if __name__ == "__main__":
    import sys
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    year = sys.argv[1] if len(sys.argv) > 1 else "2025"
    main(year)
