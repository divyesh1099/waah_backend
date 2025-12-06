import sys
import os

# Add the current directory to sys.path to make app module importable
sys.path.append(os.getcwd())

from sqlalchemy import inspect, text
from app.db import engine

def inspect_db():
    insp = inspect(engine)
    tables = insp.get_table_names()
    print("Tables:", tables)
    
    for table in tables:
        columns = insp.get_columns(table)
        col_names = [c["name"] for c in columns]
        print(f"Table {table} columns: {col_names}")

if __name__ == "__main__":
    inspect_db()
