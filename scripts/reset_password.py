"""
LOCAL DEV ONLY -- resets a user's password directly in the database.

Usage:
    python3 scripts/reset_password.py you@example.com "your-new-password"
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import text
from backend.app.config.database import db_transaction
from backend.app.security.passwords import hash_password


def main():
    if len(sys.argv) != 3:
        print('Usage: python3 scripts/reset_password.py you@example.com "your-new-password"')
        sys.exit(1)

    email = sys.argv[1].strip().lower()
    new_password = sys.argv[2]

    if len(new_password) < 10:
        print("Password must be at least 10 characters (same rule the signup form uses).")
        sys.exit(1)

    password_hash = hash_password(new_password)

    with db_transaction() as conn:
        result = conn.execute(
            text("UPDATE users SET password_hash = :hash WHERE email = :email"),
            {"hash": password_hash, "email": email},
        )
        if result.rowcount == 0:
            print(f"No user found with email {email!r}. Nothing was changed.")
            sys.exit(1)

    print(f"Password reset for {email}. You can log in with the new password now.")


if __name__ == "__main__":
    main()