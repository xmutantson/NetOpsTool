# netops/cli.py
from __future__ import annotations
import argparse
import secrets
from .db import SessionLocal, init_db
from .models import Station
from .auth import hash_password

def add_station(name: str, password: str):
    with SessionLocal() as s:
        name = name.strip().upper()
        st = s.query(Station).filter(Station.name == name).first()
        if st:
            # UPSERT: update password & rotate token salt (invalidates old JWTs)
            st.password_hash = hash_password(password)
            st.token_salt = secrets.token_hex(8)
            s.commit()
            print(f"Updated station: {name}")
            return
        st = Station(
            name=name,
            password_hash=hash_password(password),
            token_salt=secrets.token_hex(8)
        )
        s.add(st)
        s.commit()
        print(f"Added station: {name}")

def reset_station_password(name: str, password: str):
    with SessionLocal() as s:
        name = name.strip().upper()
        st = s.query(Station).filter(Station.name == name).first()
        if not st:
            raise SystemExit(f"Station '{name}' not found.")
        st.password_hash = hash_password(password)
        st.token_salt = secrets.token_hex(8)  # invalidate existing tokens
        s.commit()
        print(f"Password reset for station: {name}")

def delete_station(name: str):
    with SessionLocal() as s:
        name = name.strip().upper()
        st = s.query(Station).filter(Station.name == name).first()
        if not st:
            raise SystemExit(f"Station '{name}' not found.")
        # Rely on ORM cascade; if not configured, this will raise on commit.
        s.delete(st)
        s.commit()
        print(f"Deleted station: {name}")

def main():
    parser = argparse.ArgumentParser(prog="netops")
    sub = parser.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add-station", help="Create or update (upsert) a station with the given password")
    a.add_argument("name")
    a.add_argument("password")
    r = sub.add_parser("reset-station-password", help="Update password for an existing station")
    r.add_argument("name")
    r.add_argument("password")
    # delete aliases so the bulk script's autodetect works
    for verb in ("delete-station", "del-station", "rm-station"):
        d = sub.add_parser(verb, help="Delete a station (and cascade its data)")
        d.add_argument("name")
    args = parser.parse_args()

    init_db()

    if args.cmd == "add-station":
        add_station(args.name, args.password)
    elif args.cmd == "reset-station-password":
        reset_station_password(args.name, args.password)
    elif args.cmd in ("delete-station", "del-station", "rm-station"):
        delete_station(args.name)

if __name__ == "__main__":
    main()
