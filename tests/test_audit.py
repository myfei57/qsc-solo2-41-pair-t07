"""Audit trail, its filters and its summary."""

from __future__ import annotations

from tankfarm.domain import VALVE_VENT
from tankfarm.event import topics
from tankfarm.judgement.filters import RecordFilter


def test_published_event_creates_audit_entry(services):
    services.persist_valves()
    assert topics.VALVE_PERSISTED in services.audit.kinds()


def test_audit_query_filters_by_kind(services):
    services.persist_valves()
    selected = services.audit.select(RecordFilter(kinds=(topics.VALVE_PERSISTED,)))
    assert [entry.kind for entry in selected] == [topics.VALVE_PERSISTED]


def test_audit_query_filters_by_subject(services):
    services.open_valve(VALVE_VENT)
    selected = services.audit.select(RecordFilter(subject=VALVE_VENT))
    assert selected
    assert all(entry.subject == VALVE_VENT for entry in selected)


def test_audit_summary_counts_kinds_and_subjects(services):
    services.persist_valves()
    summary = services.audit_summary()
    assert summary.total >= 2
    assert summary.by_kind[topics.VALVE_PERSISTED] >= 1
    assert summary.by_kind[topics.SEQUENCE_STAGE] >= 1
    assert summary.latest is not None
    assert summary.latest.kind == topics.SEQUENCE_STAGE


def test_audit_entries_are_written_to_disk(services):
    services.persist_valves()
    lines = (
        services.repository.layout.audit_file().read_text(encoding="utf-8").splitlines()
    )
    assert len(lines) == services.audit_summary().total


def test_audit_log_restores_entries_after_restart(services, restart):
    services.persist_valves()
    total = services.audit_summary().total
    fresh = restart()
    assert fresh.audit_summary().total == total


def test_audit_subjects_list_distinct_subjects(services):
    services.open_valve(VALVE_VENT)
    services.persist_valves()
    assert VALVE_VENT in services.audit.subjects()


def test_audit_summary_counts_every_subject(services):
    services.open_valve("valve-vent")
    services.open_valve("valve-tank-new")
    summary = services.audit_summary()
    assert summary.by_subject["valve-vent"] >= 1
    assert summary.by_subject["valve-tank-new"] >= 1


def test_audit_filter_combines_kind_and_subject(services):
    services.open_valve("valve-vent")
    services.close_valve("valve-vent")
    services.open_valve("valve-tank-new")
    selected = services.audit.select(
        RecordFilter(kinds=(topics.VALVE_POSITION,), subject="valve-vent")
    )
    assert len(selected) == 2
    assert {entry.subject for entry in selected} == {"valve-vent"}


def test_repeated_actions_on_one_device_all_remain(services):
    services.open_valve(VALVE_VENT)
    services.close_valve(VALVE_VENT)
    services.open_valve(VALVE_VENT)
    selected = services.audit.select(RecordFilter(subject=VALVE_VENT))
    assert len(selected) == 3
    assert [entry.subject for entry in selected] == [VALVE_VENT] * 3


def test_audit_filter_combines_subject_kind_and_time_window(services):
    services.open_valve(VALVE_VENT)
    services.close_valve(VALVE_VENT)
    vent = services.audit.select(
        RecordFilter(kinds=(topics.VALVE_POSITION,), subject=VALVE_VENT)
    )
    assert len(vent) == 2

    services.tick_clock(10)
    services.open_valve("valve-tank-new")
    other = services.audit.select(RecordFilter(subject="valve-tank-new"))
    earliest_new = min(entry.ts for entry in other)
    last_vent = max(entry.ts for entry in vent)
    assert earliest_new > last_vent

    # all three dimensions narrow the same set
    selected = services.audit.select(
        RecordFilter(
            kinds=(topics.VALVE_POSITION,),
            subject=VALVE_VENT,
            start_ts=min(entry.ts for entry in vent),
            end_ts=last_vent,
        )
    )
    assert len(selected) == 2
    assert {entry.subject for entry in selected} == {VALVE_VENT}

    only_later = services.audit.select(RecordFilter(start_ts=earliest_new))
    assert {entry.subject for entry in only_later} == {"valve-tank-new"}


def test_audit_restart_keeps_every_repeated_action(services, restart):
    services.open_valve(VALVE_VENT)
    services.close_valve(VALVE_VENT)
    services.open_valve(VALVE_VENT)
    fresh = restart()
    restored = fresh.audit.select(RecordFilter(subject=VALVE_VENT))
    assert len(restored) == 3
    # entries appended after restart keep strictly increasing sequence numbers
    fresh.tick_clock(1)
    fresh.close_valve(VALVE_VENT)
    final = fresh.audit.select(RecordFilter(subject=VALVE_VENT))
    assert len(final) == 4
    assert [entry.seq for entry in final] == sorted(entry.seq for entry in final)
