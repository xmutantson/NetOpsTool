# netops/models.py
from __future__ import annotations
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, DateTime, Float, Text, ForeignKey, UniqueConstraint, Index
)
from sqlalchemy.orm import relationship
from .db import Base

class Station(Base):
    __tablename__ = "stations"
    id = Column(Integer, primary_key=True)
    name = Column(String(128), unique=True, nullable=False, index=True)
    password_hash = Column(String(256), nullable=False)
    token_salt = Column(String(64), nullable=False, default="")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    last_seen_at = Column(DateTime, nullable=True)
    last_default_origin = Column(String(8), nullable=True)
    last_origin_lat = Column(Float, nullable=True)
    last_origin_lon = Column(Float, nullable=True)

    snapshots = relationship("Snapshot", back_populates="station", cascade="all,delete-orphan")
    flights = relationship("Flight", back_populates="station", cascade="all,delete-orphan")

class Snapshot(Base):
    __tablename__ = "snapshots"
    id = Column(Integer, primary_key=True)
    station_id = Column(Integer, ForeignKey("stations.id"), nullable=False)
    generated_at = Column(DateTime, nullable=False)
    window_hours = Column(Integer, nullable=False, default=24)
    inventory_last_update = Column(String(64), nullable=True)

    station = relationship("Station", back_populates="snapshots")
    flows = relationship("Flow", back_populates="snapshot", cascade="all,delete-orphan")

    __table_args__ = (
        UniqueConstraint("station_id", "generated_at", name="uq_snap_station_gen"),
        Index("ix_snapshot_generated_at", "generated_at"),
    )

class Flow(Base):
    __tablename__ = "flows"
    id = Column(Integer, primary_key=True)
    snapshot_id = Column(Integer, ForeignKey("snapshots.id"), nullable=False)
    origin = Column(String(8), nullable=False)
    dest = Column(String(8), nullable=False)
    direction = Column(String(10), nullable=False)  # inbound|outbound
    legs = Column(Integer, nullable=False, default=0)
    weight_lbs = Column(Float, nullable=False, default=0.0)

    snapshot = relationship("Snapshot", back_populates="flows")
    __table_args__ = (
        Index("ix_flows_route_dir", "origin", "dest", "direction"),
        Index("ix_flows_snapshot", "snapshot_id"),
    )

class Flight(Base):
    __tablename__ = "flights"
    id = Column(Integer, primary_key=True)
    station_id = Column(Integer, ForeignKey("stations.id"), nullable=False)
    aoct_flight_id = Column(Integer, nullable=True)
    flight_code = Column(String(32), nullable=True, unique=True)
    tail = Column(String(16), nullable=True)
    direction = Column(String(10), nullable=True)  # inbound|outbound
    origin = Column(String(8), nullable=True)
    dest = Column(String(8), nullable=True)
    cargo_type = Column(String(128), nullable=True)
    cargo_weight_lbs = Column(Float, nullable=True)
    takeoff_hhmm = Column(String(4), nullable=True)
    eta_hhmm = Column(String(4), nullable=True)
    is_ramp_entry = Column(Integer, nullable=False, default=0)
    complete = Column(Integer, nullable=False, default=0)
    remarks = Column(Text, nullable=True)
    first_seen_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    last_seen_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    station = relationship("Station", back_populates="flights")
    __table_args__ = (
        Index("ix_flights_origin_dest_dir", "origin", "dest", "direction"),
        Index("ix_flights_complete_seen", "complete", "last_seen_at"),
        Index("ix_flights_station_aoct", "station_id", "aoct_flight_id"),
    )

class IngestLog(Base):
    __tablename__ = "ingest_log"
    id = Column(Integer, primary_key=True)
    station_id = Column(Integer, ForeignKey("stations.id"), nullable=False)
    received_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    status = Column(String(16), nullable=False)  # accepted|rejected
    error = Column(Text, nullable=True)
    raw = Column(Text, nullable=True)

class Airport(Base):
    __tablename__ = "airports"
    code = Column(String(8), primary_key=True)  # ICAO/IATA/FAA
    lat = Column(Float, nullable=False)
    lon = Column(Float, nullable=False)
    __table_args__ = (Index("ix_airports_code", "code"),)
