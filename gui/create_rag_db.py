import sqlite3
import json

examples = [
    # --- Move event ---
    {
        "intent": "move_event",
        "nl": {
            "en": "Move course {course_name} from {room_name} on {weekday_en} at {start_time_hhmm} to {room_name_new} on {weekday_en_new} at {start_time_hhmm_new}.",
            "is": "Færðu námskeiðið {course_name} úr {room_name} á {weekday_is} klukkan {start_time_hhmm} í {room_name_new} á {weekday_is_new} klukkan {start_time_hhmm_new}."
        },
        "sql": (
            "UPDATE events SET roomId='{roomId_new}', start='{start_new}', end='{end_new}' "
            "WHERE courseOfferingId='{courseOfferingId}' AND roomId='{roomId}' AND start='{start}';"
        )
    },
    # --- Change time ---
    {
        "intent": "change_time",
        "nl": {
            "en": "Change the time for {course_name} in {room_name} from {weekday_en} at {start_time_hhmm} to {weekday_en_new} at {start_time_hhmm_new}.",
            "is": "Breyttu tíma fyrir {course_name} í {room_name} úr {weekday_is} klukkan {start_time_hhmm} í {weekday_is_new} klukkan {start_time_hhmm_new}."
        },
        "sql": (
            "UPDATE events SET start='{start_new}', end='{end_new}' "
            "WHERE courseOfferingId='{courseOfferingId}' AND roomId='{roomId}' AND start='{start}';"
        )
    },
    # --- Remove/delete event ---
    {
        "intent": "remove_event",
        "nl": {
            "en": "Remove the course {course_name} in {room_name} on {weekday_en} at {start_time_hhmm}.",
            "is": "Fjarlægðu námskeiðið {course_name} í {room_name} á {weekday_is} klukkan {start_time_hhmm}."
        },
        "sql": (
            "DELETE FROM events WHERE courseOfferingId='{courseOfferingId}' "
            "AND roomId='{roomId}' AND start='{start}';"
        )
    },
    # --- Change room only ---
    {
        "intent": "change_room",
        "nl": {
            "en": "Change event for {course_name} to room {room_name_new} on {weekday_en} at {start_time_hhmm}.",
            "is": "Breyttu viðburði fyrir {course_name} í stofu {room_name_new} á {weekday_is} klukkan {start_time_hhmm}."
        },
        "sql": (
            "UPDATE events SET roomId='{roomId_new}' "
            "WHERE courseOfferingId='{courseOfferingId}' AND start='{start}';"
        )
    },
    # --- Update note ---
    {
        "intent": "update_note",
        "nl": {
            "en": "Update the note for course {course_name} in {room_name} on {weekday_en} at {start_time_hhmm} to \"{note_new}\".",
            "is": "Uppfærðu athugasemdina fyrir námskeiðið {course_name} í {room_name} á {weekday_is} klukkan {start_time_hhmm} í \"{note_new}\"."
        },
        "sql": (
            "UPDATE events SET note='{note_new}' "
            "WHERE courseOfferingId='{courseOfferingId}' AND roomId='{roomId}' AND start='{start}';"
        )
    },
    # --- Assign/remove teacher ---
    {
        "intent": "assign_teacher",
        "nl": {
            "en": "Assign {teacher_new} as the teacher for {course_name} in {room_name} on {weekday_en} at {start_time_hhmm}.",
            "is": "Úthlutaðu {teacher_new} sem kennara fyrir {course_name} í {room_name} á {weekday_is} klukkan {start_time_hhmm}."
        },
        "sql": (
            "UPDATE events SET teachers='{teacher_new}' "
            "WHERE courseOfferingId='{courseOfferingId}' AND roomId='{roomId}' AND start='{start}';"
        )
    },
    {
        "intent": "remove_teacher",
        "nl": {
            "en": "Remove {teacher_old} from {course_name} in {room_name} on {weekday_en} at {start_time_hhmm}.",
            "is": "Fjarlægðu {teacher_old} úr {course_name} í {room_name} á {weekday_is} klukkan {start_time_hhmm}."
        },
        "sql": (
            "UPDATE events SET teachers=NULL "
            "WHERE courseOfferingId='{courseOfferingId}' AND roomId='{roomId}' AND start='{start}';"
        )
    },
    # --- Assign group ---
    {
        "intent": "assign_group",
        "nl": {
            "en": "Assign group {group_new} to {course_name} in {room_name} on {weekday_en} at {start_time_hhmm}.",
            "is": "Úthlutaðu hópi {group_new} á {course_name} í {room_name} á {weekday_is} kl. {start_time_hhmm}."
        },
        "sql": (
            "UPDATE events SET group='{group_new}' "
            "WHERE courseOfferingId='{courseOfferingId}' AND roomId='{roomId}' AND start='{start}';"
        )
    },
    # --- Multi-update (room+time+note) ---
    {
        "intent": "multi_update",
        "nl": {
            "en": "Move course {course_name} from {room_name} on {weekday_en} at {start_time_hhmm} to {room_name_new} on {weekday_en_new} at {start_time_hhmm_new} and update note to \"{note_new}\".",
            "is": "Færðu námskeiðið {course_name} úr {room_name} á {weekday_is} klukkan {start_time_hhmm} í {room_name_new} á {weekday_is_new} klukkan {start_time_hhmm_new} og uppfærðu athugasemdina í \"{note_new}\"."
        },
        "sql": (
            "UPDATE events SET roomId='{roomId_new}', start='{start_new}', end='{end_new}', note='{note_new}' "
            "WHERE courseOfferingId='{courseOfferingId}' AND roomId='{roomId}' AND start='{start}';"
        )
    }
]

# --- Database creation (no changes needed) ---

conn = sqlite3.connect("rag.sql")
c = conn.cursor()

c.execute("""
CREATE TABLE IF NOT EXISTS rag_examples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    intent TEXT NOT NULL,
    nl TEXT NOT NULL,      -- JSON with 'en' and 'is'
    sql TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
)
""")

# Clear table for demo (remove in production!)
c.execute("DELETE FROM rag_examples")

for ex in examples:
    c.execute(
        "INSERT INTO rag_examples (intent, nl, sql) VALUES (?, ?, ?)",
        (ex['intent'], json.dumps(ex['nl'], ensure_ascii=False), ex['sql'])
    )

conn.commit()
conn.close()

print("rag.sql database created and populated with template-based examples!")
