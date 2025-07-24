# rag.py
from dotenv import load_dotenv
import sqlite3
from sqlalchemy import text
import json
from datetime import datetime
import requests
import numpy as np
from sentence_transformers import SentenceTransformer
import re
import pandas as pd
import os
from openai import OpenAI

# Load environment variables from .env file
load_dotenv()
OPENAI_API_KEY = os.getenv("openai_api_key")

# Create the OpenAI client (new API)
client = OpenAI(api_key=OPENAI_API_KEY)

# Path to your RAG SQLite database
RAG_DB_PATH = "rag.sql"
OLLAMA_MODEL = "llama3"
OLLAMA_API_URL = "http://localhost:11434/api/generate"
EMBEDDING_MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"

# Map function names to your real context functions
CONTEXT_FUNCTIONS = {
    "get_weekday_date_lines": lambda props, chat: (
        "Weekday mapping for this week:\n" +
        "\n".join(get_weekday_date_lines(
            props.get("start"), lang=chat.get("language", "en")
        ))
    ),
    # Add other context function mappings as needed
    # "get_teacher_roster": lambda props, chat: ...
}

def get_languages_from_rag(db_path="rag.sql"):
    """
    Returns a dict of language codes found in the nl field of rag_examples.
    Example: {'en': 'English', 'is': 'Icelandic'}
    """
    code_to_label = {'en': 'English', 'is': 'Icelandic'}
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("SELECT nl FROM rag_examples")
    langs = set()
    for (nl_json,) in c.fetchall():
        try:
            d = json.loads(nl_json)
            if isinstance(d, dict):
                langs.update(d.keys())
        except Exception:
            pass
    conn.close()
    # Fallback to code itself if not in label map
    return {code: code_to_label.get(code, code.capitalize()) for code in sorted(langs)}

def get_statuses_from_rag(db_path="rag.sql"):
    """
    Returns a sorted list of unique statuses in rag_examples.
    """
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    try:
        c.execute("SELECT DISTINCT status FROM rag_examples")
        statuses = [row[0] for row in c.fetchall() if row[0] is not None]
    except Exception:
        statuses = []
    conn.close()
    return sorted(statuses)

# --- LLM/Embedding Helper ---
_embed_model = None
def get_embed_model():
    global _embed_model
    if _embed_model is None:
        _embed_model = SentenceTransformer(EMBEDDING_MODEL_NAME)
    return _embed_model

def query_ollama(prompt, temperature=0.0, model=OLLAMA_MODEL, url=OLLAMA_API_URL):
    """Query Llama or Ollama API for completion."""
    print(f"Querying LLM with prompt: {prompt}")  # Log first 100 chars
    response = requests.post(
        url,
        json={"model": model, "prompt": prompt, "temperature": temperature, "stream": False}
    )
    try:
        return response.json()["response"]
    except Exception:
        return response.text

def query_openai(prompt, temperature=0.0, model="gpt-4.1-mini"):
    """Query OpenAI ChatGPT for completions using the new API."""
    print(f"Querying OpenAI with prompt: {prompt}")  # Show only first 100 chars for logging
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
    )
    return response.choices[0].message.content

# --- Main RAG Functions ---

def serialize_complex_types(d):
    # Returns a new dict with lists/dicts converted to JSON strings
    out = {}
    for k, v in d.items():
        if isinstance(v, (list, dict)):
            out[k] = json.dumps(v, ensure_ascii=False)
        else:
            out[k] = v
    return out


def build_dynamic_context(best_example, props, chat):
    """
    Calls all functions listed in required_context_functions for the RAG example.
    Returns a combined string to include in the LLM prompt.
    """
    context_blocks = []
    required_fns = best_example.get("required_context_functions", [])
    # If loaded from DB, may be JSON string:
    if isinstance(required_fns, str):
        try:
            required_fns = json.loads(required_fns)
        except Exception:
            required_fns = [required_fns] if required_fns else []
    for fn_name in required_fns:
        fn = CONTEXT_FUNCTIONS.get(fn_name)
        if fn:
            try:
                context_blocks.append(fn(props, chat))
            except Exception as e:
                context_blocks.append(f"[Context function '{fn_name}' failed: {e}]")
    return "\n\n".join(context_blocks)


