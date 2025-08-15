import streamlit as st
import pandas as pd
import numpy as np
from streamlit_calendar import calendar
from sports_ui import timetable_to_events_, update_df_from_events
from sports_optimizer_gurobi import run_gurobi_optimization
from sports_optimizer_scip import run_scip_optimization, save_solution, load_solution, list_solutions
import threading
import queue
import re
import io
from collections import defaultdict
import time
from st_aggrid import AgGrid, GridOptionsBuilder
from streamlit_tags import st_tags
from pathlib import Path

# === NEW: auth imports ===
import streamlit_authenticator as stauth
import yaml
from yaml.loader import SafeLoader

# --- Abbreviations mapping (must match optimizer!) ---
ABBREV = {
    '1/3 A-sal-1': 'A', '1/3 A-sal-2': 'A', '1/3 A-sal-3': 'A',
    '2/3 A-sal': 'A', 'A-sal': 'A', 'B-sal': 'B',
    'Gervi fjær': 'G', 'Gervi nær': 'G', 'Aðalvöllur': 'Aðalv',
    'Æfingavöllur': 'Æfingv', 'Gervigras': 'Gervi'
}

def get_area_base(area):
    return re.sub(r"\s*\(.*\)", "", str(area)).strip()

def force_calendar_redraw():
    st.session_state["calendar_update_ts"] = time.time()

def col_included(col, row_dict):
    value = row_dict.get(col, "")
    if pd.isna(value) or value == "":
        return False
    if col == "Æfingarhópar" and str(value).strip() == "1":
        return False
    return True

calendar_to_df = {
    "title": "Æfing",
    "start": "Byrjun",
    "end": "Endir",
    "resourceId": "Salur/svæði",
}

def iso_to_hhmm(s):
    if not isinstance(s, str) or "T" not in s:
        return s
    try:
        t = pd.to_datetime(s)
        return t.strftime("%H:%M")
    except Exception:
        return s
    
def _get_user_solution_dir(base_dir="solutions"):
    username = st.session_state.get("username")
    if not username:
        raise ValueError("No username found in session_state. User must be logged in.")
    user_dir = Path(base_dir) / username
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir

# === IMPORTANT: set page config once, up top ===
st.set_page_config(page_title="Sports Timetable", layout="wide", page_icon="🏋️")

# =========================
# AUTH BLOCK (runs first)
# =========================
def require_login():
    # Load credentials
    try:
        with open("config.yaml") as f:
            config = yaml.load(f, Loader=SafeLoader)
    except Exception as e:
        st.error("Could not load config.yaml for authentication.")
        st.stop()

    authenticator = stauth.Authenticate(
        config["credentials"],
        config["cookie"]["name"],
        config["cookie"]["key"],
        config["cookie"]["expiry_days"],
        # auto_hash=True  # leave default off if you already store hashed passwords
    )

    # Nice header area for auth
    with st.sidebar:
        st.markdown("### 🔐 Innskráning")
        try:
            authenticator.login(location="sidebar", key="Login")
        except Exception as e:
            st.error(e)

        # Optional: show logout if logged in
        if st.session_state.get("authentication_status"):
            st.success(f"Skráður inn sem **{st.session_state.get('name')}**")
            authenticator.logout(location="sidebar", key="Logout")

    # Gate the rest of the app
    auth_status = st.session_state.get("authentication_status", None)
    if auth_status is True:
        return True
    elif auth_status is False:
        st.error("Rangt notandanafn eða lykilorð.")
        st.stop()
    else:
        st.info("Vinsamlegast skráðu þig inn í hliðarslá.")
        st.stop()

# Call it before any app logic
require_login()

# =========================
# Your existing app starts
# =========================

# --- 1. Fetch and Prepare Data ---
SHEET_URL = "https://docs.google.com/spreadsheets/d/1B91Ez1iHNW7f0AVKJwURqHhq-vQF3b4F2e-1HcDO2wM/export?format=csv&gid=0"

