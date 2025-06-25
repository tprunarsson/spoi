from spoi.db.models import Base
from spoi.db.session import engine

if __name__ == "__main__":
    try:
        Base.metadata.create_all(bind=engine)
        print("Database initialized (all tables created if missing).")
    except Exception as e:
        print(f"Error initializing the database: {e}")
