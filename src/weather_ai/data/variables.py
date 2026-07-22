"""Explicit ERA5 request-name to NetCDF-variable contracts."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Era5VariableSpec:
    """Expected raw metadata and broad integrity bounds for one ERA5 variable."""

    requested_name: str
    internal_name: str
    canonical_units: str
    accepted_units: frozenset[str]
    safe_minimum: float
    safe_maximum: float


def normalize_units(value: str) -> str:
    """Normalize explicitly supported spelling differences without inferring units."""

    normalized = value.strip().lower().replace("−", "-")
    normalized = normalized.replace("**", "").replace("^", "")
    return re.sub(r"[\s{}]", "", normalized)


def _units(*values: str) -> frozenset[str]:
    return frozenset(normalize_units(value) for value in values)


ERA5_VARIABLE_SPECS: dict[str, Era5VariableSpec] = {
    "2m_temperature": Era5VariableSpec(
        requested_name="2m_temperature",
        internal_name="t2m",
        canonical_units="K",
        accepted_units=_units("K", "kelvin"),
        safe_minimum=150.0,
        safe_maximum=350.0,
    ),
    "2m_dewpoint_temperature": Era5VariableSpec(
        requested_name="2m_dewpoint_temperature",
        internal_name="d2m",
        canonical_units="K",
        accepted_units=_units("K", "kelvin"),
        safe_minimum=150.0,
        safe_maximum=350.0,
    ),
    "surface_pressure": Era5VariableSpec(
        requested_name="surface_pressure",
        internal_name="sp",
        canonical_units="Pa",
        accepted_units=_units("Pa", "pascal", "pascals"),
        safe_minimum=0.0,
        safe_maximum=120_000.0,
    ),
    "10m_u_component_of_wind": Era5VariableSpec(
        requested_name="10m_u_component_of_wind",
        internal_name="u10",
        canonical_units="m s-1",
        accepted_units=_units("m s-1", "m s^-1", "m s**-1", "m/s"),
        safe_minimum=-200.0,
        safe_maximum=200.0,
    ),
    "10m_v_component_of_wind": Era5VariableSpec(
        requested_name="10m_v_component_of_wind",
        internal_name="v10",
        canonical_units="m s-1",
        accepted_units=_units("m s-1", "m s^-1", "m s**-1", "m/s"),
        safe_minimum=-200.0,
        safe_maximum=200.0,
    ),
}


def get_variable_spec(requested_name: str) -> Era5VariableSpec | None:
    """Return an exact mapping; unknown request names are never guessed."""

    return ERA5_VARIABLE_SPECS.get(requested_name)
