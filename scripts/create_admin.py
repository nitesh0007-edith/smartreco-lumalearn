#!/usr/bin/env python3
"""Create or promote an administrator without hardcoding credentials."""

import argparse
import getpass
import sys
from pathlib import Path

from sqlalchemy import func, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import SessionLocal, init_db  # noqa: E402
from app.models import User  # noqa: E402
from app.security import hash_password  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Create or promote a LumaLearn admin")
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", default="LumaLearn Admin")
    args = parser.parse_args()
    email = args.email.strip().lower()
    init_db()
    with SessionLocal() as db:
        user = db.scalar(select(User).where(func.lower(User.email) == email))
        if user:
            user.role = "admin"
            user.is_active = True
            db.commit()
            print(f"Promoted {email} to admin.")
            return
        password = getpass.getpass("Admin password (10+ characters): ")
        if len(password) < 10:
            raise SystemExit("Password must contain at least 10 characters.")
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            raise SystemExit("Passwords do not match.")
        db.add(
            User(
                email=email,
                name=args.name.strip(),
                password_hash=hash_password(password),
                role="admin",
            )
        )
        db.commit()
        print(f"Created admin {email}.")


if __name__ == "__main__":
    main()
