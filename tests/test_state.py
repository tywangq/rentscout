from rentscout.models import Listing
from rentscout.state import Store


def mk(source_id: str, price: int) -> Listing:
    return Listing(
        id=f"test:{source_id}",
        source="test",
        url="",
        address=f"{source_id} St",
        neighborhood="Fremont",
        price=price,
        beds=1,
        baths=1,
        sqft=500,
        description="",
        available="",
    )


def test_reconcile_lifecycle(store: Store):
    a, b = mk("a", 1000), mk("b", 2000)

    day1 = store.reconcile("2026-08-01", [a, b], snapshot=True)
    assert [l.id for l in day1.new] == ["test:a", "test:b"]

    # unchanged listing is not fresh; price change is tracked with old price
    day2 = store.reconcile("2026-08-02", [a, mk("b", 1900)], snapshot=True)
    assert day2.new == [] and day2.fresh == []
    assert [(l.id, old) for l, old in day2.price_changed] == [("test:b", 2000)]
    assert store.price_history("test:b") == [
        ("2026-08-01", 2000),
        ("2026-08-02", 1900),
    ]

    # missing from a snapshot feed = delisted
    day3 = store.reconcile("2026-08-03", [a], snapshot=True)
    assert [l.id for l in day3.delisted] == ["test:b"]

    # back again = relisted, and fresh again
    day4 = store.reconcile("2026-08-04", [a, mk("b", 1900)], snapshot=True)
    assert [l.id for l in day4.relisted] == ["test:b"]
    assert [l.id for l in day4.fresh] == ["test:b"]


def test_non_snapshot_source_never_delists(store: Store):
    store.reconcile("2026-08-01", [mk("a", 1000)], snapshot=False)
    day2 = store.reconcile("2026-08-02", [], snapshot=False)
    assert day2.delisted == []


def test_feedback_and_rejections(store: Store):
    store.add_feedback("test:a", "down", "too far")
    store.add_feedback("test:b", "up", "nice")
    assert store.rejected_ids() == {"test:a"}


def test_month_spend_sums_by_run_date(store: Store):
    store.record_spend("r1", "2026-08-01", "llm", 0.01)
    store.record_spend("r2", "2026-08-15", "llm", 0.02)
    store.record_spend("r3", "2026-09-01", "llm", 0.04)
    assert abs(store.month_spend("2026-08") - 0.03) < 1e-9
    assert abs(store.month_spend("2026-09") - 0.04) < 1e-9
