"""FarmFuture soil N/P/K readings are in kg/ha

The FarmFuture web app labels its soil nitrogen, phosphorus and potassium in kg/ha (reviewed 6 Nov 2026); readings
imported earlier carried the provisional unit mg/kg. The numbers are unchanged – only the unit label is corrected, on
sensor observations imported by the FarmFuture soil & microclimate connector. Data only; no schema change.

Revision ID: 7d3e9a1c4b20
Revises: 5c1f0a7e2b9d
Create Date: 2026-11-06 16:00:00.000000

"""
import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '7d3e9a1c4b20'
down_revision: Union[str, Sequence[str], None] = '5c1f0a7e2b9d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NUTRIENTS = ("nitrogen", "phosphorus", "potassium")


def _relabel(old: str, new: str) -> None:
    bind = op.get_bind()
    rows = bind.execute(sa.text(
        "SELECT r.id, r.attributes FROM field_records r JOIN integration_sources s ON s.id = r.source_system_id "
        "WHERE r.record_type = 'sensor_observation' AND r.unit = :old "
        "AND s.adapter_code = 'farmfuture_soil_microclimate'"), {"old": old}).all()
    for record_id, raw in rows:
        attributes = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
        if attributes.get("parameter") not in NUTRIENTS or attributes.get("unit") != old:
            continue
        attributes["unit"] = new
        bind.execute(sa.text("UPDATE field_records SET unit = :new, attributes = :attributes WHERE id = :id"),
                     {"new": new, "attributes": json.dumps(attributes, ensure_ascii=False), "id": record_id})


def _drop_confirmed_units() -> None:
    """Remove the units the connector now fixes (EC, N/P/K) from saved source settings, so the settings shown on screen
    no longer list the old provisional values."""
    bind = op.get_bind()
    rows = bind.execute(sa.text(
        "SELECT id, config FROM integration_sources WHERE adapter_code = 'farmfuture_soil_microclimate'")).all()
    for source_id, raw in rows:
        config = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
        units = config.get("units")
        if not isinstance(units, dict) or not any(k in units for k in ("ec", *NUTRIENTS)):
            continue
        config["units"] = {k: v for k, v in units.items() if k not in ("ec", *NUTRIENTS)}
        bind.execute(sa.text("UPDATE integration_sources SET config = :config WHERE id = :id"),
                     {"config": json.dumps(config, ensure_ascii=False), "id": source_id})


def upgrade() -> None:
    _relabel("mg/kg", "kg/ha")
    _drop_confirmed_units()


def downgrade() -> None:
    # Source settings are not restored: the connector ignores EC / N/P/K units in settings either way.
    _relabel("kg/ha", "mg/kg")