def load_rag_examples_from_json(json_path, cache=True):
    """
    Loads all RAG examples from a JSON file, for all languages found in 'nl'.
    Returns a dict: {lang_code: [examples_in_that_lang, ...], ...}
    """
    global _cached_examples
    cache_key = f"json::{os.path.abspath(json_path)}::ALL"
    if cache and '_cached_examples' in globals() and _cached_examples is not None and _cached_examples.get(cache_key):
        return _cached_examples[cache_key]
    
    with open(json_path, "r", encoding="utf-8") as f:
        rag = json.load(f)
    
    # Discover all languages used
    lang_set = set()
    for entry in rag:
        nl = entry.get("nl", {})
        if isinstance(nl, dict):
            lang_set.update(nl.keys())
        else:
            lang_set.add("en")  # fallback
    
    results = {}
    embed_model = get_embed_model()
    for lang in lang_set:
        examples = []
        nls = []
        for entry in rag:
            nl_dict = entry.get("nl", {})
            if isinstance(nl_dict, str):
                nl_dict = {"en": nl_dict}
            if lang in nl_dict:
                e = entry.copy()
                e["lang"] = lang
                e["nl"] = nl_dict
                e["nl_template"] = nl_dict[lang]
                examples.append(e)
                nls.append(nl_dict[lang])
        if examples:
            embeddings = embed_model.encode(nls)
            for ex, emb in zip(examples, embeddings):
                ex["embedding"] = emb
            results[lang] = examples
    # cache
    if '_cached_examples' not in globals() or _cached_examples is None:
        globals()['_cached_examples'] = {}
    _cached_examples[cache_key] = results
    return results

def import_rag_json_to_db(json_path="data/rag.json", db_path="rag.sql"):
    """
    Loads RAG entries from the new JSON and writes them to the database.
    Only fields present in the DB schema will be written.
    """
    # Read JSON
    with open(json_path, "r", encoding="utf-8") as f:
        rag = json.load(f)
    
    # Inspect DB columns
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("PRAGMA table_info(rag_examples)")
    db_fields = [row[1] for row in c.fetchall()]
    
    # Prepare entries
    for entry in rag:
        # For backward compatibility: ensure 'nl' and 'sql' as strings
        entry = entry.copy()
        if "nl" in entry and not isinstance(entry["nl"], str):
            entry["nl"] = json.dumps(entry["nl"], ensure_ascii=False)
        # Only keep fields that are present in the table
        insert_fields = {k: entry[k] for k in db_fields if k in entry}
        # Fill missing fields as NULL/None
        for field in db_fields:
            if field not in insert_fields:
                insert_fields[field] = None

        # Serialize lists/dicts:
        insert_fields = serialize_complex_types(insert_fields)

        placeholders = ",".join(["?"] * len(db_fields))
        field_list = ",".join(db_fields)
        values = [insert_fields[k] for k in db_fields]
        c.execute(f"INSERT INTO rag_examples ({field_list}) VALUES ({placeholders})", values)
    conn.commit()
    conn.close()
    print(f"Imported {len(rag)} RAG examples from {json_path} to {db_path}.")


def clean_language_code(text):
    code = re.sub(r'[^isen]', '', text.lower())
    return "is" if code == "is" else "en"

def detect_language_llama(text):
    """Detect language for user input via Llama API (returns 'is' or 'en')."""
    prompt = (
        f'Which language is this: """{text}"""\n'
        '*Important: Just return two characters: "en" for English or "is" for Icelandic.'
    )
    response = query_ollama(prompt)
    try:
        reply = response.strip()
        return clean_language_code(reply)
    except Exception:
        return "en"

def get_weekday_date_lines(start_datetime, num_days=7, lang="en"):
    # Get the date of the Monday of that week
    dt = pd.to_datetime(start_datetime)
    monday = dt - pd.Timedelta(days=dt.weekday())
    if lang == "is":
        weekdays = ["Mánudagur", "Þriðjudagur", "Miðvikudagur", "Fimmtudagur", "Föstudagur", "Laugardagur", "Sunnudagur"]
    else:
        weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    lines = []
    for i in range(num_days):
        date_str = (monday + pd.Timedelta(days=i)).strftime("%Y-%m-%d")
        lines.append(f"{weekdays[i]}: {date_str}")
    return lines

