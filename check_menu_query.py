
import sys
import os
import traceback
sys.path.append(os.getcwd())

from app.db import SessionLocal
from app.models.core import MenuItem, MenuCategory

def check():
    db = SessionLocal()
    try:
        print("Querying Menu Items...")
        # Replicate menu.py query
        q = (
            db.query(MenuItem)
            .join(MenuCategory, MenuCategory.id == MenuItem.category_id)
        )
        items = q.all()
        print(f"Found {len(items)} items")
        for i in items[:3]:
            print(f"Item: {i.name}, Station: {i.kitchen_station_id}")
            
    except Exception as e:
        print("CRASHED:")
        traceback.print_exc()
    finally:
        db.close()

if __name__ == "__main__":
    check()
