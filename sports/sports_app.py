import streamlit as st
import pandas as pd
import numpy as np
from streamlit_calendar import calendar
from sports_ui import timetable_to_events, update_df_from_events
from sports_optimizer import run_gurobi_optimization, save_solution, load_solution, list_solutions
import threading
import queue
import re
from collections import defaultdict
import time
from st_aggrid import AgGrid, GridOptionsBuilder

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

st.set_page_config(page_title="Sports Timetable", layout="wide")

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

df = get_data(SHEET_URL)

if 'editable_df' not in st.session_state:
    st.session_state['editable_df'] = df.copy()

required_cols = {'Æfing', 'Salur/svæði'}
if df.empty or not required_cols.issubset(df.columns):
    st.error("Google Sheet missing required columns ('Æfing', 'Salur/svæði').")
    st.stop()

# --- 2. Sidebar Filters (pretty labels, abbreviation logic) ---
st.sidebar.header("Filters")

# Collect all area names, then map to abbreviation
editable_df = st.session_state['editable_df']  # ensure up-to-date edits

all_areas = set()
for sv in editable_df['Salur/svæði']:
    for area in str(sv).split('|'):
        area = area.strip()
        if area:
            base = get_area_base(area)  # strips (mán) etc.
            all_areas.add(base)

abbr_to_full = defaultdict(list)
for area in all_areas:
    abbr = ABBREV.get(area, area)
    abbr_to_full[abbr].append(area)

room_options = sorted(abbr_to_full.keys())
room_labels = [
    f"{abbr} ({', '.join(abbr_to_full[abbr])})" for abbr in room_options
]
room_label_to_abbr = dict(zip(room_labels, room_options))

selected_labels = st.sidebar.multiselect(
    "Veldu svæði (Select area(s))",
    room_labels,
    default=room_labels
)
room_filter = [room_label_to_abbr[l] for l in selected_labels]

exercise_options = sorted(set(df['Æfing'].unique()))
exercise_filter = st.sidebar.multiselect("Select exercise(s)", exercise_options, default=exercise_options)

with st.sidebar.expander("🔄 Load Previous Solution"):
    files = list_solutions()
    if files:
        selected_file = st.selectbox("Select solution file", files)
        if st.button("Load Solution"):
            loaded_df = load_solution(selected_file)
            st.session_state['opt_result'] = loaded_df
            force_calendar_redraw()
            st.success(f"Loaded {selected_file}")
            st.rerun()
    else:
        st.info("No previous solutions saved.")

