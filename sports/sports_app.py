import streamlit as st
import pandas as pd
import numpy as np
from streamlit_calendar import calendar
from sports_ui import timetable_to_events
from sports_optimizer import run_gurobi_optimization, save_solution, load_solution, list_solutions
import threading
import queue
import re
from collections import defaultdict

# --- Abbreviations mapping (must match optimizer!) ---
ABBREV = {
    '1/3 A-sal-1': 'A', '1/3 A-sal-2': 'A', '1/3 A-sal-3': 'A',
    '2/3 A-sal': 'A', 'A-sal': 'A', 'B-sal': 'B',
    'Gervi fjær': 'G', 'Gervi nær': 'G', 'Aðalvöllur': 'Aðalv',
    'Æfingavöllur': 'Æfingv', 'Gervigras': 'Gervi'
}

def get_area_base(area):
    return re.sub(r"\s*\(.*\)", "", str(area)).strip()

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

required_cols = {'Æfing', 'Salur/svæði'}
if df.empty or not required_cols.issubset(df.columns):
    st.error("Google Sheet missing required columns ('Æfing', 'Salur/svæði').")
    st.stop()

# Add dummy time columns if missing
if not set(['Dagur', 'Byrjun', 'Endir']).issubset(df.columns):
    days = ['mán', 'þri', 'mið', 'fim', 'fös', 'lau', 'sun']
    df['Dagur'] = np.random.choice(days, size=len(df))
    df['Byrjun'] = np.random.choice(['08:00', '10:00', '12:00', '16:00', '18:00'], size=len(df))
    df['Endir'] = np.random.choice(['09:30', '11:30', '13:30', '17:30', '19:30'], size=len(df))

# --- 2. Sidebar Filters (pretty labels, abbreviation logic) ---
st.sidebar.header("Filters")

# Collect all area names, then map to abbreviation
all_areas = set()
for sv in df['Salur/svæði']:
    for area in str(sv).split('|'):
        area = area.strip()
        if area:
            base = get_area_base(area)  # <-- this strips (þri) etc.
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
    print("Available solution files:", files)
    if files:
        selected_file = st.selectbox("Select solution file", files)
        if st.button("Load Solution"):
            loaded_df = load_solution(selected_file)
            st.session_state['opt_result'] = loaded_df
            st.success(f"Loaded {selected_file}")
    else:
        st.info("No previous solutions saved.")


# --- 3. Editable Table (Always On Full Data) ---
st.subheader("📋 Forsendur")
edited_df = st.data_editor(
    df.reset_index(drop=True),
    use_container_width=True,
    num_rows="dynamic",
    key="editable_table"
)

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

def run_optimization_thread(full_df, q):
    try:
        result_df = run_gurobi_optimization(full_df, kill_callback=kill_callback)
        q.put(result_df)
    except Exception as e:
        q.put(e)

# --- 5. Buttons (Thread-Safe) ---
col1, col2, col3 = st.columns([2,2,2])
with col1:
    if st.button("Besta"):
        if (st.session_state['opt_thread'] is not None 
            and st.session_state['opt_thread'].is_alive()):
            st.warning("Optimization is already running! Please wait or press 'Stop Gurobi' to stop.")
        else:
            st.session_state['kill_gurobi'] = False
            st.session_state["opt_result"] = None  # clear previous result
            st.session_state['opt_queue'] = queue.Queue()  # new result queue
            thread = threading.Thread(
                target=run_optimization_thread, 
                args=(edited_df.copy(), st.session_state['opt_queue'])
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
        st.session_state["opt_result"] = run_gurobi_optimization(edited_df, kill_callback=None)
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
    display_df = edited_df

if "opt_result" in st.session_state and st.session_state["opt_result"] is not None:
    if st.button("💾 Save Solution"):
        save_path = save_solution(st.session_state["opt_result"])
        st.success(f"Solution saved to: {save_path}")

# --- 8. Filtering for Visualization (calendar/table) ---
def area_abbrev_in_room_filter(area_abbrev, abbr_filter_set):
    # area_abbrev is e.g. 'A', 'B', etc (already abbreviated by optimizer output)
    return str(area_abbrev).strip() in abbr_filter_set

filtered_display_df = display_df[
    display_df['Æfing'].isin(exercise_filter) &
    display_df['Salur/svæði'].apply(lambda sv: area_abbrev_in_room_filter(sv, set(room_filter)))
].copy()

#def area_abbrev_in_room_filter(area_str, room_filter_set):
#    # For each abbreviation in room_filter, see if any of its full names is in area_str
#    for abbr in room_filter_set:
#        for full_name in abbr_to_full[abbr]:
#            if full_name in area_str or abbr in area_str:
#                return True
#    return False

#filtered_display_df = display_df[
#    display_df['Æfing'].isin(exercise_filter) &
#    display_df['Salur/svæði'].apply(lambda sv: area_abbrev_in_room_filter(sv, set(room_filter)))
#].copy()



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
    "editable": False,
    "eventDurationEditable": False,
    "eventStartEditable": False,
    "eventResizableFromStart": False,
    "height": "auto",
}
if events:
    calendar(
        events=events,
        options=calendar_options,
        key=f"sports-calendar-{hash(tuple(room_filter))}-{hash(tuple(exercise_filter))}"
    )
    print("room_filter", room_filter)
    print("unique Salur/svæði", display_df['Salur/svæði'].unique())
    print(f"Total events displayed:", events)
else:
    st.info("No events to display with the current filters.")

# --- 10. Show Filtered Table Below Calendar ---
st.dataframe(filtered_display_df.reset_index(drop=True), use_container_width=True)
if st.button("Refresh"):
    st.rerun()
