"""Command-line account tools (run on the server).

  python manage.py list
  python manage.py add <username> [--admin] [--email you@example.com]
  python manage.py reset <username>        # new temporary password, unlocks the account
  python manage.py deactivate <username>
"""
import argparse
import secrets
import time

from app import auth, db


def temp():
    return "Aug-" + secrets.token_urlsafe(9) + "7"


def main():
    ap = argparse.ArgumentParser(description="Augusta Terminal account tools")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    add = sub.add_parser("add")
    add.add_argument("username")
    add.add_argument("--admin", action="store_true")
    add.add_argument("--email", default="")
    for name in ("reset", "deactivate"):
        sub.add_parser(name).add_argument("username")
    args = ap.parse_args()
    db.connect()
    if args.cmd == "list":
        for u in db.q("SELECT username, role, active, email, last_login FROM users ORDER BY id"):
            last = time.strftime("%Y-%m-%d %H:%M", time.localtime(u["last_login"])) if u["last_login"] else "never"
            print(f"{u['username']:<20} {u['role']:<8} {'active' if u['active'] else 'OFF':<7} {u['email']:<30} last login {last}")
        return
    if args.cmd == "add":
        password = temp()
        try:
            auth.create_user(args.username, password, "admin" if args.admin else "analyst", args.email)
        except Exception as e:
            raise SystemExit(f"Could not create user: {getattr(e, 'detail', e)}")
        print(f"Created {args.username}. Temporary password: {password}")
        return
    user = db.one("SELECT id FROM users WHERE username=?", (args.username,))
    if not user:
        raise SystemExit("No such user.")
    if args.cmd == "reset":
        password = temp()
        db.x("UPDATE users SET pw_hash=?, must_change=1, failed=0, locked_until=0, active=1 WHERE id=?",
             (auth.hash_password(password), user["id"]))
        auth.end_sessions(user["id"])
        print(f"Temporary password for {args.username}: {password}")
    else:
        db.x("UPDATE users SET active=0 WHERE id=?", (user["id"],))
        auth.end_sessions(user["id"])
        print(f"{args.username} deactivated.")


if __name__ == "__main__":
    main()
