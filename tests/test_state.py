import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from models import Availability, IN_STOCK, OUT_OF_STOCK, UNKNOWN  # noqa: E402
from state import StateStore  # noqa: E402


def av(status, qty=None, dist=10.0, key="s1"):
    return Availability("Castorama", "P", "ean", key, "Casto X", "Paris", status,
                        quantity=qty, distance_km=dist)


def run(st, r, **kw):
    out = st.update([r], **kw)
    st.mark_notified(out)
    return out


def store(tmp_path):
    return StateStore(tmp_path / "s.json")


def test_first_stock_notifies(tmp_path):
    st = store(tmp_path)
    assert run(st, av(IN_STOCK, 5)) and not run(st, av(IN_STOCK, 5))


def test_far_store_silent(tmp_path):
    st = store(tmp_path)
    assert run(st, av(IN_STOCK, 5, dist=400), max_notify_km=150) == []
    assert run(st, av(IN_STOCK, 20, dist=400), max_notify_km=150) == []


def test_online_always_eligible(tmp_path):
    st = store(tmp_path)
    assert run(st, av(IN_STOCK, None, dist=None, key="web"), max_notify_km=150)


def test_small_restock_accumulates(tmp_path):
    st = store(tmp_path)
    run(st, av(IN_STOCK, 10), min_restock_delta=3)
    assert run(st, av(IN_STOCK, 11), min_restock_delta=3) == []
    assert run(st, av(IN_STOCK, 12), min_restock_delta=3) == []
    out = run(st, av(IN_STOCK, 13), min_restock_delta=3)
    assert out and out[0].restock and out[0].delta == 3


def test_single_oos_does_not_rearm(tmp_path):
    st = store(tmp_path)
    run(st, av(IN_STOCK, 12))
    run(st, av(OUT_OF_STOCK, 0))
    assert run(st, av(IN_STOCK, 12)) == []


def test_two_oos_rearm(tmp_path):
    st = store(tmp_path)
    run(st, av(IN_STOCK, 12))
    run(st, av(OUT_OF_STOCK, 0))
    run(st, av(OUT_OF_STOCK, 0))
    assert run(st, av(IN_STOCK, 12))


def test_unknown_keeps_state(tmp_path):
    st = store(tmp_path)
    run(st, av(IN_STOCK, 12))
    run(st, av(UNKNOWN))
    run(st, av(UNKNOWN))
    assert run(st, av(IN_STOCK, 12)) == []


def test_quiet_hours_and_cooldown():
    import clim_watch as cw
    tz = ZoneInfo("Europe/Paris")
    q = {"enabled": True, "start": "22:00", "end": "07:00", "timezone": "Europe/Paris"}
    assert cw._in_quiet_hours(q, datetime(2026, 10, 14, 23, 0, tzinfo=tz))
    assert cw._in_quiet_hours(q, datetime(2026, 10, 14, 6, 59, tzinfo=tz))
    assert not cw._in_quiet_hours(q, datetime(2026, 10, 14, 7, 0, tzinfo=tz))
    noon = datetime(2026, 10, 14, 12, 0, tzinfo=tz)
    cfg = {"quiet_hours": q, "notify_cooldown_minutes": 60, "cooldown_bypass_km": 50}
    far, near = [av(IN_STOCK, 5, dist=120)], [av(IN_STOCK, 5, dist=20)]
    assert cw.should_push(cfg, far, 1000, 1000 + 600, noon)[0] is False
    assert cw.should_push(cfg, near, 1000, 1000 + 600, noon)[0] is True
    assert cw.should_push(cfg, far, 1000, 1000 + 3600, noon)[0] is True
