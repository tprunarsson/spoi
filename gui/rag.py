import sqlite3
from sqlalchemy import text
import json
from datetime import datetime
import requests
import numpy as np
from sentence_transformers import SentenceTransformer
import re
import pandas as pd

# Path to your RAG SQLite database
RAG_DB_PATH = "rag.sql"
OLLAMA_MODEL = "llama3"
OLLAMA_API_URL = "http://localhost:11434/api/generate"
EMBEDDING_MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"

def query_ollama(prompt, temperature=0.0, model=OLLAMA_MODEL, url=OLLAMA_API_URL):
    response = requests.post(
        url,
        json={"model": model, "prompt": prompt, "temperature": temperature, "stream": False}
    )
    try:
        return response.json()["response"]
    except Exception:
        return response.text

# ---- Embedding model ----
_embed_model = None
def get_embed_model():
    global _embed_model
    if _embed_model is None:
        _embed_model = SentenceTransformer(EMBEDDING_MODEL_NAME)
    return _embed_model

def profile_sqlalchemy_row(row_dict, table_name="events"):
    """
    Display a subset of keys, but show the mapped (DB) name as the key in output.
    Use the app key to look up the value, display the mapped key name.
    """
    key_map = {
        "start": "start",
        "end": "end",
        "courseOfferingId": "courseOfferingId",
        "roomId": "roomId",
        "type": "type",
        "group": "group",
        "note": "note",
        "teachers": "teachers",
        "course_code": "course_code",
        "course_name": "course_name",
        "room_name": "room_name",
    }
    keys_of_interest = [
        "courseOfferingId", "roomId", "type", "group", "note", "teachers",
        "course_code", "course_name", "room_name", "start", "end"
    ]
    lines = [f"Table: {table_name}\nColumns and current values:"]
    for app_key in keys_of_interest:
        if app_key in row_dict:
            db_key = key_map.get(app_key, app_key)
            val = row_dict[app_key]
            # Format start/end without seconds if needed
            if app_key in ["start", "end"]:
                try:
                    dt = pd.to_datetime(val)
                    val = dt.strftime('%Y-%m-%d %H:%M')
                except Exception:
                    pass
            lines.append(f"- {db_key}: {val}")
    return "\n".join(lines)

# ---- Load examples and cache embeddings ----
_cached_examples = None
def load_rag_examples_with_embeddings(language="en"):
    """
    Loads all NL-to-SQL examples for the specified language,
    attaches embeddings to each, returns list of dicts.
    """
    global _cached_examples
    # Optional: separate cache per language
    if _cached_examples is not None and _cached_examples.get(language):
        return _cached_examples[language]
    conn = sqlite3.connect(RAG_DB_PATH)
    c = conn.cursor()
    c.execute("SELECT intent, nl, sql FROM rag_examples")
    rows = c.fetchall()
    examples = []
    for intent, nl_json, sql in rows:
        try:
            nl_dict = json.loads(nl_json)
            if language in nl_dict:
                examples.append({'intent': intent, 'nl': nl_dict[language], 'sql': sql, 'lang': language})
        except Exception:
            pass
    conn.close()
    # Compute embeddings
    embed_model = get_embed_model()
    nls = [ex['nl'] for ex in examples]
    embeddings = embed_model.encode(nls)
    for ex, emb in zip(examples, embeddings):
        ex['embedding'] = emb
    # Cache
    if _cached_examples is None:
        _cached_examples = {}
    _cached_examples[language] = examples
    return examples

def load_rag_examples_with_embeddings_():
    """
    Loads all NL-to-SQL examples from rag.sql (both languages),
    attaches embeddings to each (for fast search).
    Returns a list of dicts: {intent, nl, sql, embedding}
    """
    global _cached_examples
    if _cached_examples is not None:
        return _cached_examples
    conn = sqlite3.connect(RAG_DB_PATH)
    c = conn.cursor()
    c.execute("SELECT intent, nl, sql FROM rag_examples")
    rows = c.fetchall()
    examples = []
    # Load both IS and EN for every row
    for intent, nl_json, sql in rows:
        try:
            nl_dict = json.loads(nl_json)
            for lang in nl_dict:
                examples.append({'intent': intent, 'nl': nl_dict[lang], 'sql': sql, 'lang': lang})
        except Exception:
            # Fallback: store as-is in "unknown" language
            examples.append({'intent': intent, 'nl': nl_json, 'sql': sql, 'lang': 'unknown'})
    conn.close()
    # Compute and attach embeddings
    embed_model = get_embed_model()
    nls = [ex['nl'] for ex in examples]
    embeddings = embed_model.encode(nls)
    for ex, emb in zip(examples, embeddings):
        ex['embedding'] = emb
    _cached_examples = examples
    return examples

