"""User settings persisted in SQLite, layered over the environment defaults.

Precedence is: value saved in the Settings tab, then the `.env` / environment value,
then the built-in default. That ordering is what lets someone paste an Underdog token
into the UI and have it take effect without touching a file or restarting anything.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.tables import SettingRow
from app.pricing.entry import (
    DEFAULT_INSURED_PAYOUTS,
    DEFAULT_STANDARD_PAYOUTS,
    PayoutStructure,
)

SETTINGS_KEY = "user_settings"


@dataclass
class UserSettings:
    # --- credentials ------------------------------------------------------
    underdog_token: str = ""
    cfbd_api_key: str = ""

    # --- how EV is quoted -------------------------------------------------
    reference_entry_type: str = "standard"
    reference_entry_legs: int = 3
    standard_payouts: dict[str, float] = field(
        default_factory=lambda: {str(k): v for k, v in DEFAULT_STANDARD_PAYOUTS.items()}
    )
    insured_payouts: dict[str, dict[str, float]] = field(
        default_factory=lambda: {
            str(k): {str(kk): vv for kk, vv in v.items()}
            for k, v in DEFAULT_INSURED_PAYOUTS.items()
        }
    )

    # --- bankroll ---------------------------------------------------------
    bankroll: float = 1000.0
    kelly_fraction: float = 0.25

    # --- board defaults ---------------------------------------------------
    default_mode: str = "value"
    min_edge: float = 0.0
    min_confidence: float = 0.0
    hide_negative_ev: bool = False

    def payout_structure(self) -> PayoutStructure:
        return PayoutStructure(
            standard={int(k): float(v) for k, v in self.standard_payouts.items()},
            insured={
                int(k): {int(kk): float(vv) for kk, vv in v.items()}
                for k, v in self.insured_payouts.items()
            },
        )

    def effective_underdog_token(self) -> str:
        return (self.underdog_token or get_settings().underdog_token or "").strip()

    def effective_cfbd_key(self) -> str:
        return (self.cfbd_api_key or get_settings().cfbd_api_key or "").strip()

    def redacted(self) -> dict[str, Any]:
        """Settings safe to send to the browser: secrets become presence flags."""
        data = asdict(self)
        data["underdog_token"] = ""
        data["cfbd_api_key"] = ""
        data["underdog_token_set"] = bool(self.effective_underdog_token())
        data["cfbd_api_key_set"] = bool(self.effective_cfbd_key())
        return data


def load_settings(session: Session) -> UserSettings:
    row = session.get(SettingRow, SETTINGS_KEY)
    if row is None:
        return UserSettings()
    try:
        stored = json.loads(row.value_json)
    except json.JSONDecodeError:
        return UserSettings()
    known = {f for f in UserSettings.__dataclass_fields__}
    return UserSettings(**{k: v for k, v in stored.items() if k in known})


def save_settings(session: Session, updates: dict[str, Any]) -> UserSettings:
    """Merge `updates` into the stored settings.

    An empty string for a secret means "leave it alone", so re-saving the form from a
    browser that never received the token does not wipe it.
    """
    current = load_settings(session)
    for key, value in updates.items():
        if key not in UserSettings.__dataclass_fields__:
            continue
        if key in ("underdog_token", "cfbd_api_key") and value in ("", None):
            continue
        setattr(current, key, value)

    session.merge(SettingRow(key=SETTINGS_KEY, value_json=json.dumps(asdict(current))))
    session.commit()
    return current


def clear_secret(session: Session, key: str) -> UserSettings:
    current = load_settings(session)
    if key in ("underdog_token", "cfbd_api_key"):
        setattr(current, key, "")
        session.merge(SettingRow(key=SETTINGS_KEY, value_json=json.dumps(asdict(current))))
        session.commit()
    return current
