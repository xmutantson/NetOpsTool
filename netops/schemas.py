from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, constr, root_validator, validator

from .timeutil import utc_naive

Direction = Literal["inbound", "outbound"]
AirportCode = constr(strip_whitespace=True, min_length=1, max_length=8)
StationName = constr(strip_whitespace=True, min_length=1, max_length=128)


class DataModel(BaseModel):
    class Config:
        allow_inf_nan = False


class LoginRequest(DataModel):
    station: StationName
    password: str = Field(..., min_length=1, max_length=1024)

    _station = validator("station", allow_reuse=True)(lambda value: value.upper())


class TokenResponse(DataModel):
    token: str


class OriginCoords(DataModel):
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)


class AirportRequest(OriginCoords):
    code: AirportCode

    _code = validator("code", allow_reuse=True)(lambda value: value.upper())


class FlowRow(DataModel):
    origin: AirportCode
    dest: AirportCode
    direction: Direction
    legs: int = Field(0, ge=0)
    weight_lbs: float = Field(0.0, ge=0)

    _codes = validator("origin", "dest", allow_reuse=True)(lambda value: value.upper())


class ManifestRow(DataModel):
    flight_id: Optional[int] = Field(None, ge=0)
    aoct_flight_id: Optional[int] = Field(None, ge=0)
    flight_code: Optional[constr(strip_whitespace=True, min_length=1, max_length=32)] = None
    tail: Optional[constr(strip_whitespace=True, max_length=16)] = None
    direction: Optional[Direction] = None
    origin: Optional[AirportCode] = None
    dest: Optional[AirportCode] = None
    cargo_type: Optional[str] = Field(None, max_length=128)
    cargo_weight_lbs: Optional[float] = Field(None, ge=0)
    remarks: Optional[str] = Field(None, max_length=10000)
    takeoff_hhmm: Optional[str] = None
    eta_hhmm: Optional[str] = None
    is_ramp_entry: Optional[Literal[0, 1]] = None
    complete: Optional[Literal[0, 1]] = None
    updated_at: Optional[datetime] = None

    @root_validator(pre=True)
    def _flight_id_alias(cls, values):
        values = dict(values)
        first, alias = values.get("flight_id"), values.get("aoct_flight_id")
        if first is not None and alias is not None and first != alias:
            raise ValueError("flight_id and aoct_flight_id must agree")
        if first is None and alias is not None:
            values["flight_id"] = alias
        return values

    @root_validator(skip_on_failure=True)
    def _required_identity(cls, values):
        if values.get("flight_code") is None and values.get("flight_id") is None:
            raise ValueError("Manifest requires flight_code, flight_id, or aoct_flight_id")
        return values

    @validator("tail", "origin", "dest")
    def _codes(cls, value):
        return value.upper() if value else value

    @validator("takeoff_hhmm", "eta_hhmm")
    def _hhmm(cls, value):
        if value is None or value == "":
            return value
        value = value.strip()
        if len(value) not in (3, 4) or not value.isdigit():
            raise ValueError("HHMM must be 3-4 digits")
        value = value.zfill(4)
        if int(value[:2]) > 23 or int(value[2:]) > 59:
            raise ValueError("HHMM must be a valid 24-hour time")
        return value

    @validator("updated_at")
    def _updated_at(cls, value):
        return utc_naive(value) if value is not None else None


class InventoryItem(DataModel):
    category: Optional[constr(strip_whitespace=True, max_length=128)] = None
    category_id: Optional[int] = Field(None, ge=0)
    item: constr(strip_whitespace=True, min_length=1, max_length=128)
    qty: Optional[float] = Field(None, ge=0)
    weight_lbs: Optional[float] = Field(None, ge=0)
    updated_at: Optional[datetime] = None

    @validator("category")
    def _category(cls, value):
        return value or None

    @validator("updated_at")
    def _updated_at(cls, value):
        return utc_naive(value) if value is not None else None


class IngestSnapshot(DataModel):
    station: StationName
    generated_at: datetime
    default_origin: Optional[AirportCode] = None
    origin_coords: Optional[OriginCoords] = None
    inventory_last_update: Optional[str] = Field(None, max_length=64)
    window_hours: int = Field(24, ge=0, le=8760)
    flows: List[FlowRow] = Field(default_factory=list, max_items=10000)
    manifests: List[ManifestRow] = Field(default_factory=list, max_items=10000)
    inventory: Optional[List[InventoryItem]] = Field(None, max_items=10000)

    _station = validator("station", allow_reuse=True)(lambda value: value.upper())
    _generated_at = validator("generated_at", allow_reuse=True)(utc_naive)

    @validator("default_origin")
    def _origin(cls, value):
        return value.upper() if value else value

    @validator("inventory")
    def _unique_inventory(cls, values):
        if values is not None:
            identities = [(item.category, item.item) for item in values]
            if len(set(identities)) != len(identities):
                raise ValueError("Inventory contains duplicate category/item pairs")
        return values
