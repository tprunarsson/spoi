# spoi/sports/sports_ui.py

import pandas as pd
import datetime
import re

def get_base_monday():
    today = datetime.date.today()
    base_monday = today - datetime.timedelta(days=today.weekday())
    return base_monday

def parse_iso_to_time(dt_str):
    # dt_str: '2025-07-23T18:00:00'
    try:
        t = dt_str.split("T")[1]
        h, m, *_ = t.split(":")
        return f"{int(h):02d}:{int(m):02d}"
    except Exception:
        return "00:00"

def parse_iso_to_day(dt_str):
    # dt_str: '2025-07-23T18:00:00'
    dt = datetime.datetime.fromisoformat(dt_str)
    day_map_inv = {0: "mán", 1: "þri", 2: "mið", 3: "fim", 4: "fös", 5: "lau", 6: "sun"}
    return day_map_inv[dt.weekday()]

def extract_area_from_title(title):
    # e.g. 'Æfing (A)' --> 'A'
    m = re.search(r"\((.*?)\)$", title)
    return m.group(1).strip() if m else ""

def extract_name_from_title(title):
    # e.g. 'Æfing (A)' --> 'Æfing'
    m = re.search(r"^(.*?)\(", title)
    return m.group(1).strip() if m else title.strip()

def update_df_from_events(display_df, events):
    # For each event, find matching EventID or (Æfing, Salur/svæði, Dagur), update Byrjun/Endir/Dagur if changed
    df = display_df.copy()
    for ev in events:
        # Try to find by EventID, else match by name/area/day
        title = ev.get("title", "")
        name = extract_name_from_title(title)
        area = extract_area_from_title(title)
        start = parse_iso_to_time(ev["start"])
        end = parse_iso_to_time(ev["end"])
        dagur = parse_iso_to_day(ev["start"])

        # Try EventID
        if "EventID" in df.columns and "id" in ev:
            match_idx = df.index[df["EventID"] == str(ev["id"])]
        else:
            match_idx = df.index[(df['Æfing'] == name) & (df['Salur/svæði'] == area) & (df['Dagur'] == dagur)]
        if len(match_idx) > 0:
            i = match_idx[0]
            df.at[i, "Byrjun"] = start
            df.at[i, "Endir"] = end
            df.at[i, "Dagur"] = dagur
            df.at[i, "Salur/svæði"] = area
            df.at[i, "Modified"] = True
    return df

def timetable_to_events(df: pd.DataFrame) -> list:
    day_map = {"mán": 0, "þri": 1, "mið": 2, "fim": 3, "fös": 4, "lau": 5, "sun": 6}
    base_date = get_base_monday()

    # Ensure there's a unique EventID column (index as fallback)
    if "EventID" not in df.columns:
        df = df.copy()
        df["EventID"] = df.index.astype(str)

    events = []
    for i, row in df.iterrows():
        day_idx = day_map.get(row['Dagur'], None)
        if day_idx is None:
            continue
        try:
            start_hour, start_min = map(int, str(row['Byrjun']).split(':'))
            end_hour, end_min = map(int, str(row['Endir']).split(':'))
        except Exception:
            continue
        date = base_date + datetime.timedelta(days=day_idx)
        start_dt = datetime.datetime.combine(date, datetime.time(start_hour, start_min))
        end_dt = datetime.datetime.combine(date, datetime.time(end_hour, end_min))
        area = str(row['Salur/svæði']).split('|')[0]
        event = {
            "id": str(row['EventID']),
            "title": f"{row['Æfing']} ({area})",
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
            "backgroundColor": (
                "#D32F2F" if row.get("ViolatedWindow", False)
                else "#4CAF50" if row.get('Modified', False)
                else "#1976D2"
            )
        }
        events.append(event)
    return events
