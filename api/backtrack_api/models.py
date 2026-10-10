"""The API contract (ARCHITECTURE.md section 6), as pydantic models.

These are the server side of the contract; the web app's TypeScript types
mirror them, and tests/test_contracts.py checks every fixture against them.
Times are ISO 8601 with an offset.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Source = Literal["live", "as_of", "fixture"]
Line = Literal["RED", "GOLD", "BLUE", "GREEN"]
Dir = Literal["N", "S", "E", "W"]
Mode = Literal["bus", "rail", "streetcar", "any"]


class ErrorBody(BaseModel):
    error: Literal["db_unavailable", "not_found", "bad_request", "rate_limited"]
    message: str


class Place(BaseModel):
    place_id: str                       # "stop:210555" | "station:510027" | "alias:gsu"
    label: str
    kind: Literal["stop", "station", "alias"]
    lat: float
    lon: float
    distance_m: int | None = None
    stop_ids: list[str]                 # platform / child stops only
    routes: list[str]
    lines: list[Line]
    needs_confirm: bool


# 1. health ------------------------------------------------------------------------

class RecorderHealth(BaseModel):
    recorder: str | None = None         # which machine (RECORDER_ID); the freshest one is shown
    first_started_at: datetime | None = None
    last_poll_age_s: int | None = None
    data_age_s: int | None = None       # how old the newest vehicle data is
    capture_pct: float | None = None    # bus: feed versions captured / versions published (one per 30 s)
    poll_ok_pct: float | None = None    # rail: polls that succeeded
    open_gap: bool = False              # an outage is in progress right now
    db: str | None = None               # the recorder's own view of its database sync


class Compression(BaseModel):
    before_mb: float
    after_mb: float
    ratio: float


class Health(BaseModel):
    ok: bool
    now: datetime
    git_sha: str
    db_ok: bool
    recorders: dict[Literal["bus", "rail"], RecorderHealth]
    positions_logged: int | None        # null until the database is reachable
    since: datetime | None
    compression: Compression | None = None
    source: Source


# 2-4. places and directions --------------------------------------------------------

class PlacesNearby(BaseModel):
    places: list[Place] = Field(max_length=3)
    source: Source


class PlacesSearch(BaseModel):
    resolved: Place | None
    choices: list[Place] = Field(max_length=3)
    method: Literal["alias", "station", "stop_fuzzy", "route_context", "none"]
    needs_confirm: bool
    source: Source


class DirectionOption(BaseModel):
    direction_id: Literal[0, 1] | None
    direction: Dir | None
    label: str                          # "Toward North Ave Station" / "To Airport"
    stop_ids: list[str]


class Directions(BaseModel):
    ask: bool                           # false when the anchor stops serve only one direction
    options: list[DirectionOption]
    source: Source


# 5. trains nearby ("Which train are you on now?") ---------------------------------------

class NextStation(BaseModel):
    place_id: str                       # "station:<parent_stop_id>"
    label: str
    eta: datetime


class NearbyTrain(BaseModel):
    vehicle_key: str                    # "rail:<TRAIN_ID>"
    line: Line                          # each train carries its own line (Red and Gold share a trunk)
    direction: Dir | None
    destination_label: str
    next_station: NextStation | None
    distance_m: int | None = None       # only with a GPS fix; never coordinates
    preselect: bool                     # accuracy <= 100 m, fix < 30 s old, train within 600 m


class TrainsNearby(BaseModel):
    trains: list[NearbyTrain] = Field(max_length=12)
    source: Source


# 7. sessions ----------------------------------------------------------------------

class IntakeAnchor(BaseModel):
    role: Literal["alight", "board"]
    place_id: str
    stop_ids: list[str] = Field(min_length=1, max_length=40)
    at: datetime                        # t_center, from the time button (resolved on the server clock) or a picked time
    time_kind: Literal["relative", "picked"]
    window_min: int = Field(ge=1, le=360)   # W


class OnVehicle(BaseModel):
    mode: Literal["rail"]
    vehicle_key: str


class Item(BaseModel):
    category: Literal["phone", "wallet", "bag", "keys", "laptop", "other"]


class Intake(BaseModel):
    """Tap answers only. Never GPS, free text, item descriptions, screenshots, names or phones."""
    item: Item
    mode: Literal["bus", "rail", "streetcar", "unknown"]
    route: str | None = Field(None, max_length=8)
    line: Line | None = None
    direction: Dir | None = None
    direction_id: Literal[0, 1] | None = None
    anchors: list[IntakeAnchor] = Field(min_length=1, max_length=2)
    on_vehicle: OnVehicle | None = None
    car: Literal["front", "middle", "back"] | None = None
    taken: Literal["no", "maybe", "yes", "unsure"] | None = None
    walk: Literal["normal", "slow"] | None = None


class SessionRequest(BaseModel):
    intake: Intake
    as_of: datetime | None = None


class Evidence(BaseModel):
    role: Literal["alight", "board"]
    place_label: str
    at: datetime
    t_in: datetime
    t_out: datetime
    distance_m: int


class MatchCandidate(BaseModel):
    vehicle_key: str
    mode: Literal["bus", "rail"]
    vehicle_id: str
    label: str | None
    route: str | None
    continuing_as: str | None = None
    line: Line | None = None
    direction_label: str
    confidence: float
    level: Literal["high", "medium", "low"]
    evidence: list[Evidence]
    flags: list[Literal["route_differs", "line_differs", "direction_differs", "off_shape",
                        "feed_canceled_trip", "relies_on_gap"]]
    source: Literal["live"] = "live"


class BlockVehicle(BaseModel):
    vehicle_key: str
    label: str
    note: Literal["probably the same bus"] = "probably the same bus"


class ScheduleCandidate(BaseModel):
    trip_id: str
    route: str
    headsign: str | None
    scheduled_at: datetime
    stop_label: str
    status: Literal["scheduled", "canceled", "unknown"]
    block_vehicle_now: BlockVehicle | None = None


class Coverage(BaseModel):
    unreported_share: float
    recorder_gap: bool


class TieOption(BaseModel):
    vehicle_key: str
    facts: dict[str, str]


class TieBreak(BaseModel):
    options: list[TieOption]


class SeparateOption(BaseModel):
    label: str
    vehicle_keys: list[str]


class SeparateBy(BaseModel):
    question: Literal["route", "direction", "board_place"]
    options: list[SeparateOption]


class MatchWindow(BaseModel):
    from_: datetime = Field(alias="from")
    to: datetime
    model_config = {"populate_by_name": True}


class MatchContext(BaseModel):
    vehicles_near: int
    counted: Literal["trains", "buses", "buses and trains"]
    radius_m: int = 400
    place_label: str
    window: MatchWindow


class MatchStats(BaseModel):
    positions_scanned: int
    vehicles_considered: int
    query_ms: int
    sql: str
    engine: Literal["sql", "python"]


class Match(BaseModel):
    status: Literal["matched", "ambiguous", "uncertain", "not_reporting", "no_match"]
    level: Literal["high", "medium", "low"] | None
    context: MatchContext
    candidates: list[MatchCandidate] = Field(max_length=5)
    schedule_candidates: list[ScheduleCandidate]
    coverage: Coverage
    tie_break: TieBreak | None = None
    separate_by: SeparateBy | None = None
    stats: MatchStats


class SessionResponse(BaseModel):
    session_id: str
    deletes_by: datetime
    as_of: datetime | None
    record: bool
    match: Match
    source: Source


# 8. replay -------------------------------------------------------------------------

class ReplayFocus(BaseModel):
    lat: float
    lon: float
    radius_m: int = 400


class ReplayAnchor(BaseModel):
    lat: float
    lon: float
    label: str
    at: datetime


class ReplayVehicle(BaseModel):
    k: str                              # VehicleKey
    label: str | None
    route: str
    mode: Literal["bus", "rail"]
    role: Literal["match", "candidate", "other", "own"]
    p: list[tuple[int, int, int]]       # [frame, lon_e5, lat_e5]


class MatchTrack(BaseModel):
    vehicle_key: str
    p: list[tuple[int, int, int]]       # [t_offset_s, lon_e5, lat_e5], repeats collapsed


class Replay(BaseModel):
    t0: datetime
    step_s: int = 60
    n_frames: int
    bbox: tuple[float, float, float, float]     # [min_lon, min_lat, max_lon, max_lat]
    window: MatchWindow                 # <= 60 min, centered on the match's pass time
    focus: ReplayFocus                  # the replay opens here
    anchors: list[ReplayAnchor]
    vehicles: list[ReplayVehicle] = Field(max_length=150)
    match_tracks: list[MatchTrack]
    route_shape: list[tuple[int, int]] | None = None   # [lon_e5, lat_e5]
    source: Source


# 9. where is it now -----------------------------------------------------------------

class VehiclePosition(BaseModel):
    lon: float
    lat: float
    bearing: int | None = None


class NextStop(BaseModel):
    place_id: str
    label: str
    eta: datetime
    source: Literal["live", "schedule"]


class VehicleNow(BaseModel):
    vehicle_key: str
    label: str | None
    route: str                          # bus route short name; for rail, the line
    continuing_as: str | None = None    # the next trip on the same block, when the route changes
    line: Line | None = None
    destination_label: str | None = None
    position: VehiclePosition
    at: datetime                        # the fix time: when the vehicle was at `position`
    age_s: int                          # now (or as_of) minus `at`
    state: Literal["live", "stale", "dark", "at_terminus"]
    next_stops: list[NextStop] = Field(max_length=5)
    turns_around_at: str | None = None
    # at_terminus only: the station the train is at, the {station} of the decision 8 copy. Not
    # turns_around_at: at a terminus MARTA has usually flipped DESTINATION to the far end already
    terminus_label: str | None = None
    trip_status: Literal["scheduled", "canceled", "unknown"]
    meet_ok: bool | None = None         # filled by the meet check (decision 9), not by this feature
    meet_reason: Literal["ok", "stale", "passed", "margin"] | None = None
    source: Source


# 10. plan --------------------------------------------------------------------------

class PlanUser(BaseModel):
    """Where the rider is now. Used in memory for this request only, never stored or logged."""
    lat: float | None = Field(None, ge=-90, le=90)
    lon: float | None = Field(None, ge=-180, le=180)
    accuracy_m: float | None = Field(None, ge=0)
    fix_age_s: float | None = Field(None, ge=0)
    place_id: str | None = None
    at_station: bool | None = None
    on_vehicle: OnVehicle | None = None
    walk: Literal["normal", "slow"] = "normal"


class PlanRequest(BaseModel):
    vehicle_keys: list[str] = Field(default_factory=list, max_length=2)   # 2 on a tie
    user: PlanUser = Field(default_factory=PlanUser)
    as_of: datetime | None = None


class LostFound(BaseModel):
    open_now: bool
    next_open: datetime
    usually: bool = False   # the day is not on the verified holiday check (Columbus Day and later): "usually open"


class Meet(BaseModel):
    possible: bool
    reason: Literal["meet", "ahead", "stale", "no_eta", "too_far", "not_shared"]
    vehicle_keys: list[str]
    place_id: str | None = None
    place_label: str | None = None
    platform_hint: Literal["front", "middle", "back"] | None = None
    # rail: the direction of the platform the meet is on ("Wait at West End southbound"). /now's meet check
    # takes it back as meet_direction, so a train that turned at the end of the line never reads as coming
    direction: Dir | None = None
    eta: list[datetime] | None = None
    walk_s: int | None = None
    margin_min: int | None = None


class CardAction(BaseModel):
    type: Literal["copy", "tel", "mailto", "link", "airtag_share", "ics"]
    label_id: str
    value: str


class CardEvidence(BaseModel):
    role: str
    place_label: str
    at: datetime
    distance_m: int


class Card(BaseModel):
    kind: Literal["safety", "bag_staff", "meet", "too_far", "tell_staff", "keep", "report", "call",
                  "freeze_cards", "phone_lost", "police_property"]
    step: int
    facts: dict[str, str | int | float]
    fact_sources: dict[str, Literal["db", "static_gtfs", "contacts", "rules"]]
    actions: list[CardAction]
    evidence: list[CardEvidence]
    source: Literal["live", "schedule", "static"]


class ReportFormFields(BaseModel):
    route_number: str | None = None
    bus_number: str | None = None
    direction: str | None = None
    location: str
    time: str
    rail_line: str | None = None
    rail_station: str | None = None
    rail_car_number: str | None = None   # only from the rider's own answer, filled on the phone
    train_run: str | None = None         # TRAIN_ID: a run number, never a railcar number


class Wording(BaseModel):
    text: str
    wording: Literal["gemini", "template"]


class EmailWording(BaseModel):
    subject: str
    body: str
    wording: Literal["gemini", "template"]


class Report(BaseModel):
    form_fields: ReportFormFields
    candidates_note: str | None = None
    email: EmailWording
    call_script: Wording


class Plan(BaseModel):
    generated_at: datetime
    lost_found: LostFound
    meet: Meet | None
    cards: list[Card]
    report: Report
    source: Source