@st.cache_data
def get_data(url):
    try:
        df = pd.read_csv(url)
        df.columns = [col.strip() for col in df.columns]
        return df
    except Exception as e:
        st.error(f"Failed to load the Google Sheet. Error: {e}")
        return pd.DataFrame()

try:
    df = get_data(SHEET_URL)
except Exception:
    _, df = load_solution(list_solutions(_get_user_solution_dir())[0],_get_user_solution_dir())

if 'editable_df' not in st.session_state:
    st.session_state['editable_df'] = df.copy()

required_cols = {'Æfing', 'Salur/svæði'}
if df.empty or not required_cols.issubset(df.columns):
    st.error("Google Sheet missing required columns ('Æfing', 'Salur/svæði').")
    st.stop()

if 'editor_version' not in st.session_state:
    st.session_state['editor_version'] = 0


# --- 2. Sidebar Filters (pretty labels, abbreviation logic) ---
with st.sidebar.expander("🔄 Opna Solution"):
    files = list_solutions(_get_user_solution_dir())
    if files:
        selected_file = st.selectbox("Select solution file", files)
        if st.button("Load Solution"):
            loaded_df, editable_df = load_solution(selected_file,_get_user_solution_dir())
            if loaded_df is not None and "Modified" not in loaded_df.columns:
                loaded_df["Modified"] = False
            st.session_state['opt_result'] = loaded_df
            st.session_state['editable_df'] = editable_df
            st.session_state['editor_version'] += 1 
            force_calendar_redraw()
            st.success(f"Loaded {selected_file}")
            st.rerun()
    else:
        st.info("No previous solutions saved.")

editable_df = st.session_state['editable_df']  # ensure up-to-date edits

# --- Prepare resource (room) mapping ---
all_areas = set()
for sv in editable_df['Salur/svæði']:
    for area in str(sv).split('|'):
        area = area.strip()
        if area:
            base = get_area_base(area)
            all_areas.add(base)

abbr_to_full = defaultdict(list)
for area in all_areas:
    abbr = ABBREV.get(area, area)
    abbr_to_full[abbr].append(area)

room_options = sorted(abbr_to_full.keys())
room_labels = [f"{abbr} ({', '.join(abbr_to_full[abbr])})" for abbr in room_options]
room_label_to_abbr = dict(zip(room_labels, room_options))

with st.sidebar.expander("Veldu svæði"):
    select_all = st.checkbox("Select All", value=True, key="select_all_rooms")
    selected_labels = st.multiselect(
        "Choose room(s):",
        room_labels,
        default=room_labels if st.session_state.select_all_rooms else [],
        key="room_multiselect"
    )
room_filter = [room_label_to_abbr[l] for l in selected_labels]

exercise_options = sorted(set(df['Æfing'].unique()))
with st.sidebar.expander("Veldu æfingar"):
    select_all_exercises = st.checkbox("Select All", value=True, key="select_all_exercises")
    selected_exercises = st.multiselect(
        "Choose exercise(s):",
        exercise_options,
        default=exercise_options if st.session_state.select_all_exercises else [],
        key="exercise_multiselect"
    )
exercise_filter = selected_exercises

# --- Prepare resource list for calendar ---
resources = [{"id": abbr, "title": ", ".join(abbr_to_full[abbr])} for abbr in room_options]

# --- 3. Editable Table ---
st.subheader("📋 Forsendur")
edited_df = st.data_editor(
    st.session_state['editable_df'],
    use_container_width=True,
    num_rows="dynamic",
    key=f"editable_table_{st.session_state['editor_version']}"
)
st.session_state['editable_df'] = edited_df
display_df = st.session_state.get("opt_result")

# --- 4. Thread and Queue Setup ---
if 'kill_gurobi' not in st.session_state:
    st.session_state['kill_gurobi'] = False
if 'opt_thread' not in st.session_state:
    st.session_state['opt_thread'] = None
if 'opt_queue' not in st.session_state:
    st.session_state['opt_queue'] = queue.Queue()
if 'opt_running' not in st.session_state:
    st.session_state['opt_running'] = False

def kill_callback(model, where):
    if st.session_state.get('kill_gurobi', False):
        model.terminate()