# ---- Find best match using embeddings ----
def clean_language_code(text):
    # Lowercase the response and remove any unwanted characters except i, s, e, n
    code = re.sub(r'[^isen]', '', text.lower())
    # Only accept "is" or "en", fallback to "en"
    if code == "is":
        return "is"
    elif code == "en":
        return "en"
    else:
        return "en"
def detect_language_llama(text):
    """
    Ask Llama to detect the language: returns 'en' or 'is' only.
    """
    prompt = (
        f'Which language is this: """{text}"""\n'
        '*Important: Just return two characters: "en" for English or "is" for Icelandic.'
    )
    response = query_ollama(prompt)
    try:
        reply = response.strip()
        reply = clean_language_code(reply)
        # Defensive: only accept "en" or "is"
        if reply.lower().startswith('en'):
            return "en"
        if reply.lower().startswith('is'):
            return "is"
        # fallback: guess English
        print(f"Warning: detected language as '{reply}', defaulting to 'en'.")
        return "en"
    except Exception:
        print(f"Warning: detected language as '{reply}', defaulting to 'en'.")
        return "en"

def find_best_example(user_instruction):
    """
    Detect language, then find best-matching example in the same language using embeddings.
    """
    detected_language = detect_language_llama(user_instruction)
    examples = load_rag_examples_with_embeddings(language=detected_language)
    embed_model = get_embed_model()
    user_emb = embed_model.encode([user_instruction])[0]
    sims = np.array([
        np.dot(user_emb, ex['embedding']) / (np.linalg.norm(user_emb) * np.linalg.norm(ex['embedding']))
        for ex in examples
    ])
    best_idx = int(np.argmax(sims))
    return examples[best_idx], detected_language


def format_example_for_prompt_(example):
    return f"{example['nl']}\nSQL: {example['sql']}"

def format_example_for_prompt(example, event_props):
    """
    Fill the example NL and SQL with values from the current event.
    event_props: dictionary with all relevant event fields
    """
    
    def get_weekday(dt):
        return pd.to_datetime(dt).strftime("%A")

    def get_time(dt):
        return pd.to_datetime(dt).strftime("%H:%M")

    def get_datetime_str(dt):
        return pd.to_datetime(dt).strftime("%Y-%m-%d %H:%M")


    # Parse event start and end
    dt_start = pd.to_datetime(event_props["start"])
    dt_end = pd.to_datetime(event_props["end"])

    # Add all relevant replacements
    filled = dict(event_props)
    filled.update({
        "weekday_en": get_weekday(dt_start),
        "start_time_hhmm": get_time(dt_start),
        "end_time_hhmm": get_time(dt_end),
        "start": get_datetime_str(dt_start),
        "end": get_datetime_str(dt_end),
    })

    # Placeholders for "new" values (if your template uses them)
    filled.setdefault("room_name_new", "[NEW_ROOM]")
    filled.setdefault("weekday_en_new", "[NEW_DAY]")
    filled.setdefault("start_time_hhmm_new", "[NEW_TIME]")
    filled.setdefault("start_new", "[NEW_START_DATETIME]")
    filled.setdefault("end_new", "[NEW_END_DATETIME]")
    filled.setdefault("roomId_new", "[NEW_ROOM_ID]")

    try:
        nl_filled = example['nl'].format(**filled)
        sql_filled = example['sql'].format(**filled)
    except Exception as e:
        print("Warning: Error formatting example:", e)
        nl_filled = example['nl']
        sql_filled = example['sql']

    return f"{nl_filled}\nSQL: {sql_filled}"


def format_event_time(start_iso):
    try:
        dt = datetime.fromisoformat(start_iso)
        return dt.strftime("%A %H:%M")
    except Exception:
        return start_iso

def generate_suggested_instruction(props, event_data, language='is'):
    course_code = props.get('course_code', '')
    course_name = props.get('course_name', '')
    room = props.get('room', '')
    start_iso = event_data.get('start', '')
    readable_time = format_event_time(start_iso)
    if language == 'is':
        return (f"Færðu námskeiðið {course_code} ({course_name}) "
                f"úr {room} á {readable_time} í [NÝR_STOFU] á [NÝR_DEGI] kl [NÝR_TÍMI].")
    else:
        return (f"Move the course {course_code} ({course_name}) "
                f"from {room} on {readable_time} to [NEW_ROOM] on [NEW_DAY] at [NEW_TIME].")

