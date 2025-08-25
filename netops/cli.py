# netops/cli.py
from __future__ import annotations
import argparse
import secrets
from .db import SessionLocal, init_db
from .models import Station
from .auth import hash_password

def add_station(name: str, password: str):
    with SessionLocal() as s:
        exists = s.query(Station).filter(Station.name == name).first()
        if exists:
            raise SystemExit(f"Station '{name}' already exists.")
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
        st = s.query(Station).filter(Station.name == name).first()
        if not st:
            raise SystemExit(f"Station '{name}' not found.")
        st.password_hash = hash_password(password)
        st.token_salt = secrets.token_hex(8)  # invalidate existing tokens
        s.commit()
        print(f"Password reset for station: {name}")

def main():
    parser = argparse.ArgumentParser(prog="netops")
    sub = parser.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add-station")
    a.add_argument("name")
    a.add_argument("password")
    r = sub.add_parser("reset-station-password")
    r.add_argument("name")
    r.add_argument("password")
    args = parser.parse_args()

    init_db()

    if args.cmd == "add-station":
        add_station(args.name, args.password)
    elif args.cmd == "reset-station-password":
        reset_station_password(args.name, args.password)

if __name__ == "__main__":
    main()