def run_optimization_thread(full_df, q, prev_soln=None):
    try:
        result_df = run_gurobi_optimization(full_df, kill_callback=kill_callback, prev_soln=prev_soln)
        if prev_soln is not None and 'Modified' in prev_soln.columns:
            result_df = result_df.copy()
            result_df['Modified'] = result_df.apply(
                lambda row: prev_soln[
                    (prev_soln['Æfing'] == row['Æfing']) &
                    (prev_soln['Dagur'] == row['Dagur']) &
                    (prev_soln['Salur/svæði'] == row['Salur/svæði'])
                ]['Modified'].values[0]
                if not prev_soln[
                    (prev_soln['Æfing'] == row['Æfing']) &
                    (prev_soln['Dagur'] == row['Dagur']) &
                    (prev_soln['Salur/svæði'] == row['Salur/svæði'])
                ].empty else False,
                axis=1
            )
        q.put(result_df)
    except Exception as e:
        q.put(e)

# --- 5. Buttons (Thread-Safe) ---
col1, col2 = st.columns([2,2])

with col1:
    if st.button("Besta (Gurobi)"):
        st.session_state['kill_gurobi'] = False
        prev_soln = display_df.copy() if display_df is not None else None
        result_df = run_gurobi_optimization(edited_df.copy(), kill_callback=None, prev_soln=None)
        if prev_soln is not None and 'Modified' in prev_soln.columns:
            result_df['Modified'] = result_df.apply(
                lambda row: prev_soln[
                    (prev_soln['Æfing'] == row['Æfing']) &
                    (prev_soln['Dagur'] == row['Dagur']) &
                    (prev_soln['Salur/svæði'] == row['Salur/svæði'])
                ]['Modified'].values[0]
                if not prev_soln[
                    (prev_soln['Æfing'] == row['Æfing']) &
                    (prev_soln['Dagur'] == row['Dagur']) &
                    (prev_soln['Salur/svæði'] == row['Salur/svæði'])
                ].empty else False,
                axis=1
            )
        st.session_state["opt_result"] = result_df
        st.info("Optimization complete!")

with col2:
    if st.button("Besta (SCIP)"):
        st.session_state['kill_gurobi'] = False
        prev_soln = display_df.copy() if display_df is not None else None
        result_df = run_scip_optimization(edited_df.copy(), kill_callback=None, prev_soln=None)
        if prev_soln is not None and 'Modified' in prev_soln.columns:
            result_df['Modified'] = result_df.apply(
                lambda row: prev_soln[
                    (prev_soln['Æfing'] == row['Æfing']) &
                    (prev_soln['Dagur'] == row['Dagur']) &
                    (prev_soln['Salur/svæði'] == row['Salur/svæði'])
                ]['Modified'].values[0]
                if not prev_soln[
                    (prev_soln['Æfing'] == row['Æfing']) &
                    (prev_soln['Dagur'] == row['Dagur']) &
                    (prev_soln['Salur/svæði'] == row['Salur/svæði'])
                ].empty else False,
                axis=1
            )
        st.session_state["opt_result"] = result_df
        st.info("Optimization complete!")

# --- 6. Main Thread: Poll Queue For Result ---
if st.session_state.get('opt_thread') is not None:
    try:
        result = st.session_state['opt_queue'].get_nowait()
        if isinstance(result, pd.DataFrame):
            st.session_state["opt_result"] = result
            st.session_state['opt_running'] = False
        elif isinstance(result, Exception):
            st.session_state["opt_result"] = None
            st.session_state['opt_running'] = False
            st.error(f"Error in optimization thread: {result}")
    except queue.Empty:
        pass

if st.session_state.get('opt_running', False):
    st.info("⏳ Optimization is still running...")
else:
    st.info("Ready. Edit data or run optimization.")

# --- 7. Choose Table To Display: optimized or edited? ---
if "opt_result" in st.session_state and st.session_state["opt_result"] is not None:
    display_df = st.session_state["opt_result"]
    st.info("Showing the optimized timetable. To re-optimize, edit the table and click the button again.")
