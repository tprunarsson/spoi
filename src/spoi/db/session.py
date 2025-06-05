from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# For SQLite; for Postgres: "postgresql://user:pass@localhost/dbname"
DATABASE_URL = "sqlite:///spoi.sqlite"

engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(bind=engine)

