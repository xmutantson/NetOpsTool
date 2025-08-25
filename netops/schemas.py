# netops/schemas.py
from __future__ import annotations
from typing import List, Optional, Literal
from datetime import datetime
from pydantic import BaseModel, Field, validator

Direction = Literal["inbound", "outbound"]

class LoginRequest(BaseModel):
    station: str
    password: str

class TokenResponse(BaseModel):
    token: str

class OriginCoords(BaseModel):
    lat: float
    lon: float

class FlowRow(BaseModel):
    origin: str
    dest: str
    direction: Direction
    legs: int = 0
    weight_lbs: float = 0.0

class ManifestRow(BaseModel):
    flight_id: Optional[int] = Field(None, alias="flight_id")
    flight_code: Optional[str] = None
    tail: Optional[str] = None
    direction: Optional[Direction] = None
    origin: Optional[str] = None
    dest: Optional[str] = None
    cargo_type: Optional[str] = None
    cargo_weight_lbs: Optional[float] = None
    remarks: Optional[str] = None
    takeoff_hhmm: Optional[str] = None
    eta_hhmm: Optional[str] = None
    is_ramp_entry: Optional[int] = 0
    complete: Optional[int] = 0
    updated_at: Optional[datetime] = None
    aoct_flight_id: Optional[int] = None  # alias; will map from flight_id if needed

    @validator("takeoff_hhmm", "eta_hhmm")
    def _hhmm(cls, v):
        if v is None or v == "":
            return v
        v = v.strip()
        if not (len(v) in (3,4) and v.isdigit()):
            raise ValueError("HHMM must be 3–4 digits")
        return v.zfill(4)

class IngestSnapshot(BaseModel):
    station: str
    generated_at: datetime
    default_origin: Optional[str] = None
    origin_coords: Optional[OriginCoords] = None
    inventory_last_update: Optional[str] = None
    window_hours: int = 24
    flows: List[FlowRow] = Field(default_factory=list)
    manifests: List[ManifestRow] = Field(default_factory=list)
