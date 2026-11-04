"""Seed commands.

    python -m app.seed reference                     Roles, permissions, settings, workstreams, activities.
    python -m app.seed demo                          Reference data + clearly labelled synthetic DEMO data.
    python -m app.seed admin --email E --name N      Create a Platform Super Admin (password from MRV_ADMIN_PASSWORD).
    python -m app.seed reset-role-permissions        Restore default role → permission mapping.
"""

import argparse
import os
import secrets
import sys

from sqlalchemy import select

from app.core.config import get_settings
from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models import Role, User
from app.seed.reference import seed_reference


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.seed")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("reference")
    sub.add_parser("reset-role-permissions")
    demo = sub.add_parser("demo")
    demo.add_argument("--password", help="Password for demo accounts (default: DEMO_USER_PASSWORD env or random)")
    decisions = sub.add_parser("decisions", help="Load the V2 register of pending programme-owner decisions")
    decisions.add_argument("--programme", help="Programme code to link the decisions to")
    admin = sub.add_parser("admin")
    admin.add_argument("--email", required=True)
    admin.add_argument("--name", required=True)
    args = parser.parse_args()
    settings = get_settings()

    with SessionLocal() as db:
        if args.command == "reference":
            seed_reference(db)
            db.commit()
            print("Reference data seeded.")
        elif args.command == "reset-role-permissions":
            seed_reference(db, reset_role_permissions=True)
            db.commit()
            print("Role permissions restored to defaults.")
        elif args.command == "demo":
            if not settings.is_development:
                sys.exit(f"Refusing to load synthetic demo data: APP_ENV is '{settings.app_env}'. "
                         "Set APP_ENV=local (or development/test) for a local database.")
            from app.seed.demo import seed_demo
            password = args.password or os.environ.get("DEMO_USER_PASSWORD")
            generated = password is None
            password = password or secrets.token_urlsafe(9)
            seed_reference(db)
            summary = seed_demo(db, password)
            db.commit()
            print(f"Demo data seeded: {summary['farmers']} synthetic farmers, {summary['plots']} plots.")
            print("Demo accounts (local development only):")
            for email in summary["users"]:
                print(f"  {email}")
            print(f"Password for all demo accounts: {password}" + ("  (generated – note it now)" if generated else ""))
        elif args.command == "decisions":
            from app.models import Programme
            from app.seed.decisions import seed_programme_decisions
            programme = db.scalar(select(Programme).where(Programme.code == args.programme)) if args.programme else None
            added = seed_programme_decisions(db, programme.id if programme else None)
            db.commit()
            print(f"{added} pending decision(s) added.")
        elif args.command == "admin":
            password = os.environ.get("MRV_ADMIN_PASSWORD")
            if not password or len(password) < 12:
                sys.exit("Set MRV_ADMIN_PASSWORD (12+ characters) in the environment before creating an admin.")
            seed_reference(db)
            if db.scalar(select(User).where(User.email == args.email.lower())):
                sys.exit(f"User {args.email} already exists.")
            role = db.scalar(select(Role).where(Role.code == "super_admin"))
            db.add(User(email=args.email.lower(), full_name=args.name, password_hash=hash_password(password),
                        roles=[role], must_change_password=True))
            db.commit()
            print(f"Super admin {args.email} created; password change required at first sign-in.")


if __name__ == "__main__":
    main()
