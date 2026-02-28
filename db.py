# db.py
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import sessionmaker, declarative_base

load_dotenv()

MSSQL_SERVER = os.getenv("MSSQL_SERVER", "127.0.0.1")
MSSQL_DATABASE = os.getenv("MSSQL_DATABASE", "fondofund")
MSSQL_USER = os.getenv("MSSQL_USER", "sa")
MSSQL_PASSWORD = os.getenv("MSSQL_PASSWORD", "")
MSSQL_PORT = int(os.getenv("MSSQL_PORT", "1433"))

db_url = URL.create(
    "mssql+pymssql",
    username=MSSQL_USER,
    password=MSSQL_PASSWORD,  # aman walau ada @!#: dll
    host=MSSQL_SERVER,
    port=MSSQL_PORT,
    database=MSSQL_DATABASE,
)

engine = create_engine(
    db_url,
    pool_pre_ping=True,
    pool_recycle=1800,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()