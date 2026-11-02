import argparse
import getpass
import re
from contextlib import closing

from argon2 import PasswordHasher

from src import db


def main():
    parser = argparse.ArgumentParser(description="Manage Folio administrators locally")
    parser.add_argument("action", choices=("create", "grant", "revoke", "password"))
    parser.add_argument("username", help="Existing username/email, or a new local username for create")
    args = parser.parse_args()
    username = args.username.strip().casefold()
    db.init_db()
    user = db.get_user_by_username(username) or db.get_user_by_email(username)
    if args.action == "create":
        if user:
            parser.error("Account already exists; use grant or password instead")
        if not re.fullmatch(r"[a-z0-9_-]{3,64}", username):
            parser.error("Local usernames need 3–64 lowercase letters, digits, underscores or hyphens")
    elif user is None:
        parser.error("Account not found")
    if args.action in ("create", "password"):
        password = getpass.getpass("Password: ")
        if not 8 <= len(password) <= 128:
            parser.error("Local passwords need 8–128 characters")
        if password != getpass.getpass("Confirm password: "):
            parser.error("Passwords do not match")
        password_hash = PasswordHasher().hash(password)
        if args.action == "create":
            user = db.create_user(username, password_hash)
        else:
            db.update_password_hash(user["id"], password_hash)
            with closing(db._connect()) as connection, connection:
                connection.execute("DELETE FROM sessions WHERE user_id = ?", (user["id"],))
    if args.action != "password":
        with closing(db._connect()) as connection, connection:
            connection.execute("UPDATE users SET is_admin = ? WHERE id = ?",
                               (int(args.action != "revoke"), user["id"]))
    print(f"Account {args.action} complete: {username} ({user['id']})")


if __name__ == "__main__":
    main()
