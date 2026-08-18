import os
import sys

# Ensure the project root is importable
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from core.db_helper import get_db_engine  # noqa: E402
from sqlalchemy import text  # noqa: E402


def promote_to_admin(email: str) -> bool:
    """Sets role='admin' for the given user email. Returns True on success."""
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        print("[ERROR] Please provide a valid email address.")
        return False

    engine = get_db_engine()
    try:
        with engine.begin() as conn:
            existing = conn.execute(
                text("SELECT user_code FROM users WHERE email = :email"),
                {"email": email},
            ).fetchone()

            if not existing:
                print(f"[ERROR] No AthGad AI account found for: {email}")
                print("   Tip: register the user first, then run this script again.")
                return False

            conn.execute(
                text("UPDATE users SET role = 'admin' WHERE email = :email"),
                {"email": email},
            )
        print(f"[OK] {email} has been promoted to Admin.")
        print("   The admin can now sign in at /login and access the Admin Workspace.")
        return True
    except Exception as e:
        print(f"[ERROR] Could not promote user: {e}")
        return False


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python create_admin.py your@email.com")
        sys.exit(1)
    success = promote_to_admin(sys.argv[1])
    sys.exit(0 if success else 1)
