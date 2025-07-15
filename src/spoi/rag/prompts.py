"""
rag/prompts.py

Prompt formatting and LLM context-building for RAG.
"""

def build_llm_prompt_for_event_chat(
    chat,
    user_msg,
    db_schema_str,
    event_data_profile,
    rag_example_summary,
    props,
    intent,
    language="en"
):
    """
    Build a prompt to send to the LLM for event chat SQL completion.
    """
    event_course_offering_id = props.get("courseOfferingId", "")
    event_course_name = props.get("course_name", "")
    prompt = (
        f"Context: The course offeringId is {event_course_offering_id}, named {event_course_name}.\n\n"
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

def build_conversation_summary(chat, max_turns=4):
    """
    Returns last max_turns from chat history as markdown string.
    """
    summary = ""
    for msg in chat["messages"][-max_turns:]:
        role = "User" if msg["role"] == "user" else "Assistant"
        summary += f"{role}: {msg['content']}\n"
    return summary.strip()
