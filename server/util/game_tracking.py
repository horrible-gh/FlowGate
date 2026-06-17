"""Badugi confidence-model tracking — T066

Record confidence data in SQLite for every betting action.
Tuning data used for threshold adjustments.
"""
import logging
import os
import sqlite3
import threading
from datetime import datetime

logger = logging.getLogger("game_tracking")

_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "logs", "game_tracking.db")
_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
        _conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("""
            CREATE TABLE IF NOT EXISTS badugi_actions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                ts          TEXT    NOT NULL,
                room_id     TEXT,
                round_no    INTEGER,
                phase       INTEGER,
                player_idx  INTEGER,
                hand_score  INTEGER,
                my_conf     REAL,
                opp_conf    REAL,
                gap         REAL,
                raise_th    REAL,
                call_th     REAL,
                bet_pressure REAL,
                opp_draws   TEXT,
                action      TEXT,
                amount      INTEGER,
                model_ver   TEXT
            )
        """)
        # Existing DB migration: add the model_ver column if it does not exist
        try:
            _conn.execute("ALTER TABLE badugi_actions ADD COLUMN model_ver TEXT")
        except sqlite3.OperationalError:
            pass  # Already exists

        # Round result table
        _conn.execute("""
            CREATE TABLE IF NOT EXISTS badugi_rounds (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                ts          TEXT    NOT NULL,
                room_id     TEXT,
                round_no    INTEGER,
                winner      INTEGER,
                prize       INTEGER,
                pot         INTEGER,
                player_cnt  INTEGER,
                fold_cnt    INTEGER,
                fold_win    INTEGER,
                hands       TEXT,
                chip_changes TEXT,
                chips_after TEXT,
                model_ver   TEXT
            )
        """)
        _conn.commit()
    return _conn


_MODEL_VER: str | None = None
_TRACKING: bool | None = None

_PRESET_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..",
    "rulebook", "phantom_engine", "presets", "confidence_preset.json",
)
_preset_mtime: float = 0.0


def _load_preset_meta() -> None:
    """Read version and tracking from confidence_preset.json (mtime-cached)."""
    global _MODEL_VER, _TRACKING, _preset_mtime
    try:
        mtime = os.path.getmtime(_PRESET_PATH)
        if mtime == _preset_mtime and _MODEL_VER is not None:
            return
        import json
        with open(_PRESET_PATH, encoding="utf-8") as f:
            data = json.load(f)
        _MODEL_VER = data.get("version", "unknown")
        _TRACKING = data.get("tracking", True)
        _preset_mtime = mtime
    except Exception:
        _MODEL_VER = _MODEL_VER or "unknown"
        _TRACKING = _TRACKING if _TRACKING is not None else True


def _is_tracking_enabled() -> bool:
    """Check whether tracking is on/off (preset hot-swap)."""
    _load_preset_meta()
    return bool(_TRACKING)


def _get_model_ver() -> str:
    _load_preset_meta()
    return _MODEL_VER or "unknown"


def log_badugi_action(
    room_id: str,
    round_no: int,
    phase: int,
    player_idx: int,
    hand_score: int,
    result: dict,
    bet_pressure: float,
    opp_draws: list[int] | None,
    action: str,
    amount: int,
) -> None:
    """INSERT confidence-decision data."""
    if not _is_tracking_enabled():
        return
    try:
        with _lock:
            conn = _get_conn()
            model_ver = _get_model_ver()
            conn.execute(
                """INSERT INTO badugi_actions
                   (ts, room_id, round_no, phase, player_idx, hand_score,
                    my_conf, opp_conf, gap, raise_th, call_th,
                    bet_pressure, opp_draws, action, amount, model_ver)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    datetime.now().isoformat(timespec="seconds"),
                    room_id,
                    round_no,
                    phase,
                    player_idx,
                    hand_score,
                    result.get("my_conf"),
                    result.get("opp_conf"),
                    result.get("gap"),
                    result.get("raise_th"),
                    result.get("call_th"),
                    bet_pressure,
                    str(opp_draws) if opp_draws else None,
                    action,
                    amount,
                    model_ver,
                ),
            )
            conn.commit()
    except Exception as e:
        logger.warning("game_tracking INSERT failed: %s", e)


def log_badugi_round(
    room_id: str,
    round_no: int,
    winner: int,
    prize: int,
    pot: int,
    player_cnt: int,
    fold_cnt: int,
    fold_win: bool,
    hands: dict,
    chip_changes: dict,
    chips_after: dict,
) -> None:
    """INSERT round results."""
    if not _is_tracking_enabled():
        return
    import json
    try:
        with _lock:
            conn = _get_conn()
            model_ver = _get_model_ver()
            conn.execute(
                """INSERT INTO badugi_rounds
                   (ts, room_id, round_no, winner, prize, pot,
                    player_cnt, fold_cnt, fold_win,
                    hands, chip_changes, chips_after, model_ver)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    datetime.now().isoformat(timespec="seconds"),
                    room_id,
                    round_no,
                    winner,
                    prize,
                    pot,
                    player_cnt,
                    fold_cnt,
                    1 if fold_win else 0,
                    json.dumps(hands, ensure_ascii=False),
                    json.dumps(chip_changes),
                    json.dumps(chips_after),
                    model_ver,
                ),
            )
            conn.commit()
    except Exception as e:
        logger.warning("game_tracking round INSERT failed: %s", e)
