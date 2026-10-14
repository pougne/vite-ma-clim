import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from models import Availability, IN_STOCK, UNKNOWN  # noqa: E402
from state import StateStore  # noqa: E402


def av(retailer="Castorama", key="s1", qty=5, ts=None):
    a = Availability(retailer, "P", "ean", key, "X", "", IN_STOCK, quantity=qty)
    if ts:
        a.checked_at = ts
    return a


def test_purge_disabled_and_old(tmp_path):
    st = StateStore(tmp_path / "s.json")
    st.update([av(), av("Leroy Merlin", "lm"), av(key="old", ts="2020-01-01T00:00:00+00:00")])
    st.health_update(["Castorama", "Leroy Merlin"], [])
    removed = st.purge(["Castorama"])
    assert set(removed) == {"Leroy Merlin|ean|lm", "Castorama|ean|old"}
    assert "Castorama|ean|s1" in st.snapshot()
    assert "Leroy Merlin" not in st.health()


def test_national_point_only_on_change_and_skip(tmp_path):
    st = StateStore(tmp_path / "s.json")
    st.history_update([av(qty=5)], 1000)
    st.history_update([av(qty=5)], 1600)          # inchangé, < 1 h -> pas de point
    st.history_update([av(qty=7)], 2000)
    st.history_update([av(qty=9)], 2600, skip_national=True)
    assert [v for _, v in st.history()["national"]] == [5, 7]
    st.history_update([av(qty=7)], 2000 + 3600)   # témoin horaire
    assert len(st.history()["national"]) == 3


def test_health_last_ok(tmp_path):
    st = StateStore(tmp_path / "s.json")
    st.health_update(["Castorama"], [av()], now_ts=123)
    assert st.health()["Castorama"]["last_ok_ts"] == 123


def test_state_changed_ignores_volatile():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import state_changed as sc
    a = {"k": {"status": "in_stock", "checked_at": "1"}, "__health__": {"C": {"streak": 1}}}
    b = {"k": {"status": "in_stock", "checked_at": "2"}, "__health__": {"C": {"streak": 9}}}
    assert sc.strip(a) == sc.strip(b)
    b["k"]["status"] = "out_of_stock"
    assert sc.strip(a) != sc.strip(b)


def test_optimea_parse():
    from checkers.optimea import OptimeaChecker
    c = OptimeaChecker({})
    html = '<meta property="product:availability" content="in stock"><meta property="product:price:amount" content="699">'
    r = c.parse_html(html, "L", "ref", "u")
    assert r.status == IN_STOCK and "699" in r.detail
    assert c.parse_html("<html></html>", "L", "ref", "u").status == UNKNOWN


def test_castorama_without_token(monkeypatch):
    from checkers.castorama import CastoramaChecker
    monkeypatch.delenv("VMC_CASTO_TOKEN", raising=False)
    c = CastoramaChecker({})
    res = c.check(None, [{"ref": "123_CAFR", "url": "u"}], [])
    assert c.partial and res[0].status == UNKNOWN


def test_email_env(monkeypatch, tmp_path):
    import clim_watch as cw
    p = tmp_path / "c.yaml"
    p.write_text("notifications:\n  email:\n    enabled: false\n", encoding="utf-8")
    monkeypatch.setenv("VMC_SMTP_USER", "u@x")
    monkeypatch.setenv("VMC_SMTP_PASSWORD", "pw")
    monkeypatch.setenv("VMC_SMTP_TO", "a@x, b@x")
    em = cw.load_config(p)["notifications"]["email"]
    assert em["enabled"] and em["to_addrs"] == ["a@x", "b@x"] and em["from_addr"] == "u@x"


def test_partial_pass_marks_unknown_and_skips_national(monkeypatch, tmp_path):
    import clim_watch as cw
    st = StateStore(tmp_path / "s.json")
    st.update([av(key="a"), av(key="b")])
    st.history_update([av(key="a"), av(key="b")], 1)

    def fake(cfg, h):
        cw.PARTIAL_RETAILERS.clear(); cw.PARTIAL_RETAILERS.add("Castorama")
        return [av(key="a", qty=6)], ["Castorama"]
    monkeypatch.setattr(cw, "run_checks", fake)
    monkeypatch.setattr(cw, "_sources_info", lambda *a, **k: {})
    seen = {}
    monkeypatch.setattr(cw.dashboard, "render", lambda res, *a, **k: seen.setdefault("r", res))
    monkeypatch.setattr(cw.notify, "dispatch", lambda *a: None)
    monkeypatch.setattr(cw, "HERE", tmp_path)
    cw.one_pass({"notifications": {"ntfy": {}}}, st, False, False)
    assert st.snapshot()["Castorama|ean|b"]["status"] == UNKNOWN
    assert len(st.history()["national"]) == 1
    assert any(r.store_key == "b" and r.status == UNKNOWN for r in seen["r"])
