"""One provider-status interpretation for every roster decision.

An activated player must be imported as active before a reserve designation is
treated as playable. Unknown tags remain conditional; they are never silently
converted into a numerical injury probability.
"""

from __future__ import annotations

import re

ACTIVE = {"", "A", "ACT", "ACTIVE", "HEALTHY"}
UNAVAILABLE = {
    "O",
    "OUT",
    "INACTIVE",
    "IA",
    "IR",
    "IR+",
    "INJURED RESERVE",
    "IR-RETURN",
    "IR-R",
    "IR-DTR",
    "RESERVE/INJURED",
    "RESERVE-INJURED",
    "SUSPENDED",
    "SUSP",
    "SUS",
    "RESERVE/SUSPENDED",
    "PUP",
    "PUP-R",
    "PUP-P",
    "RESERVE/PUP",
    "NFI",
    "NFI-R",
    "NFI-A",
    "RESERVE/NFI",
    "NA",
    "PHYSICALLY UNABLE TO PERFORM",
    "NON-FOOTBALL INJURY",
}
RESERVE_SLOTS = {"IR", "IR+", "NA"}


def normalize_status(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().upper()).replace("_", "-")


def unavailable_status(value: str | None) -> bool:
    status = normalize_status(value)
    return status in UNAVAILABLE or status.startswith(("IR-", "PUP-", "NFI-"))


def conditional_status(value: str | None) -> bool:
    return normalize_status(value) not in ACTIVE and not unavailable_status(value)


def can_start(value: str | None, current_slot: str | None = None) -> bool:
    return not unavailable_status(value) and normalize_status(current_slot) not in RESERVE_SLOTS
