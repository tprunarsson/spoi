from langchain.memory.chat_message_histories import SQLChatMessageHistory
from langchain.memory import ConversationBufferMemory
from langchain.llms import OpenAI, Ollama
from sqlalchemy.orm import Session
from sqlalchemy import text
from spoi.db.models import EventMessage
import pandas as pd
from datetime import datetime, timezone
from rag import (
    query_ollama, get_best_rag_example, build_llm_prompt_for_event_chat, profile_sqlalchemy_row, extract_sql_from_llm_response
)
from spoi.db.queries import get_db_schema_str, extract_localized_name

class EventChatManager:
    def __init__(self, db_session: Session, model_name: str = "llama3"):
        self.session = db_session
        self.model_name = model_name
        self.llm = self._get_llm()

    def _get_llm(self):
        if self.model_name == "openai":
            return OpenAI(temperature=0)
        else:
            return Ollama(model=self.model_name, temperature=0.1)

    def get_memory_for_event(self, event_id: str, timetable_version: str):
        return SQLChatMessageHistory(
            session_id=f"{event_id}:{timetable_version}",
            connection_string="sqlite:///spoi.sqlite",  # <-- your DB path here!
            table_name="event_message"                  # <-- your table name here!
        )


    def get_langchain_memory(self, event_id: str, timetable_version: str):
        history = self.get_memory_for_event(event_id, timetable_version)
        return ConversationBufferMemory(
            memory_key="chat_history",
            chat_memory=history,
            return_messages=True
        )

    def add_user_message(self, event_id, timetable_version, content):
        self.session.add(EventMessage(
            event_id=event_id,
            timetable_version=timetable_version,
            role="user",
            content=content,
            timestamp=datetime.now(timezone.utc)
        ))
        self.session.commit()

    def add_ai_message(self, event_id, timetable_version, content):
        self.session.add(EventMessage(
            event_id=event_id,
            timetable_version=timetable_version,
            role="ai",
            content=content,
            timestamp=datetime.now(timezone.utc)
        ))
        self.session.commit()

    def detect_intent(self, user_msg: str) -> str:
        prompt = (
            f"User input: '''{user_msg}'''.\n"
            "Does the user want to:\n"
            "A) Get information (like a SELECT SQL query),\n"
            "B) Request a change (like UPDATE/DELETE SQL),\n"
            "C) Something else?\n"
            "Reply ONLY with one word: 'info', 'change', or 'other'."
        )
        intent = query_ollama(prompt).strip().split()[0].lower()
        return intent if intent in ("info", "change", "other") else "other"

    def get_llm_prompt(self, user_msg, props, chat_state, intent):
        db_schema_str = get_db_schema_str(self.session)
        rag_example_summary = get_best_rag_example(user_msg, props)
        event_data_profile = profile_sqlalchemy_row(props)
        return build_llm_prompt_for_event_chat(
            chat=chat_state,
            user_msg=user_msg,
            db_schema_str=db_schema_str,
            event_data_profile=event_data_profile,
            rag_example_summary=rag_example_summary,
            props=props,
            intent=intent,
            language=chat_state.get("language", "en"),
        )

    def run_sql_and_format(self, sql_query: str, lang="is") -> str:
        try:
            result = self.session.execute(text(sql_query))
            rows = result.fetchall()
            if not rows:
                return "No results found."
            df = pd.DataFrame(rows, columns=result.keys())
            for col in df.columns:
                if df[col].dtype == object and df[col].apply(lambda x: isinstance(x, str) and x.strip().startswith("{")).any():
                    df[col] = df[col].apply(lambda x: extract_localized_name(x, lang=lang))
            return (
                f"Here is the SQL executed:\n```sql\n{sql_query}\n```\n"
                f"Results:\n\n{df.to_markdown(index=False)}"
            )
        except Exception as e:
            return f"Error executing SQL:\n```sql\n{sql_query}\n```\n\n{e}"

    def handle_event_query(self, event_id, timetable_version, user_msg, props, chat_state):
        # Add user message to DB
        self.add_user_message(event_id, timetable_version, user_msg)

        # Detect intent
        intent = self.detect_intent(user_msg)
        chat_state["last_user_intent"] = intent
        chat_state["last_user_instruction"] = user_msg

        # Info intent: RAG SQL
        if intent == "info":
            prompt = self.get_llm_prompt(user_msg, props, chat_state, intent)
            sql_query = query_ollama(prompt)
            sql_clean = extract_sql_from_llm_response(sql_query)
            chat_state["last_sql"] = sql_clean
            answer = self.run_sql_and_format(sql_clean, lang=chat_state.get("language", "is"))
            self.add_ai_message(event_id, timetable_version, answer)
            return answer

        # Change intent: paraphrase and confirm
        elif intent == "change":
            prompt = self.get_llm_prompt(user_msg, props, chat_state, intent) + "\n\nParaphrase this request."
            paraphrased = query_ollama(prompt)
            answer = f"I understand you want to: **{paraphrased}**\n\nIs this correct? (Yes/No)"
            self.add_ai_message(event_id, timetable_version, answer)
            return answer

        # Otherwise, generic LLM fallback
        else:
            prompt = self.get_llm_prompt(user_msg, props, chat_state, intent)
            response = query_ollama(prompt)
            self.add_ai_message(event_id, timetable_version, response)
            return response