def get_weekday_date_lines(event_start_iso, language="en"):
    WeekDays = {
        "is": {
            "Monday": "Mánudagur", "Tuesday": "Þriðjudagur", "Wednesday": "Miðvikudagur",
            "Thursday": "Fimmtudagur", "Friday": "Föstudagur", "Saturday": "Laugardagur", "Sunday": "Sunnudagur",
        }
    }
    dt = pd.to_datetime(event_start_iso)
    week_start = dt - pd.Timedelta(days=dt.weekday())  # Monday of this week
    # Always use time and microseconds from the event!
    event_time = dt.strftime('%H:%M')
    lines = []
    for i in range(7):
        day = week_start + pd.Timedelta(days=i)
        eng_name = day.strftime('%A')
        name = WeekDays["is"][eng_name] if language == "is" else eng_name
        lines.append(f"- {name}: {day.strftime('%Y-%m-%d')} {event_time}")
    return lines


ICELANDIC_WEEKDAY_HEADER = """Í þessari viku samsvara dagarnir eftirfarandi dagsetningum:"""
ENGLISH_WEEKDAY_HEADER = """In this week, the days correspond to these dates:"""
# Map: prop_name -> (schema_name, label_en, label_is)
EVENT_FIELD_MAP = [
    ("course_offering_id", "courseOfferingId", "Course offering", "Námskeiðsútgáfa"),
    ("course_code",        "courseCode",       "Course code",     "Námskeiðskóði"),
    ("course_name",        "courseName",       "Course name",     "Heiti námskeiðs"),
    ("room_id",            "roomId",           "Room",            "Kennslustofa"),
    ("event_type",         "type",             "Event type",      "Tegund viðburðar"),
    ("start",              "start",            "Start",           "Upphaf"),
    ("end",                "end",              "End",             "Lok"),
    ("location",           "location",         "Location",        "Staðsetning"),
    ("note",               "note",             "Note",            "Athugasemd"),
    ("teachers",           "teachers",         "Teachers",        "Kennarar"),
    ("group",              "group",            "Group",           "Hópur"),
]


PROMPT_TEMPLATES = {
    "is": """
Þú ert aðstoðarmaður við gerð stundaskráa í háskóla. Þú hjálpar til við að uppfæra tíma og kennslustofur í gagnagrunninum með því að skrifa SQL skipanir (UPDATE eða DELETE eftir þörfum) samkvæmt gagnalíkaninu og dæminu hér að neðan.

**Nota má eingöngu töfluna `events` fyrir þessar skipanir.**

Hér er dæmi um notendaskipun sem þarf að þýða yfir í SQL kóða:
{example_txt}

{calendar_context}

Gagnalíkan:
{data_profile}

**Áríðandi leiðbeiningar:**
- Notaðu alltaf nákvæmt dagsetningar- og tímabilform: `'YYYY-MM-DDTHH:MM'`.
- Þegar þú framkvæmir UPDATE eða DELETE, verður alltaf að tilgreina bæði `courseOfferingId` og upprunalegt `start` í WHERE-skilyrðinu.
- Ekki má breyta eða eyða gildum í öðrum dálkum en þeim sem eru sýndir í gagnalíkaninu.
- Ef dálkur er tómur eða ekki tilgreindur, á ekki að breyta honum.
- Svara skal AÐEINS með SQL skipuninni, engum viðbótarútskýringum eða texta.

Hér kemur það sem ég þarf að fá frá þér, notendaskipun er:

**{user_instruction}**

Skrifaðu AÐEINS þá SQL skipun sem breytir eða fjarlægir þennan viðburð úr gagnagrunninum.
""",

    "en": """
You are a university timetabling assistant. You help update event times and rooms in the database by writing SQL statements (UPDATE or DELETE as needed), using the provided schema and the example below.

**You must only use the `events` table for these statements.**

Here is an example user instruction to translate into SQL code:
{example_txt}

{calendar_context}

Schema:
{data_profile}

**Important instructions:**
- Use the exact datetime format as in the database: `'YYYY-MM-DD HH:MM'` (note the SPACE between date and time).
- When performing UPDATE or DELETE, always specify BOTH `courseOfferingId` and the original `start` in the WHERE clause.
- Only modify columns explicitly shown in the schema below.
- If a column is empty or not specified, do not change it.
- Respond ONLY with the valid SQL command, no explanations or extra text.

Here is what I need from you:
{user_instruction}

Write ONLY the SQL statement that will modify or remove this event in the database.
"""
}


def generate_event_details_block(props, language="is"):
    """
    Given props dict from the calendar, outputs a block of event details lines,
    showing both DB field name and language-specific label.
    """
    lines = []
    for prop, schema, label_en, label_is in EVENT_FIELD_MAP:
        val = props.get(prop, "")
        if val not in (None, ""):
            label = label_is if language == "is" else label_en
            lines.append(f"- {schema} ({label}): {val}")
    return "\n".join(lines)


