import sqlite3
import json

DB_PATH = "spoi.sqlite"

def create_join_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS event_teachers (
            eventTeacherId INTEGER PRIMARY KEY AUTOINCREMENT,
            eventId INTEGER,
            personId TEXT,
            teacherName TEXT,
            matched INTEGER
        )
    """)
    conn.commit()

def build_person_name_map(conn):
    """Build a dict of {is_name: personId} for fast matching."""
    person_map = {}
    cur = conn.execute("SELECT personId, name FROM persons WHERE name IS NOT NULL")
    for row in cur:
        try:
            name_json = json.loads(row["name"])
            is_name = name_json.get("is")
            if is_name:
                person_map[is_name.strip()] = row["personId"]
        except Exception:
            pass  # name was not JSON
    return person_map

def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    create_join_table(conn)
    person_map = build_person_name_map(conn)

    n_inserted = 0

    cur = conn.execute(
        "SELECT eventId, teachers FROM events WHERE teachers IS NOT NULL AND teachers != ''"
    )
    for row in cur:
        event_id = row["eventId"]
        teachers_raw = row["teachers"]
        teacher_names = [t.strip() for t in teachers_raw.split(',') if t.strip()]
        for name in teacher_names:
            person_id = person_map.get(name)
            conn.execute(
                """
                INSERT INTO event_teachers (eventId, personId, teacherName, matched)
                VALUES (?, ?, ?, ?)
                """,
                (event_id, person_id, name, 1 if person_id else 0)
            )
            n_inserted += 1
            print(f"[LINK] Event {event_id} | Teacher: '{name}' | PersonId: {person_id or 'NOT FOUND'}")
    conn.commit()
    print(f"\n[SUMMARY] Linked {n_inserted} teacher occurrences from events.")

    conn.close()

if __name__ == "__main__":
    main()
