# spoi/sports/sports_ui.py

import pandas as pd
import datetime

def get_base_monday():
    today = datetime.date.today()
    base_monday = today - datetime.timedelta(days=today.weekday())  # weekday(): Monday is 0
    return base_monday

def timetable_to_events(df: pd.DataFrame) -> list:
    day_map = {"mán": 0, "þri": 1, "mið": 2, "fim": 3, "fös": 4, "lau": 5, "sun": 6}
    base_date = get_base_monday()  # Always use THIS week’s Monday

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
            "title": f"{row['Æfing']} ({area})",
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
            "backgroundColor": "#1976D2"
        }
        events.append(event)
    return events