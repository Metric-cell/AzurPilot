-- Current fleet ledger. Each row in ships is one card, including duplicate names.
CREATE TABLE ships (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    position INTEGER NOT NULL UNIQUE CHECK(position >= 0),
    group_type INTEGER,
    name TEXT NOT NULL,
    level INTEGER NOT NULL CHECK(level BETWEEN 0 AND 125),
    rarity TEXT NOT NULL,
    base_rarity TEXT NOT NULL,
    extra_exp INTEGER CHECK(extra_exp >= 0),
    review INTEGER NOT NULL CHECK(review IN (0, 1)),
    source TEXT NOT NULL
) STRICT;

-- The sole scan descriptor, also holding a few application settings.
CREATE TABLE fleet_info (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    revision TEXT,
    updated_at TEXT NOT NULL DEFAULT '',
    min_level INTEGER NOT NULL DEFAULT 1 CHECK(min_level BETWEEN 1 AND 125),
    max_level INTEGER NOT NULL DEFAULT 125 CHECK(max_level BETWEEN min_level AND 125),
    reached_bottom INTEGER NOT NULL DEFAULT 0 CHECK(reached_bottom IN (0, 1)),
    filters_clear INTEGER NOT NULL DEFAULT 0 CHECK(filters_clear IN (0, 1)),
    physical_count INTEGER NOT NULL DEFAULT 0 CHECK(physical_count >= 0),
    expected_count INTEGER CHECK(expected_count >= 0),
    complete INTEGER NOT NULL DEFAULT 0 CHECK(complete IN (0, 1)),
    review_count INTEGER NOT NULL DEFAULT 0 CHECK(review_count >= 0),
    imported_at TEXT,
    mind_fields INTEGER NOT NULL DEFAULT 0,
    mind_updated_at TEXT,
    mind_snapshot_revision TEXT,
    technology_fields INTEGER NOT NULL DEFAULT 0
) STRICT;

-- One row per ship TYPE. NULL manual columns may mean an explicit blank, so
-- manual_fields records which overrides are present: bit 0 = manual row exists,
-- bits 1..5 = owned, full_star, lv120, level, level_toggle.
CREATE TABLE technology (
    group_type TEXT PRIMARY KEY NOT NULL,
    max_observed_level INTEGER CHECK(max_observed_level BETWEEN 1 AND 125),
    manual_fields INTEGER NOT NULL DEFAULT 0,
    owned INTEGER CHECK(owned IN (0, 1)),
    full_star INTEGER CHECK(full_star IN (0, 1)),
    lv120 INTEGER CHECK(lv120 IN (0, 1)),
    level INTEGER CHECK(level BETWEEN 1 AND 125),
    low_level INTEGER CHECK(low_level BETWEEN 1 AND 119),
    high_level INTEGER CHECK(high_level BETWEEN 120 AND 125)
) STRICT;

CREATE TABLE nation_technology (
    nation_key TEXT PRIMARY KEY NOT NULL,
    level INTEGER CHECK(level >= 0)
) STRICT;

-- Optional mind edits, exclusions and additional entries share one typed table.
-- fields records which columns override automatic values, in MIND_FIELDS order.
CREATE TABLE mind_settings (
    kind TEXT NOT NULL CHECK(kind IN ('override', 'removed', 'extra')),
    ship_key TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
    fields INTEGER NOT NULL DEFAULT 0,
    name TEXT,
    level INTEGER CHECK(level BETWEEN 0 AND 125),
    rarity TEXT,
    base_rarity TEXT,
    excluded INTEGER CHECK(excluded IN (0, 1)),
    review INTEGER CHECK(review IN (0, 1)),
    source TEXT,
    PRIMARY KEY(kind, ship_key)
) STRICT;
