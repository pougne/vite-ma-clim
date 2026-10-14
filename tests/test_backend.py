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
    a = {"k": {"status": "in_stock", "checked_at": "1"}, "__health__": {"C": {"streak": 1, "last_ok_ts": 1}}}
    b = {"k": {"status": "in_stock", "checked_at": "2"}, "__health__": {"C": {"streak": 1, "last_ok_ts": 9}}}
    assert sc.strip(a) == sc.strip(b)
    b["__health__"]["C"]["streak"] = 2               # le compteur de panne compte
    assert sc.strip(a) != sc.strip(b)
    b["__health__"]["C"]["streak"] = 1
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


def _load(monkeypatch, tmp_path, env, yaml_txt="notifications:\n  email:\n    enabled: false\n"):
    import clim_watch as cw
    for k in ("VMC_SMTP_HOST", "VMC_SMTP_USER", "VMC_SMTP_PASSWORD", "VMC_SMTP_FROM", "VMC_SMTP_TO"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    p = tmp_path / "c.yaml"
    p.write_text(yaml_txt, encoding="utf-8")
    return cw.load_config(p)["notifications"]["email"]


def test_email_env_complete(monkeypatch, tmp_path):
    em = _load(monkeypatch, tmp_path, {"VMC_SMTP_USER": "u@x", "VMC_SMTP_PASSWORD": "pw",
                                       "VMC_SMTP_FROM": "f@x", "VMC_SMTP_TO": "a@x, b@x"})
    assert em["enabled"] and em["to_addrs"] == ["a@x", "b@x"] and em["from_addr"] == "f@x"


def test_email_env_missing_to_skips_placeholders(monkeypatch, tmp_path):
    yaml_txt = ("notifications:\n  email:\n    enabled: false\n"
                "    from_addr: ton.adresse@gmail.com\n    to_addrs: [ton.adresse@gmail.com]\n")
    em = _load(monkeypatch, tmp_path, {"VMC_SMTP_USER": "u@x", "VMC_SMTP_PASSWORD": "pw",
                                       "VMC_SMTP_FROM": "f@x"}, yaml_txt)
    assert em["enabled"] is False


def test_email_placeholder_config_disabled(monkeypatch, tmp_path):
    yaml_txt = ("notifications:\n  email:\n    enabled: true\n    user: ton.adresse@gmail.com\n"
                "    from_addr: ton.adresse@gmail.com\n    to_addrs: [ton.adresse@gmail.com]\n")
    assert _load(monkeypatch, tmp_path, {}, yaml_txt)["enabled"] is False


def test_outage_alert_fires_across_ci_runs(tmp_path):
    """Simule GitHub Actions : chaque run repart de l'état COMMITTÉ, qui n'est
    mis à jour que si state_changed le juge utile. L'alerte doit partir au seuil."""
    import json
    import shutil
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import state_changed as sc
    committed = tmp_path / "committed.json"
    committed.write_text("{}", encoding="utf-8")
    work = tmp_path / "state.json"
    alerts = []
    silent = [Availability("Castorama", "P", "ean", "en-ligne", "x", "", UNKNOWN)]
    for _ in range(5):
        shutil.copy(committed, work)                 # checkout
        st = StateStore(work)
        broken, _ = st.health_update(["Castorama"], silent)
        alerts += broken
        st.save()
        old = json.loads(committed.read_text(encoding="utf-8"))
        new = json.loads(work.read_text(encoding="utf-8"))
        if sc.strip(old) != sc.strip(new):          # « commit » seulement si utile
            shutil.copy(work, committed)
    assert alerts == ["Castorama"]
    h = json.loads(committed.read_text(encoding="utf-8"))["__health__"]["Castorama"]
    assert h["streak"] == StateStore.HEALTH_THRESHOLD and h["alerted"]


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
