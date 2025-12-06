import sys
import os
sys.path.append(os.getcwd())

try:
    from app.db import engine
    print(f"Engine url drivername: {engine.url.drivername}")
    import psycopg
    print("psycopg version:", psycopg.__version__)
    
    # Try connecting
    with engine.connect() as conn:
        print("Connection successful")
except Exception as e:
    print(f"Error: {e}")