else:
    display_df = None
    filtered_display_df = None

if display_df is not None:
    if 'EventID' not in display_df.columns:
        display_df = display_df.copy()
        display_df['EventID'] = display_df.index.astype(str)
    if 'Modified' not in display_df.columns:
        display_df['Modified'] = False

if "opt_result" in st.session_state and st.session_state["opt_result"] is not None:
    if st.button("💾 Vista lausn"):
        save_path = save_solution(st.session_state["opt_result"], st.session_state['editable_df'], _get_user_solution_dir())
        st.success(f"Solution saved to: {save_path}")

# --- 8. Filtering for Visualization (calendar/table) ---
def area_abbrev_in_room_filter(area_abbrev, abbr_filter_set):
    return str(area_abbrev).strip() in abbr_filter_set

if display_df is not None:
    filtered_display_df = display_df[
        display_df['Æfing'].isin(exercise_filter) &
        display_df['Salur/svæði'].apply(lambda sv: area_abbrev_in_room_filter(sv, set(room_filter)))
    ].copy()

# --- 9. Calendar Display (Filtered) ---
    events = timetable_to_events_(filtered_display_df)

    st.subheader("📅 Æfingatafla")
    view = st.radio("Veldu sýn:", ["Week", "Resource"], horizontal=True)
    if view == "Week":
        calendar_options = {
            "initialView": "timeGridWeek",
            "slotMinTime": "07:00:00",
            "slotMaxTime": "23:00:00",
            "allDaySlot": False,
            "locale": "is",
            "firstDay": 1,
            "editable": True,
            "selectable": True,
            "eventDurationEditable": True,
            "eventStartEditable": True,
            "eventResizableFromStart": True,
            "snapDuration": "00:05:00",
            "height": "auto"
        }
        calendar_key = f"sports-calendar-week-{hash(tuple(room_filter))}-{hash(tuple(exercise_filter))}-{int(st.session_state.get('calendar_update_ts',0))}"
    else:
        calendar_options = {
            "initialView": "resourceTimeGridDay",
            "resources": resources,
            "schedulerLicenseKey": "CC-Attribution-NonCommercial-NoDerivatives",
            "slotMinTime": "07:00:00",
            "slotMaxTime": "23:00:00",
            "allDaySlot": False,
            "locale": "is",
            "firstDay": 1,
            "editable": True,
            "selectable": True,
            "eventDurationEditable": True,
            "eventStartEditable": True,
            "eventResizableFromStart": True,
            "snapDuration": "00:05:00",
            "height": "auto"
        }
        calendar_key = f"sports-calendar-resource-{hash(tuple(room_filter))}-{hash(tuple(exercise_filter))}-{int(st.session_state.get('calendar_update_ts',0))}"

    if events:
        calendar_return = calendar(
            events=events,
            options=calendar_options,
            key=calendar_key,
            callbacks=["eventChange", "eventMouseEnter"],
        )
    else:
        calendar_return = None
        st.info("No events to display with the current filters.")
else:
    calendar_return = None

if calendar_return and "eventMouseEnter" in calendar_return:
    event = calendar_return["eventMouseEnter"]["event"]
    df = st.session_state["editable_df"]
    base_name = re.sub(r"\s*\(.*\)$", "", event.get("title", "")).strip()
    row = df[df["Æfing"] == base_name]
    if not row.empty:
        row_dict = row.iloc[0].to_dict()
        columns_to_show = [
            "Æfing", "Æfingarhópar", "fyrir/undan", "Salur/svæði",
            "sun", "mán", "þri", "mið", "fim", "fös", "lau",
            "Lengd", "LengdHelgar"
        ]
        summary = " ".join(f"{col}: {row_dict.get(col, '')}" for col in columns_to_show if col_included(col, row_dict))
    else:
        summary = f"Æfing: {event.get('title')}, engar forsendur!"
    st.toast(summary, icon="💡")