def load_rag_examples_with_embeddings(language="en", db_path="rag.sql"):
    """
    Load all RAG examples from SQL DB for the specified language, with embeddings.
    Returns a list of example dicts.
    """
    global _cached_examples
    cache_key = f"db::{db_path}::{language}"
    if '_cached_examples' in globals() and _cached_examples is not None and _cached_examples.get(cache_key):
        return _cached_examples[cache_key]

    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    # Get all columns
    c.execute("PRAGMA table_info(rag_examples)")
    col_names = [row[1] for row in c.fetchall()]
    c.execute(f"SELECT {', '.join(col_names)} FROM rag_examples")
    rows = c.fetchall()
    conn.close()

    # Find all language codes present
    examples = []
    for row in rows:
        ex = dict(zip(col_names, row))
        # Parse 'nl' and any other JSON/text fields you want as Python objects
        for field in ["nl", "tags", "required_context_functions", "related_intents"]:
            if field in ex and ex[field]:
                try:
                    ex[field] = json.loads(ex[field]) if isinstance(ex[field], str) else ex[field]
                except Exception:
                    pass  # fallback to original if not valid JSON
        # Only add examples with language present in nl dict
        nl_dict = ex.get("nl", {})
        if isinstance(nl_dict, str):
            try:
                nl_dict = json.loads(nl_dict)
            except Exception:
                nl_dict = {"en": nl_dict}
        if language in nl_dict:
            ex["lang"] = language
            ex["nl_template"] = nl_dict[language]
            examples.append(ex)

    # Generate embeddings for current language's NLs
    embed_model = get_embed_model()
    nls = [ex["nl_template"] for ex in examples]
    embeddings = embed_model.encode(nls)
    for ex, emb in zip(examples, embeddings):
        ex["embedding"] = emb

    # Cache
    if '_cached_examples' not in globals() or _cached_examples is None:
        globals()['_cached_examples'] = {}
    _cached_examples[cache_key] = examples
    return examples


def find_best_example_(user_instruction, db_path="rag.sql", threshold=0.7):
    detected_language = detect_language_llama(user_instruction)
    examples = load_rag_examples_with_embeddings(language=detected_language, db_path=db_path)
    embed_model = get_embed_model()
    user_emb = embed_model.encode([user_instruction])[0]
    sims = np.array([
        np.dot(user_emb, ex['embedding']) / (np.linalg.norm(user_emb) * np.linalg.norm(ex['embedding']))
        for ex in examples
    ])
    best_idx = int(np.argmax(sims))
    best_score = sims[best_idx]
    if best_score < threshold:
        return None, detected_language  # Signal no good match found
    return examples[best_idx], detected_language


def find_best_example(user_instruction, db_path="rag.sql"):
    """
    Detect language, then find best-matching example in the same language using embeddings.
    Returns (example_dict, language)
    """
    detected_language = detect_language_llama(user_instruction)
    examples = load_rag_examples_with_embeddings(language=detected_language, db_path=db_path)
    embed_model = get_embed_model()
    user_emb = embed_model.encode([user_instruction])[0]
    sims = np.array([
        np.dot(user_emb, ex['embedding']) / (np.linalg.norm(user_emb) * np.linalg.norm(ex['embedding']))
        for ex in examples
    ])
    best_idx = int(np.argmax(sims))
    best = examples[best_idx]
    # No need to fix nl dict here, it's already parsed above!
    return best, detected_language

def fill_example_nl_sql(example, props, lang="is"):
    nl_dict = example.get("nl", {})
    if isinstance(nl_dict, str):
        nl_dict = {"en": nl_dict}
    nl_template = nl_dict.get(lang, "")
    sql_template = example.get("sql", "")
    # Find all keys in the templates
    import re
    keys = set(re.findall(r'{(.*?)}', nl_template + sql_template))
    fill_dict = {k: props.get(k, "") for k in keys}
    try:
        filled_nl = nl_template.format(**fill_dict)
    except Exception as e:
        print("Format error NL:", e)
        filled_nl = nl_template
    try:
        filled_sql = sql_template.format(**fill_dict)
    except Exception as e:
        print("Format error SQL:", e)
        filled_sql = sql_template
    return filled_nl, filled_sql

