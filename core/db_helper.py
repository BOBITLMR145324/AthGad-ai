import os
from sqlalchemy import create_engine
from dotenv import load_dotenv

load_dotenv('config/.env')

# Extract connection parameters safely
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "earthguard_db")

# Construct the standard PostgreSQL connection URI string
DATABASE_URI = f"postgresql+psycopg2://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# Create a global reusable engine instance
db_engine = create_engine(DATABASE_URI, echo=False)

def get_db_engine():
    """Returns the SQLAlchemy engine for Pandas and ORM operations."""
    return db_engine