"""
rag/service.py

High-level RAG operations, combines embeddings, DB and prompts.
"""

from .embeddings import get_embed_model
from .db import get_all_rags
from .prompts import build_llm_prompt_for_event_chat
import numpy as np

def find_best_example(user_instruction, lang="en", db_path="rag.sql"):
    """
    Given a user instruction, returns the best-matching RAG example for that language.
    """
    from .db import get_all_rags  # or more advanced loader
    examples = get_all_rags(db_path)
    embed_model = get_embed_model()
    # Get only those with text in this language
    filtered = [ex for ex in examples if lang in ex["nl"]]
    if not filtered:
        return None
    user_emb = embed_model.encode([user_instruction])[0]
    nls = [ex["nl"][lang] for ex in filtered]
    ex_embeds = embed_model.encode(nls)
    sims = np.array([
        np.dot(user_emb, emb) / (np.linalg.norm(user_emb) * np.linalg.norm(emb))
        for emb in ex_embeds
    ])
    best_idx = int(np.argmax(sims))
    return filtered[best_idx]

def fill_example_nl_sql(example, props, lang="en"):
    import re
    nl_dict = example.get("nl", {})
    nl_template = nl_dict.get(lang, "")
    sql_template = example.get("sql", "")
    keys = set(re.findall(r'{(.*?)}', nl_template + sql_template))
    fill_dict = {k: props.get(k, "") for k in keys}
    try:
        filled_nl = nl_template.format(**fill_dict)
    except Exception:
        filled_nl = nl_template
    try:
        filled_sql = sql_template.format(**fill_dict)
    except Exception:
        filled_sql = sql_template
    return filled_nl, filled_sql

def get_best_rag_example(user_msg, props, lang="en"):
    """
    Given a user message and event props, return a summary string
    describing the best-matching RAG example for UI display.
    """
    best_example = find_best_example(user_msg, lang=lang)
    if not best_example:
        return "No good RAG example found."
    filled_nl, filled_sql = fill_example_nl_sql(best_example, props, lang=lang)
    return (
        f"Similar example (intent: {best_example.get('intent','')}):\n"
        f"Example user request: {filled_nl}\n"
        f"Example SQL:\n{filled_sql}"
    )
