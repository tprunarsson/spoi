'''
SELECT
    ci1.courseOfferingId   AS courseA,
    ci1.courseCode         AS codeA,
    json_extract(ci1.name, '$.is') AS nameA_is,
    ci2.courseOfferingId   AS courseB,
    ci2.courseCode         AS codeB,
    json_extract(ci2.name, '$.is') AS nameB_is
FROM
    co_taught_instances ct
    JOIN course_offerings ci1 ON ct.courseOfferingId = ci1.courseOfferingId
    JOIN course_offerings ci2 ON ct.coTaughtWithId = ci2.courseOfferingId
ORDER BY
    ci1.courseOfferingId, ci2.courseOfferingId;
'''

import sqlite3
import pandas as pd

DB_PATH = "spoi.sqlite"
MIN_OVERLAP = 4

def get_course_name_dict(conn):
    name_dict = {}
    cursor = conn.execute(
        """
        SELECT courseOfferingId,
            COALESCE(json_extract(name, '$.is'), json_extract(name, '$.en'), '') as course_name
        FROM course_offerings
        """
    )
    for row in cursor.fetchall():
        name_dict[row[0]] = row[1]
    return name_dict

def main():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # -- Ensure join table exists (safe to run every time)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS co_taught_instances (
        courseOfferingId TEXT,
        coTaughtWithId TEXT,
        PRIMARY KEY (courseOfferingId, coTaughtWithId)
    )
    """)
    conn.commit()

    query = """
    SELECT
        a.courseOfferingId AS courseA,
        b.courseOfferingId AS courseB,
        a.location,
        a.start,
        a.end,
        ca.academicYear,
        ca.term
    FROM events a
    JOIN events b
        ON a.location = b.location
        AND a.start = b.start
        AND a.end = b.end
        AND a.courseOfferingId < b.courseOfferingId
    JOIN course_offerings ca ON a.courseOfferingId = ca.courseOfferingId
    JOIN course_offerings cb ON b.courseOfferingId = cb.courseOfferingId
    WHERE
        ca.academicYear = cb.academicYear
        AND ca.term = cb.term
        AND a.location NOT LIKE '%Óákveðið%'
        AND a.location NOT LIKE '%Fjarkennsla%'
        AND a.location NOT LIKE '%Utan%'
    """

    df = pd.read_sql_query(query, conn)
    if df.empty:
        print("No overlapping events found.")
        return

    agg = (
        df.groupby(['courseA', 'courseB', 'location', 'academicYear', 'term'])
          .size()
          .reset_index(name='overlap_count')
    )
    agg = agg[agg['overlap_count'] >= MIN_OVERLAP]

    # --- [1] Update isCoTaught flag ---
    cotaught_set = set(agg['courseA']) | set(agg['courseB'])
    if cotaught_set:
        qmarks = ",".join("?" for _ in cotaught_set)
        cursor.execute(f"UPDATE course_offerings SET isCoTaught = 1 WHERE courseOfferingId IN ({qmarks})", tuple(cotaught_set))
        conn.commit()
        print(f"[DB UPDATE] Marked {len(cotaught_set)} course offerings as co-taught.")
    else:
        print("[DB UPDATE] No course offerings to mark as co-taught.")

    # --- [2] Insert all unique pairs into co_taught_instances table ---
    pair_rows = agg[['courseA', 'courseB']]
    inserted = 0
    for _, row in pair_rows.iterrows():
        a, b = row['courseA'], row['courseB']
        try:
            cursor.execute(
                "INSERT OR IGNORE INTO co_taught_instances (courseOfferingId, coTaughtWithId) VALUES (?, ?)", (a, b)
            )
            cursor.execute(
                "INSERT OR IGNORE INTO co_taught_instances (courseOfferingId, coTaughtWithId) VALUES (?, ?)", (b, a)
            )
            inserted += 2
        except Exception as e:
            print(f"[ERROR] Insert failed for {a}, {b}: {e}")
    conn.commit()
    print(f"[DB UPDATE] Inserted {inserted} co-taught pair links.")

    # Optional: print/display
    name_dict = get_course_name_dict(conn)
    agg['nameA'] = agg['courseA'].map(name_dict)
    agg['nameB'] = agg['courseB'].map(name_dict)

    if agg.empty:
        print("No co-taught course pairs with sufficient overlap.")
    else:
        print(
            f"{'courseA':<12} | {'courseB':<12} | {'cnt':<3} | {'location':<30} | {'year':<6} | {'term':<4} | {'nameA':<35} | {'nameB':<35}"
        )
        print('-' * 150)
        for _, row in agg.iterrows():
            print(
                f"{row['courseA']:<12} | {row['courseB']:<12} | {row['overlap_count']:<3} | "
                f"{row['location']:<30} | {row['academicYear']:<6} | {row['term']:<4} | "
                f"{(row['nameA'] or '')[:35]:<35} | {(row['nameB'] or '')[:35]:<35}"
            )

    conn.close()

if __name__ == "__main__":
    main()
