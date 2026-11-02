from alembic.config import Config
from sqlalchemy import inspect, select

from alembic import command
from app.models import Farmer, ProgrammeParticipation


def test_models_match_migrations() -> None:
    """Fails if a model change was made without an Alembic migration."""
    command.check(Config("alembic.ini"))


def test_key_constraints_exist(db) -> None:
    insp = inspect(db.get_bind())

    def unique_sets(table: str) -> set[tuple[str, ...]]:
        # SQL Server reports unique constraints as unique indexes.
        return {tuple(i["column_names"]) for i in insp.get_indexes(table) if i.get("unique")}

    assert ("programme_id", "farmer_id") in unique_sets("programme_participations")
    assert ("source_id", "entity_type", "source_record_id") in unique_sets("source_record_maps")
    fks = {fk["referred_table"] for fk in insp.get_foreign_keys("monitoring_records")}
    assert {"indicator_definitions", "programmes", "geo_units", "farm_plots", "integration_sources"} <= fks


def test_duplicate_enrolment_rejected_by_database(db) -> None:
    import pytest
    from sqlalchemy.exc import IntegrityError
    existing = db.scalars(select(ProgrammeParticipation)).first()
    db.add(ProgrammeParticipation(programme_id=existing.programme_id, farmer_id=existing.farmer_id))
    with pytest.raises(IntegrityError):
        db.flush()


def test_demo_data_is_flagged(db) -> None:
    assert all(f.is_demo for f in db.scalars(select(Farmer)))
