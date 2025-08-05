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

def update_df_from_events(display_df, updated_events):
    import pandas as pd
    display_df = display_df.copy()
    for ev in updated_events:
        event_id = str(ev.get("id"))
        mask = display_df["EventID"].astype(str) == event_id
        if not mask.any():
            continue  # No matching row

        # Fetch old values
        row = display_df.loc[mask].iloc[0]
        old_dict = row.to_dict()

        # Update fields if present, else keep old values
        new_start = pd.to_datetime(ev["start"]).strftime("%H:%M") if "start" in ev else row["Byrjun"]
        new_end = pd.to_datetime(ev["end"]).strftime("%H:%M") if "end" in ev else row["Endir"]
        # Handle area update
        new_area = ev.get("resourceId") or ev.get("Salur/svæði") or row["Salur/svæði"]

        # Prepare changes
        changes = {}
        if row["Byrjun"] != new_start:
            changes["Byrjun"] = new_start
        if row["Endir"] != new_end:
            changes["Endir"] = new_end
        if row["Salur/svæði"] != new_area:
            changes["Salur/svæði"] = new_area

        # Only set Modified if something changed
        if changes:
            changes["Modified"] = True

        for col, val in changes.items():
            display_df.loc[mask, col] = val

        # (Optional) print for debugging
        print("Old event dict from DataFrame:", old_dict)
        print("New (changed) event dict from calendar:", ev)
        if changes:
            print("**Changed fields:**")
            for col in changes:
                if col == "Modified": continue
                print(f"**{col}**: '{row[col]}' → '{changes[col]}'")
        else:
            print("No fields changed (possible drag to same position?)")

        # (Optional) print new row for verification
        new_row = display_df.loc[mask].iloc[0].to_dict()
        print("After update:", new_row)

    return display_df


def update_df_from_events_(display_df, events):
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


ABBREV = {
    '1/3 A-sal-1': 'A', '1/3 A-sal-2': 'A', '1/3 A-sal-3': 'A',
    '2/3 A-sal': 'A', 'A-sal': 'A', 'B-sal': 'B',
    'Gervi fjær': 'G', 'Gervi nær': 'G', 'Aðalvöllur': 'Aðalv',
    'Æfingavöllur': 'Æfingv', 'Gervigras': 'Gervi'
}
import datetime

DAY_MAP = {
    "mán": 0, "þri": 1, "mið": 2, "fim": 3, "fös": 4, "lau": 5, "sun": 6
}
def get_base_monday():
    today = datetime.date.today()
    return today - datetime.timedelta(days=today.weekday())

def timetable_to_events_(df):
    events = []
    for i, row in df.iterrows():
        day_name = row["Dagur"]
        day_idx = DAY_MAP.get(day_name, 0)

        event_date = get_base_monday() + datetime.timedelta(days=day_idx)

        start_time = str(row["Byrjun"]).strip()
        end_time = str(row["Endir"]).strip()
        start_dt = datetime.datetime.strptime(f"{event_date} {start_time}", "%Y-%m-%d %H:%M")
        end_dt = datetime.datetime.strptime(f"{event_date} {end_time}", "%Y-%m-%d %H:%M")

        area_full = str(row['Salur/svæði']).strip()
        area_abbr = ABBREV.get(area_full, area_full)

        event = {
            "id": str(row.get("EventID", i)),
            "title": row["Æfing"],
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
            "resourceId": area_abbr,
            "backgroundColor": (
                "#D32F2F" if row.get("ViolatedWindow", False) is True
                else "#4CAF50" if row.get('Modified', False) is True
                else "#1976D2"
            )
        }
        events.append(event)
    return events

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