def get_best_rag_example(user_msg, props):
    """
    Given a user message and event props, return a summary string
    describing the best-matching RAG example for UI display.
    """
    try:
        best_example, language = find_best_example(user_msg)
        filled_nl, filled_sql = fill_example_nl_sql(best_example, props, lang=language)
        return (
            f"Similar example (intent: {best_example.get('intent','')}):\n"
            f"Example user request: {filled_nl}\n"
            f"Example SQL:\n{filled_sql}"
        )
    except Exception as e:
        return f"Could not retrieve example due to: {e}"

def build_conversation_summary(chat, max_turns=4):
    """
    Returns last max_turns from chat history as markdown string.
    """
    summary = ""
    for msg in chat["messages"][-max_turns:]:
        role = "User" if msg["role"] == "user" else "Assistant"
        summary += f"{role}: {msg['content']}\n"
    return summary.strip()

def build_llm_prompt_for_event_chat(
    chat,
    user_msg,
    db_schema_str,
    event_data_profile,
    rag_example_summary,
    props,  # <-- add this param if needed
    intent,
    language="en"
):
    
    event_course_offering_id = props.get("courseOfferingId", "")
    event_course_name = props.get("course_name", "")
    #dynamic_context = build_dynamic_context(best_example, props, chat)

    prompt = (
        f"Context: The course offeringId is {event_course_offering_id}, named {event_course_name}.\n\n"
       # f"{dynamic_context}\n\n"
        f"Example:\n{rag_example_summary}\n\n"
        f"User now asks: {user_msg}\n\n"
        "STRICT INSTRUCTIONS:\n"
        "- ONLY use the same tables and columns as in the example above.\n"
        "- ONLY use the same FROM, JOIN, and WHERE structure as the example above.\n"
        "- DO NOT introduce new tables, columns, or subqueries.\n"
        "- ONLY substitute literal values (such as courseOfferingId) as appropriate.\n"
        "- If you cannot answer, say so clearly."
    )
    return prompt


def smoke_test_sqlalchemy(sql, session):
    try:
        session.execute(text(sql))
        session.rollback()
        return True, "Query executed successfully."
    except Exception as e:
        session.rollback()
        return False, str(e)
    
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

def add_rag_example(intent, nl, sql, lang="en", db_path="rag.sql"):
    if not isinstance(nl, dict):
        nl = {lang: nl}
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("""
        INSERT INTO rag_examples (intent, nl, sql, created_at) VALUES (?, ?, ?, ?)
    """, (intent, json.dumps(nl, ensure_ascii=False), sql, datetime.now().isoformat()))
    conn.commit()
    conn.close()
    if '_cached_examples' in globals() and _cached_examples is not None:
        for code in nl.keys():
            _cached_examples.pop(code, None)

def get_all_rags(db_path="rag.sql"):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("SELECT id, intent, nl, sql, created_at FROM rag_examples ORDER BY created_at DESC")
    rows = c.fetchall()
    conn.close()
    # Parse nl as JSON
    return [
        {
            "id": row[0],
            "intent": row[1],
            "nl": json.loads(row[2]) if row[2] else {},
            "sql": row[3],
            "created_at": row[4],
        }
        for row in rows
    ]

def update_rag_example(example_id, intent, nl_dict, sql, db_path="rag.sql"):
    import sqlite3, json
    from datetime import datetime
    # Defensive: Accept both dict and str (for backward compatibility)
    if not isinstance(nl_dict, dict):
        try:
            nl_dict = json.loads(nl_dict)
        except Exception:
            nl_dict = {"en": nl_dict}
    # Only keep non-empty languages
    nl_clean = {code: nl_dict[code] for code in nl_dict if nl_dict[code].strip()}
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("""
        UPDATE rag_examples SET intent=?, nl=?, sql=?, created_at=?
        WHERE id=?
    """, (intent, json.dumps(nl_clean, ensure_ascii=False), sql, datetime.now().isoformat(), example_id))
    conn.commit()
    conn.close()    
    # Clear cache for updated languages
    if '_cached_examples' in globals() and _cached_examples is not None:
        for code in nl_clean.keys():
            _cached_examples.pop(code, None)