# --- 3. Editable Table (Always On Full Data) ---
st.subheader("📋 Forsendur")
edited_df = st.data_editor(
    st.session_state['editable_df'],
    use_container_width=True,
    num_rows="dynamic",
    key="editable_table"
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
        
        # Preserve user Modified flags
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
col1, col2, col3 = st.columns([2,2,2])
with col1:
    if st.button("Besta"):
        if (st.session_state['opt_thread'] is not None 
            and st.session_state['opt_thread'].is_alive()):
            st.warning("Optimization is already running! Please wait or press 'Stop Gurobi' to stop.\n You may need to push the rfresh button below!")
        else:
            st.session_state['kill_gurobi'] = False
            st.session_state["opt_result"] = None  # clear previous result
            st.session_state['opt_queue'] = queue.Queue()  # new result queue
            thread = threading.Thread(
                target=run_optimization_thread, 
                args=(edited_df.copy(), st.session_state['opt_queue'], display_df.copy())
            )
            thread.daemon = True
            thread.start()
            st.session_state['opt_thread'] = thread
            st.session_state['opt_running'] = True
            st.info("Optimization started in the background. You can press 'Stop Gurobi' to stop.")

with col2:
    if st.button("Stop Gurobi"):
        st.session_state['kill_gurobi'] = True
        st.info("Kill signal sent. The optimizer will stop soon (if running).")

with col3:
    if st.button("Besta (non-threaded)"):
        st.session_state['kill_gurobi'] = False
        prev_soln = display_df.copy() if display_df is not None else None
        result_df = run_gurobi_optimization(edited_df.copy(), kill_callback=None, prev_soln=prev_soln)

        # Preserve modifications
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
    if st.button("💾 Save Solution"):
        save_path = save_solution(st.session_state["opt_result"])
        st.success(f"Solution saved to: {save_path}")

# --- 8. Filtering for Visualization (calendar/table) ---
def area_abbrev_in_room_filter(area_abbrev, abbr_filter_set):
    # area_abbrev is e.g. 'A', 'B', etc (already abbreviated by optimizer output)
    return str(area_abbrev).strip() in abbr_filter_set

if display_df is not None:
    filtered_display_df = display_df[
        display_df['Æfing'].isin(exercise_filter) &
        display_df['Salur/svæði'].apply(lambda sv: area_abbrev_in_room_filter(sv, set(room_filter)))
    ].copy()

# --- 9. Calendar Display (Filtered) ---
    events = timetable_to_events(filtered_display_df)
    st.subheader("📅 Æfingatafla")
    calendar_options = {
        "initialView": "timeGridWeek",
        "slotMinTime": "07:00:00",
        "slotMaxTime": "23:00:00",
        "allDaySlot": False,
        "locale": "is",
        "firstDay": 1,
        "editable": True,
        "eventDurationEditable": True,
        "eventStartEditable": True,
        "eventResizableFromStart": True,
        "snapDuration": "00:05:00",
        "height": "auto"
    }

    if events:
        calendar_ts = st.session_state.get("calendar_update_ts", 0)
        calendar_key = f"sports-calendar-{hash(tuple(room_filter))}-{hash(tuple(exercise_filter))}-{int(calendar_ts)}"
        calendar_return = calendar(
            events=events,
            options=calendar_options,
            key=calendar_key
        )
    else:
        calendar_return = None
        st.info("No events to display with the current filters.")
else:
    calendar_return = None
# Handle calendar edits (drag-and-drop)
if calendar_return and "eventChange" in calendar_return:
    changed_event = calendar_return["eventChange"]["event"]
    st.success(f"Calendar edit received for event ID: {changed_event['id']}")

    # Wrap it in a list to match update_df_from_events signature
    updated_events = [changed_event]

    display_df = update_df_from_events(display_df, updated_events)
    st.session_state["opt_result"] = display_df.copy()

    # Re-filter and regenerate events
    filtered_display_df = display_df[
        display_df['Æfing'].isin(exercise_filter) &
        display_df['Salur/svæði'].apply(lambda sv: area_abbrev_in_room_filter(sv, set(room_filter)))
    ].copy()
    events = timetable_to_events(filtered_display_df)

    # Timestamp key to force calendar redraw
    st.session_state["calendar_update_ts"] = time.time()
    st.success("Calendar changes have been applied to the timetable.")

# --- 10. Show Filtered Table Below Calendar ---
#if filtered_display_df is not None and not filtered_display_df.empty:
#    st.dataframe(filtered_display_df.reset_index(drop=True), use_container_width=True)


# Custom day order
day_order = ['mán', 'þri', 'mið', 'fim', 'fös', 'lau', 'sun']

# Preprocess and sort
if filtered_display_df is not None and not filtered_display_df.empty:
    display_df_cleaned = filtered_display_df.copy()

    # Ensure Dagur is a categorical type with order
    display_df_cleaned['Dagur'] = pd.Categorical(display_df_cleaned['Dagur'], categories=day_order, ordered=True)
    
    # Drop the last column (assumed to be EventID)
    display_df_cleaned = display_df_cleaned.iloc[:, :-1]

    # Parse Byrjun time to minutes for sorting (if needed)
    def time_to_minutes(t):
        h, m = map(int, str(t).split(":"))
        return h * 60 + m

    display_df_cleaned['StartMinutes'] = display_df_cleaned['Byrjun'].apply(time_to_minutes)

    # Sort by day and time
    display_df_cleaned = display_df_cleaned.sort_values(['Dagur', 'StartMinutes'])

    # Optionally convert booleans to icons
    bool_cols = display_df_cleaned.select_dtypes(include='bool').columns
    for col in bool_cols:
        display_df_cleaned[col] = display_df_cleaned[col].apply(lambda x: "✔️" if x else "")

    # Remove helper column
    display_df_cleaned.drop(columns=['StartMinutes'], inplace=True)

    # AgGrid setup
    gb = GridOptionsBuilder.from_dataframe(display_df_cleaned)
    gb.configure_default_column(sortable=True, filter=True, resizable=True)
    grid_options = gb.build()

    AgGrid(
        display_df_cleaned.reset_index(drop=True),
        gridOptions=grid_options,
        fit_columns_on_grid_load=True,
        theme="streamlit",
    )

if st.button("Refresh"):
    st.rerun()
