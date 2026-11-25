"""One-time interactive setup for the single HR administrator."""

from __future__ import annotations

import getpass
import re
import sqlite3
import sys

from main import PASSWORD_HASHER, database_connection, initialize_database, utc_timestamp


USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{3,50}$")


def main() -> int:
    initialize_database()
    with database_connection() as connection:
        existing_admin = connection.execute("SELECT username FROM admins LIMIT 1").fetchone()
        if existing_admin:
            print(f"An admin account ({existing_admin['username']}) is already configured.")
            print("No changes were made.")
            return 1

    username = input("Admin username: ").strip()
    if not USERNAME_PATTERN.fullmatch(username):
        print("Username must be 3-50 characters using letters, numbers, ., _, or -.")
        return 1
    with database_connection() as connection:
        employee = connection.execute(
            "SELECT 1 FROM employees WHERE username = ? COLLATE NOCASE",
            (username,),
        ).fetchone()
    if employee:
        print("That username belongs to an employee account. Choose a different admin username.")
        return 1

    password = getpass.getpass("Admin password: ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        print("Passwords do not match.")
        return 1
    if len(password) < 8:
        print("Password must be at least 8 characters.")
        return 1

    password_hash = PASSWORD_HASHER.hash(password)
    try:
        with database_connection() as connection:
            connection.execute(
                "INSERT INTO admins(username, password_hash, created_at) VALUES (?, ?, ?)",
                (username, password_hash, utc_timestamp()),
            )
            connection.commit()
    except sqlite3.IntegrityError:
        print("An admin account is already configured. No changes were made.")
        return 1

    print(f"Admin account '{username}' created successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