def delete_rag_example(rag_id, db_path="rag.sql"):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("DELETE FROM rag_examples WHERE id=?", (rag_id,))
    conn.commit()
    conn.close()
    if '_cached_examples' in globals() and _cached_examples is not None:
        _cached_examples.clear()  # Just clear all RAG example caches


def delete_rag_example_(rag_id, db_path="rag.sql"):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("DELETE FROM rag_examples WHERE id=?", (rag_id,))
    conn.commit()
    conn.close()
    if '_cached_examples' in globals() and _cached_examples is not None:
        for code in nl.keys():
            _cached_examples.pop(code, None)


def test_find_best_example():
    user_instruction = "How many students are in Introduction to AI?"
    example, lang = find_best_example(user_instruction)
    print("Best example found:", example)
    print("Detected language:", lang)
    assert 'sql' in example
    assert lang in ('en', 'is')

def test_fill_example():
    example = {
        'nl': {'en': 'How many students are in {course_name}?'},
        'sql': "SELECT COUNT(*) FROM students WHERE course_name = '{course_name}';"
    }
    props = {'course_name': 'Introduction to AI'}
    filled_nl, filled_sql = fill_example_nl_sql(example, props, lang='en')
    print("Filled NL:", filled_nl)
    print("Filled SQL:", filled_sql)
    assert 'Introduction to AI' in filled_nl
    assert 'Introduction to AI' in filled_sql

def export_rag_to_json(sqlite_path="rag.sql", json_path="data/rag.json"):
    conn = sqlite3.connect(sqlite_path)
    c = conn.cursor()
    c.execute("SELECT intent, nl, sql, created_at FROM rag_examples")
    rows = c.fetchall()
    examples = []
    for intent, nl, sql, created_at in rows:
        try:
            nl_dict = json.loads(nl)
        except Exception:
            nl_dict = {"en": nl}
        examples.append({
            "intent": intent,
            "nl": nl_dict,
            "sql": sql,
            "created_at": created_at,
        })
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(examples, f, ensure_ascii=False, indent=2)
    print(f"Exported {len(examples)} RAG examples to {json_path}")

def propose_rag_draft(user_msg=None, chat_history=None, llm_backend="openai"):
    """
    Suggest or refine a RAG example draft via LLM.
    llm_backend: "openai" (default) or "ollama"
    """
    if chat_history:
        context_window = "\n".join([f"{m['role'].capitalize()}: {m['content']}" for m in chat_history[-6:]])
        ai_prompt = (
            context_window +
            "\n\nGiven this conversation, suggest a new RAG (intent, English NL, and SQL template) for the user's request. "
            "Reply as JSON: {\"intent\":..., \"nl\":..., \"sql\":...}. "
            "If you still need more info, ask a new clarifying question."
        )
    else:
        ai_prompt = (
            f"User asked: {user_msg}\n"
            "Suggest a new RAG (intent, English NL, and SQL template) for this request.\n"
            "Reply as JSON: {\"intent\": ..., \"nl\": ..., \"sql\": ...}.\n"
            "If unclear, ask a clarifying question."
        )

    # Use OpenAI by default, Ollama only if explicitly requested
    if llm_backend == "ollama":
        ai_response = query_ollama(ai_prompt)
    else:
        ai_response = query_openai(ai_prompt)

    try:
        ai_suggestion = json.loads(ai_response)
        intent = ai_suggestion.get('intent', '')
        nl = ai_suggestion.get('nl', '')
        sql = ai_suggestion.get('sql', '')
        draft = {"intent": intent, "nl": {"en": nl} if isinstance(nl, str) else nl, "sql": sql, "status": "draft"}
        ai_message = (f"Here's my suggestion:\n\n"
                      f"Intent: {intent}\n"
                      f"NL: {draft['nl'].get('en','')}\n"
                      f"SQL: {sql}\n\n"
                      "Please let me know if this RAG meets your requirements or if I need to make any adjustments.")
        return draft, ai_message, True
    except Exception:
        return None, ai_response, False

if __name__ == "__main__":
    #test_find_best_example()
    #test_fill_example()
    import_rag_json_to_db()