def generate_event_prompt(event_data, user_instruction, data_profile, example, language='is'):
    props = event_data.get('extendedProps', {})
    course_offering_id = props.get('course_offering_id', '')
    course_code = props.get('course_code', '')
    course_name = props.get('course_name', '')
    room_id = props.get('room_id', '')
    event_type = props.get('event_type', '')
    start = event_data.get('start', '')
    end = event_data.get('end', '')
    start_str = format_event_time(start)
    example_txt = format_example_for_prompt(example, props)
    weekday_lines = get_weekday_date_lines(start, language=language)

    if language == "is":
        calendar_context = (
            ICELANDIC_WEEKDAY_HEADER + "\n"
            + "\n".join(weekday_lines) +
            "\nEf beðið er um að færa viðburð á ákveðinn dag, notaðu viðeigandi dagsetningu héðan að ofan.\n"
        )
    else:
        calendar_context = (
            ENGLISH_WEEKDAY_HEADER + "\n"
            + "\n".join(weekday_lines) +
            "\nIf you are asked to move an event to a particular day, use the corresponding date above.\n"
        )
    event_details_block = generate_event_details_block(props, language=language)
  
    prompt = PROMPT_TEMPLATES[language].format(
        calendar_context=calendar_context,
        course_offering_id=course_offering_id,
        course_code=course_code,
        course_name=course_name,
        room=room_id,
        event_details_block=event_details_block,
        event_type=event_type,
        start=start,
        start_str=start_str,
        end=end,
        data_profile=data_profile,
        example_txt=example_txt,
        user_instruction=user_instruction,
    )
    return prompt


def generate_event_prompt__(event_data, user_instruction, data_profile, example, language='is'):
    props = event_data.get('extendedProps', {})
    course_offering_id = props.get('course_offering_id', '')
    course_code = props.get('course_code', '')
    course_name = props.get('course_name', '')
    room = props.get('room', '')
    event_type = props.get('event_type', '')
    start = event_data.get('start', '')
    end = event_data.get('end', '')
    start_str = format_event_time(start)
    example_txt = format_example_for_prompt(example, props)

    weekday_lines = get_weekday_date_lines(start, language=language)
    calendar_context = (
        "In this week, the days correspond to these dates:\n"
        + "\n".join(weekday_lines) +
        "\nIf you are asked to move an event to a particular day, use the corresponding date above.\n"
    )

    prompt = f"""
You are a university timetabling assistant. You help update event times and rooms in the database by writing SQL statements (UPDATE or DELETE as needed), using the provided schema and an example.

{calendar_context}

Event details:
- Course offering: {course_offering_id}
- Course code: {course_code}
- Course name: {course_name}
- Current room: {room}
- Event type: {event_type}
- Start: {start} ({start_str})
- End: {end}

Schema:
{data_profile}

Example NL-to-SQL pair:
{example_txt}

User instruction:
{user_instruction}

Write ONLY the SQL statement that will modify or remove this event in the database.


**Important: Don't change anything else unless requested.**
"""
    return prompt


def smoke_test_sqlalchemy(sql, session):
    try:
        session.execute(text(sql))
        session.rollback()
        return True, "Query executed successfully."
    except Exception as e:
        session.rollback()
        return False, str(e)
    

def extract_sql_from_llm_response(llm_output):
    """
    Extract only the actual SQL from a typical LLM output,
    handling code fences, prefix/suffix text, and trailing explanations.
    Returns only the first SQL statement (ending at the first semicolon).
    """
    # Remove leading/trailing whitespace
    text = llm_output.strip()

    # If triple-backtick code block, extract inside
    codeblock = re.search(r"```(?:sql)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
    if codeblock:
        text = codeblock.group(1).strip()

    # Find the first line with a SQL keyword
    lines = text.splitlines()
    sql_start_idx = None
    for idx, line in enumerate(lines):
        if re.match(r"^(DELETE|UPDATE|INSERT|SELECT)\b", line.strip(), re.IGNORECASE):
            sql_start_idx = idx
            break

    if sql_start_idx is not None:
        # Join all lines from the SQL start onwards
        sql_lines = lines[sql_start_idx:]
        # Combine, and extract up to the first semicolon (inclusive)
        sql_text = "\n".join(sql_lines)
        sql_statement_match = re.search(r"^(.*?;)", sql_text, re.DOTALL)
        if sql_statement_match:
            return sql_statement_match.group(1).strip()
        else:
            # If no semicolon, return up to end
            return sql_text.strip()
    else:
        # Fallback: extract the first SQL-like statement up to the first semicolon
        sql_statement_match = re.search(r"(DELETE|UPDATE|INSERT|SELECT)[^;]+;", text, re.IGNORECASE | re.DOTALL)
        if sql_statement_match:
            return sql_statement_match.group(0).strip()
        return text  # Last fallback: raw text

# Example usage:
# cleaned_sql = extract_sql_from_llm_response(llm_output)
