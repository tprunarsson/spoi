from langchain_ollama import OllamaLLM
from langchain_core.messages import HumanMessage, AIMessage
from langchain_community.chat_message_histories import SQLChatMessageHistory
from langchain_core.runnables.history import RunnableWithMessageHistory
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from rag import (
    query_ollama, get_best_rag_example, build_llm_prompt_for_event_chat,
    profile_sqlalchemy_row, extract_sql_from_llm_response
)
from spoi.db.queries import get_db_schema_str, extract_localized_name
from spoi.db.models import EventMessage
from sqlalchemy.orm import Session
from sqlalchemy import text
from datetime import datetime, timezone
import pandas as pd

class EventChatManager:
    def __init__(self, db_session: Session, model_name: str = "llama3"):
        self.session = db_session
        self.model_name = model_name
        self.llm = OllamaLLM(model=model_name)
        # You could optionally set up the full chain here if needed

    def get_thread_id(self, event_id, timetable_version):
        return f"{event_id}:{timetable_version}"

    def get_sqlchat_history(self, event_id, timetable_version):
        """Returns a SQLChatMessageHistory for this event/timetable."""
        return SQLChatMessageHistory(
            session_id=self.get_thread_id(event_id, timetable_version),
            connection=self.session.bind,
            table_name="event_message"
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
        # Add user message to DB (for history)
        self.add_user_message(event_id, timetable_version, user_msg)
        # Intent detection
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

        # Otherwise, generic LLM fallback (but still with history)
        else:
            # Use the RunnableWithMessageHistory pattern
            return self.llm_with_message_history(event_id, timetable_version, user_msg)

    def llm_with_message_history(self, event_id, timetable_version, user_msg):
        # 1. Build a ChatPromptTemplate (with history placeholder)
        prompt = ChatPromptTemplate.from_messages([
            MessagesPlaceholder(variable_name="history"),
            ("human", "{input}"),
        ])
        # 2. Wrap LLM in RunnableWithMessageHistory
        chat_history = self.get_sqlchat_history(event_id, timetable_version)
        chain = prompt | self.llm
        runnable = RunnableWithMessageHistory(
            runnable=chain,
            get_message_history=lambda session_id: chat_history,
            input_messages_key="input",
            history_messages_key="history"
        )
        # 3. Call it
        session_id = self.get_thread_id(event_id, timetable_version)
        output = runnable.invoke(
            {"input": user_msg},
            config={"configurable": {"session_id": session_id}}
        )
        # Save to DB
        self.add_ai_message(event_id, timetable_version, output.content)
        return output.content

    def delete_event_chat_history(self, event_id, timetable_version):
        print(f"Attempting to delete: event_id={event_id}, timetable_version={timetable_version}")
        matches = self.session.query(EventMessage)\
            .filter_by(event_id=event_id, timetable_version=timetable_version).all()
        print(f"Found {len(matches)} EventMessage(s) before delete")
        num_deleted = self.session.query(EventMessage)\
            .filter_by(event_id=event_id, timetable_version=timetable_version)\
            .delete()
        self.session.commit()
        print(f"Deleted {num_deleted} EventMessage(s)")
        return num_deleted


    def get_chat_history(self, event_id, timetable_version):
        """Retrieve chat history from the DB for the event."""
        messages = self.session.query(EventMessage)\
            .filter_by(event_id=event_id, timetable_version=timetable_version)\
            .order_by(EventMessage.timestamp.asc())\
            .all()
        chat_history = []
        for msg in messages:
            if msg.role == "user":
                chat_history.append(HumanMessage(content=msg.content))
            else:
                chat_history.append(AIMessage(content=msg.content))
        return chat_history