if calendar_return and "eventChange" in calendar_return:
    changed_event = calendar_return["eventChange"]["event"]
    st.write("Changed event object from calendar:", changed_event)

    event_id = changed_event.get("id")
    before_row = display_df[display_df["EventID"] == event_id]
    print("Before update:", before_row.to_string(index=False))

    if event_id is not None:
        old_row = display_df[display_df["EventID"] == str(event_id)]
        if not old_row.empty:
            old_event = old_row.iloc[0].to_dict()
            changes = []
            for cal_key, df_key in calendar_to_df.items():
                cal_val = changed_event.get(cal_key)
                df_val = old_event.get(df_key)
                if cal_key in ("start", "end"):
                    cal_val = iso_to_hhmm(cal_val)
                if cal_key == "resourceId" and (cal_val is None or str(cal_val).lower() in ("", "none", "nan")):
                    continue
                if str(cal_val) != str(df_val):
                    changes.append(f"**{df_key}**: '{df_val}' → '{cal_val}'")
            if changes:
                print("**Changed fields:**\n" + "\n".join(changes))
            else:
                print("No fields changed (possible drag to same position?)")
        else:
            print("Warning: Old event could not be found in DataFrame (maybe just added?)")
    else:
        print("Warning:No 'id' found in changed event!")

    changed_event = calendar_return["eventChange"]["event"]
    st.success(f"Calendar edit received for event ID: {changed_event['id']}")
    updated_events = [changed_event]
    display_df = update_df_from_events(display_df, updated_events)
    if event_id is not None and "Modified" in display_df.columns:
        display_df.loc[display_df["EventID"] == str(event_id), "Modified"] = True

    after_row = display_df[display_df["EventID"] == event_id]
    print("After update:", after_row.to_string(index=False))

    st.session_state["opt_result"] = display_df.copy()
    filtered_display_df = display_df[
        display_df['Æfing'].isin(exercise_filter) &
        display_df['Salur/svæði'].apply(lambda sv: area_abbrev_in_room_filter(sv, set(room_filter)))
    ].copy()
    events = timetable_to_events_(filtered_display_df)
    st.session_state["calendar_update_ts"] = time.time()
    st.success("Calendar changes have been applied to the timetable.")

# --- 10. Show Filtered Table Below Calendar ---
st.subheader("📊 Niðurstöður")

day_order = ['mán', 'þri', 'mið', 'fim', 'fös', 'lau', 'sun']
if filtered_display_df is not None and not filtered_display_df.empty:
    display_df_cleaned = filtered_display_df.copy()
    display_df_cleaned['Dagur'] = pd.Categorical(display_df_cleaned['Dagur'], categories=day_order, ordered=True)
    display_df_cleaned = display_df_cleaned.iloc[:, :-1]
    def time_to_minutes(t):
        h, m = map(int, str(t).split(":"))
        return h * 60 + m
    display_df_cleaned['StartMinutes'] = display_df_cleaned['Byrjun'].apply(time_to_minutes)
    display_df_cleaned = display_df_cleaned.sort_values(['Dagur', 'StartMinutes'])
    bool_cols = display_df_cleaned.select_dtypes(include='bool').columns
    for col in bool_cols:
        display_df_cleaned[col] = display_df_cleaned[col].apply(lambda x: "✔️" if x else "")
    display_df_cleaned.drop(columns=['StartMinutes'], inplace=True)
    gb = GridOptionsBuilder.from_dataframe(display_df_cleaned)
    gb.configure_default_column(sortable=True, filter=True, resizable=True)
    gb.configure_grid_options(enableExporting=True)
    grid_options = gb.build()
    response = AgGrid(
        display_df_cleaned.reset_index(drop=True),
        gridOptions=grid_options,
        fit_columns_on_grid_load=True,
        theme="streamlit",
        return_mode='AS_INPUT',
        update_mode='MODEL_CHANGED',
    )
    aggrid_df = pd.DataFrame(response['data'])

    excel_buf = io.BytesIO()
    aggrid_df.to_excel(excel_buf, index=False)
    excel_buf.seek(0)

    st.download_button(
        label="⬇️ Sækja töflu fyrir Excel",
        data=excel_buf,
        file_name="timetable_filtered.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

if st.button("Refresh"):
    st.rerun()
