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


def test_far_store_without_qty_not_notified(tmp_path):
    st = store(tmp_path)
    r = Availability("Boulanger", "P", "ref", "x", "Boulanger Lyon", "Lyon", IN_STOCK,
                     quantity=None, distance_km=400)
    assert run(st, r, max_notify_km=150) == []


def test_distant_online_notified(tmp_path):
    st = store(tmp_path)
    r = Availability("Optimea", "P", "ref", "en-ligne", "En ligne", "—", IN_STOCK,
                     quantity=None, distance_km=None)
    assert run(st, r, max_notify_km=10)


def test_null_numeric_config():
    import clim_watch as cw
    assert cw._num(None, 1, 1) == 1
    assert cw._num("abc", 5) == 5
    assert cw._num(0, 1, 1) == 1
    assert cw._num("150", None) == 150.0


# ---------------- end-to-end one_pass (réseau / envoi mockés) ----------------

def _cfg(**ntfy):
    base = {"enabled": True, "topic_url": "https://ntfy.sh/x", "max_notify_km": 150,
            "min_restock_delta": None, "notify_cooldown_minutes": 60,
            "cooldown_bypass_km": 50, "high_priority_km": 50,
            "quiet_hours": {"enabled": True, "start": "22:00", "end": "07:00"}}
    base.update(ntfy)
    return {"notifications": {"ntfy": base}, "output": {"dashboard": "d.html"}}


def _setup(monkeypatch, tmp_path, results_seq, quiet_seq):
    import clim_watch as cw
    sent, health = [], []
    seq, qseq = iter(results_seq), iter(quiet_seq)
    flag = {"quiet": False}

    def fake_checks(cfg, h):
        flag["quiet"] = next(qseq)          # un état « nuit/jour » par passage
        return next(seq), ["Castorama"]
    monkeypatch.setattr(cw, "run_checks", fake_checks)
    monkeypatch.setattr(cw, "_in_quiet_hours", lambda q, now=None: flag["quiet"])
    monkeypatch.setattr(cw.notify, "dispatch", lambda c, res: sent.append(list(res)))
    monkeypatch.setattr(cw.notify, "notify_health", lambda c, b, r: health.append((b, r)))
    monkeypatch.setattr(cw.dashboard, "render", lambda *a, **k: None)
    monkeypatch.setattr(cw, "HERE", tmp_path)
    return cw, sent, health


def test_one_pass_quiet_hours_defers_then_groups(monkeypatch, tmp_path):
    a = av(IN_STOCK, 5, dist=100, key="a")
    b = av(IN_STOCK, 7, dist=120, key="b")
    # passage 1 : quiet (dispo a) ; passage 2 : quiet (a + b) ; passage 3 : jour
    cw, sent, _ = _setup(monkeypatch, tmp_path,
                         [[a], [a, b], [a, b]],
                         [True, True, False])
    st = StateStore(tmp_path / "s.json")
    cfg = _cfg()
    for _ in range(3):
        cw.one_pass(cfg, st, False, False)
    assert len(sent) == 1
    assert {r.store_key for r in sent[0]} == {"a", "b"}


def test_one_pass_cooldown_defers_far_but_not_near(monkeypatch, tmp_path):
    import time as _t
    far1 = av(IN_STOCK, 5, dist=120, key="f1")
    far2 = av(IN_STOCK, 5, dist=130, key="f2")
    near = av(IN_STOCK, 5, dist=10, key="n")
    cw, sent, _ = _setup(monkeypatch, tmp_path,
                         [[far1], [far1, far2], [far1, far2, near]],
                         [False] * 10)
    st = StateStore(tmp_path / "s.json")
    cfg = _cfg()
    cw.one_pass(cfg, st, False, False)          # push far1
    cw.one_pass(cfg, st, False, False)          # far2 en cooldown -> différé
    assert len(sent) == 1
    cw.one_pass(cfg, st, False, False)          # near contourne le cooldown, groupe far2
    assert len(sent) == 2
    assert {r.store_key for r in sent[1]} == {"f2", "n"}
    # après expiration du cooldown, rien de neuf -> rien
    st.set_last_push_ts(int(_t.time()) - 4000)


def test_one_pass_health_deferred_in_quiet_hours(monkeypatch, tmp_path):
    unk = Availability("Castorama", "P", "ean", "en-ligne", "x", "", UNKNOWN)
    ok = av(IN_STOCK, 5, dist=500, key="far")
    # 3 passages muets la nuit -> alerte panne différée ; 4e muet de jour -> envoyée
    cw, _, health = _setup(monkeypatch, tmp_path, [[unk]] * 4,
                           [True, True, True, False])
    # passage 3 : seuil atteint la nuit -> différé ; passage 4 de jour -> envoyé
    st = StateStore(tmp_path / "s.json")
    for _ in range(4):
        cw.one_pass(_cfg(), st, False, False)
    assert health == [(["Castorama"], [])]
    assert st.pending_health() == {}
