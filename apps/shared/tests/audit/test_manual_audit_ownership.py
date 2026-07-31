from __future__ import annotations

import pytest
from apps.shared.audit import listeners
from apps.shared.audit.manual_ownership import (
    is_manually_audited,
    register_manual_audit_ownership,
)
from sqlalchemy import Integer, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


class OwnedThing(Base):
    __tablename__ = "owned_things"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String)


@pytest.fixture
def session_factory(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(listeners, "TRACKED_MODELS", {OwnedThing: "owned_thing"})
    monkeypatch.setattr(listeners, "SENSITIVE_FIELDS", {OwnedThing: set()})
    listeners.register_audit_listeners()
    return sessionmaker(bind=engine)


def test_manual_create_suppresses_only_owned_object(monkeypatch, session_factory):
    calls = []
    monkeypatch.setattr(listeners, "record_audit", lambda **event: calls.append(event))
    session = session_factory()
    owned = OwnedThing(name="owned")
    unrelated = OwnedThing(name="unrelated")

    register_manual_audit_ownership(session, owned, "created")
    session.add_all([owned, unrelated])
    session.commit()

    assert [call["target_id"] for call in calls] == [unrelated.id]
    assert not is_manually_audited(session, owned, "created")


def test_manual_update_and_delete_do_not_emit_listener_duplicates(
    monkeypatch,
    session_factory,
):
    session = session_factory()
    thing = OwnedThing(name="initial")
    session.add(thing)
    session.commit()
    calls = []
    monkeypatch.setattr(listeners, "record_audit", lambda **event: calls.append(event))

    register_manual_audit_ownership(session, thing, "updated")
    thing.name = "updated"
    session.commit()
    register_manual_audit_ownership(session, thing, "deleted")
    session.delete(thing)
    session.commit()

    assert calls == []


def test_rollback_clears_manual_ownership(session_factory):
    session = session_factory()
    thing = OwnedThing(name="owned")
    register_manual_audit_ownership(session, thing, "created")
    session.add(thing)

    session.rollback()

    assert not is_manually_audited(session, thing, "created")


def test_soft_rollback_clears_manual_ownership(session_factory):
    session = session_factory()
    thing = OwnedThing(name="outer")
    session.add(thing)
    session.commit()

    nested = session.begin_nested()
    register_manual_audit_ownership(session, thing, "updated")
    thing.name = "inner"
    nested.rollback()

    assert not is_manually_audited(session, thing, "updated")


@pytest.mark.parametrize("operation", ["create", "remove", "invalid"])
def test_manual_ownership_rejects_unknown_operation(session_factory, operation):
    session = session_factory()
    with pytest.raises(ValueError):
        register_manual_audit_ownership(
            session,
            OwnedThing(name="owned"),
            operation,
        )
