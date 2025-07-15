"""
rag/db.py

Database helpers for RAG examples (CRUD, import/export).
"""

import sqlite3
import json
from datetime import datetime

RAG_DB_PATH = "rag.sql"

def add_rag_example(intent, nl, sql, lang="en", db_path=RAG_DB_PATH):
    """
    Insert a new RAG example.
    """
    if not isinstance(nl, dict):
        nl = {lang: nl}
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute(
        "INSERT INTO rag_examples (intent, nl, sql, created_at) VALUES (?, ?, ?, ?)",
        (intent, json.dumps(nl, ensure_ascii=False), sql, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()

def get_all_rags(db_path=RAG_DB_PATH):
    """
    Return all RAG examples.
    """
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("SELECT id, intent, nl, sql, created_at FROM rag_examples ORDER BY created_at DESC")
    rows = c.fetchall()
    conn.close()
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

def update_rag_example(example_id, intent, nl_dict, sql, db_path=RAG_DB_PATH):
    """
    Update a RAG example.
    """
    if not isinstance(nl_dict, dict):
        try:
            nl_dict = json.loads(nl_dict)
        except Exception:
            nl_dict = {"en": nl_dict}
    nl_clean = {code: nl_dict[code] for code in nl_dict if nl_dict[code].strip()}
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute(
        "UPDATE rag_examples SET intent=?, nl=?, sql=?, created_at=? WHERE id=?",
        (intent, json.dumps(nl_clean, ensure_ascii=False), sql, datetime.now().isoformat(), example_id),
    )
    conn.commit()
    conn.close()

def delete_rag_example(example_id, db_path=RAG_DB_PATH):
    """
    Delete a RAG example.
    """
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("DELETE FROM rag_examples WHERE id=?", (example_id,))
    conn.commit()
    conn.close()

def get_languages_from_rag(db_path=RAG_DB_PATH):
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
    return {code: code_to_label.get(code, code.capitalize()) for code in sorted(langs)}

def get_statuses_from_rag(db_path=RAG_DB_PATH):
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
