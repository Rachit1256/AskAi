"""IMD domain vocabulary and conventions.

Everything meteorologically specific lives here so the generic engine stays
generic. Values that IMD may revise by circular (notably the rainfall
departure bands) are declared as data, in one place, with a source note --
verify them against the current departmental circular before deployment.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Final

# --------------------------------------------------------------------------
# Seasons -- as published on the IMD Data Service Portal cyclone series.
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Season:
    code: str
    label: str
    months: tuple[int, ...]
    aliases: tuple[str, ...] = ()


SEASONS: Final[tuple[Season, ...]] = (
    Season("JF", "Winter", (1, 2), ("winter", "cold weather")),
    Season("MAM", "Pre-Monsoon", (3, 4, 5), ("pre monsoon", "premonsoon", "hot weather", "summer")),
    Season(
        "JJAS",
        "Monsoon",
        (6, 7, 8, 9),
        ("monsoon", "southwest monsoon", "sw monsoon", "rainy season"),
    ),
    Season(
        "OND",
        "Post-Monsoon",
        (10, 11, 12),
        ("post monsoon", "postmonsoon", "northeast monsoon", "ne monsoon", "retreating monsoon"),
    ),
)

SEASON_BY_CODE: Final[dict[str, Season]] = {s.code: s for s in SEASONS}

MONTH_NAMES: Final[tuple[str, ...]] = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
MONTH_ABBR: Final[dict[str, int]] = {
    **{name.lower(): i for i, name in enumerate(MONTH_NAMES, 1)},
    **{name[:3].lower(): i for i, name in enumerate(MONTH_NAMES, 1)},
}

# Column headers that are aggregates of sibling monthly columns. Melting these
# alongside the monthly values double counts every total.
DERIVED_COLUMN_TOKENS: Final[frozenset[str]] = frozenset(
    {"annual", "total", "year", "yearly", "jf", "mam", "jjas", "ond", "season", "seasonal"}
)

# --------------------------------------------------------------------------
# Geography
# --------------------------------------------------------------------------

HOMOGENEOUS_REGIONS: Final[tuple[str, ...]] = (
    "Whole India",
    "North-East India",
    "North-West India",
    "Peninsular India",
    "Central India",
)

GEO_HIERARCHY: Final[tuple[str, ...]] = (
    "station",
    "district",
    "subdivision",
    "homogeneous_region",
    "all_india",
)

#: Levels above ``station`` must be rolled up by area weight, never by a plain
#: mean over stations -- a simple average will not reproduce IMD's published
#: subdivisional figures.
AREA_WEIGHTED_LEVELS: Final[frozenset[str]] = frozenset(GEO_HIERARCHY[1:])

#: Former and colloquial station/city names seen in historical archives, mapped
#: to the spelling used on the Data Service Portal. Extend from the station
#: master rather than editing code.
STATION_NAME_ALIASES: Final[dict[str, str]] = {
    "bombay": "mumbai",
    "calcutta": "kolkata",
    "madras": "chennai",
    "bangalore": "bengaluru",
    "poona": "pune",
    "trivandrum": "thiruvananthapuram",
    "pondicherry": "puducherry",
    "gurgaon": "gurugram",
    "baroda": "vadodara",
    "calicut": "kozhikode",
    "ooty": "udagamandalam",
    "allahabad": "prayagraj",
    "cherrapunjee": "sohra",
    "cherrapunji": "sohra",
    "mysuru": "mysore",
    "warangal": "hanamkonda",
    "simla": "shimla",
    "benares": "varanasi",
    "cochin": "kochi",
    "mangalore": "mangaluru",
    "belgaum": "belagavi",
    "hubli": "hubballi",
}

#: Suffixes appearing in station names that denote the site type, not the place.
STATION_TYPE_SUFFIXES: Final[dict[str, str]] = {
    "(a)": "aerodrome",
    "ams": "agromet",
    "mo": "meteorological_office",
}

# --------------------------------------------------------------------------
# Units, sentinels and observation conventions
# --------------------------------------------------------------------------

CANONICAL_UNITS: Final[dict[str, str]] = {
    "rainfall": "mm",
    "temperature": "degC",
    "pressure": "hPa",
    "wind_speed": "kmph",
    "humidity": "percent",
    "olr": "W/m2",
    "sst": "degC",
    "precipitable_water": "mm",
}

#: Sentinels used across IMD archives for "no observation". Coercing any of
#: these to zero silently biases every mean and total.
MISSING_SENTINELS: Final[frozenset[float]] = frozenset(
    {-999.0, -99.9, 999.9, 9999.0, -9999.0, 99.9}
)

#: Textual markers for trace rainfall (< 0.1 mm). A real observation, not a
#: missing value: stored as 0.0 with ``trace_flag`` set.
TRACE_MARKERS: Final[frozenset[str]] = frozenset({"t", "tr", "trace"})

#: IMD's rainfall day runs 0830 IST to 0830 IST, so a "daily" total is not a
#: midnight-to-midnight figure. Reported alongside every daily aggregate.
RAINFALL_DAY_START_IST = dt.time(8, 30)
RAINFALL_DAY_NOTE: Final[str] = "Rainfall day 0830-0830 IST"

IST_OFFSET: Final[dt.timedelta] = dt.timedelta(hours=5, minutes=30)


def ist_from_utc(when: dt.datetime) -> dt.datetime:
    """Satellite products are timestamped in UTC; forecasters read IST."""
    return when + IST_OFFSET


# --------------------------------------------------------------------------
# Departure from normal
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DepartureBand:
    label: str
    low: float  # inclusive, percent
    high: float  # inclusive, percent


#: Source: IMD standard rainfall departure categories. Confirm the cutoffs
#: against the current departmental circular before production use.
RAINFALL_DEPARTURE_BANDS: Final[tuple[DepartureBand, ...]] = (
    DepartureBand("Large Excess", 60.0, float("inf")),
    DepartureBand("Excess", 20.0, 59.999),
    DepartureBand("Normal", -19.0, 19.999),
    DepartureBand("Deficient", -59.0, -19.001),
    DepartureBand("Large Deficient", -99.0, -59.001),
    DepartureBand("No Rain", -100.0, -99.001),
)

NORMALS_PERIOD: Final[str] = "1991-2020"


def departure_category(departure_pct: float | None) -> str | None:
    """Map a percentage departure from normal onto IMD's published category."""
    if departure_pct is None:
        return None
    for band in RAINFALL_DEPARTURE_BANDS:
        if band.low <= departure_pct <= band.high:
            return band.label
    return None


def season_for_month(month: int) -> Season | None:
    for season in SEASONS:
        if month in season.months:
            return season
    return None


def normalise_station_name(name: str) -> str:
    """Lower-case, strip site-type suffixes, and resolve former names."""
    cleaned = name.strip().lower()
    for suffix in STATION_TYPE_SUFFIXES:
        cleaned = cleaned.replace(suffix, " ")
    cleaned = " ".join(cleaned.replace("(", " ").replace(")", " ").split())
    return STATION_NAME_ALIASES.get(cleaned, cleaned)
