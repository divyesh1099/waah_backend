
import sys
import os
import traceback
sys.path.append(os.getcwd())
from app.db import SessionLocal
from app.models.core import User, Branch
from app.util.security import verify_pw, hash_pw

E = "kot_tester"
P = "secret123"

def check():
    db = SessionLocal()
    try:
        print("Checking Login Logic...")
        
        # 1. Query User
        print("Querying User...")
        user = db.query(User).filter(User.username == E).first()
        if not user:
            print("User not found!")
            return
        print(f"User found: {user.id}, tenant: {user.tenant_id}")
        
        # 2. Verify Password
        print("Verifying Password...")
        print(f"Hash: {user.pass_hash}")
        ok = verify_pw(user.pass_hash, P)
        print(f"Password Valid: {ok}")
        
        # 3. Query Branch
        print("Querying Branch...")
        default_branch = (
            db.query(Branch)
            .filter(Branch.tenant_id == user.tenant_id)
            .order_by(Branch.id.asc())
            .first()
        )
        if default_branch:
             print(f"Branch found: {default_branch.id}")
        else:
             print("No branch found.")

        # 4. Create Token
        print("Creating Token...")
        from app.util.security import create_token
        claims = {
            "sub": str(user.id),
            "tenant_id": str(user.tenant_id),
            "branch_id": str(default_branch.id) if default_branch else None
        }
        token = create_token(claims)
        print(f"Token created: {token[:10]}...")

    except Exception as e:
        print("CRASHED:")
        traceback.print_exc()
    finally:
        db.close()

if __name__ == "__main__":
    check()
