import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import dashboard  # noqa: E402
from models import Availability, IN_STOCK, OUT_OF_STOCK  # noqa: E402


def _r(name, dist, qty, status=IN_STOCK, lat=48.8, lon=2.4, key=None):
    return Availability("Castorama", "P", "e", key or name, name, "Créteil", status,
                        "Stock magasin : 3 pièces · Retrait 2h ✓", "https://x/f?a=1&b=2",
                        dist, lat, lon, quantity=qty)


def test_render_basics(tmp_path):
    evil = _r('Castorama <img src=x onerror=alert(1)>', 5, 4, key="evil")
    res = [_r("Castorama Créteil", 11, 14), evil, _r("Castorama Lyon", 390, 0, OUT_OF_STOCK),
           Availability("Optimea", "P", "r", "en-ligne", "En ligne", "—", IN_STOCK)]
    hist = {"stores": {"Castorama|e|Castorama Créteil": {"series": [[1, 10], [2, 14]]}},
            "national": [[1, 10], [2, 14]]}
    out = dashboard.render(res, tmp_path / "index.html", home={"lat": 48.85, "lon": 2.35},
                           history=hist, sources={"Castorama": "on", "Boulanger": "disabled",
                                                  "Optimea": "on"}, min_restock_delta=3)
    html = out.read_text(encoding="utf-8")
    # 10 : aucune balise injectée (ni carte, ni popup)
    assert "<img src=x" not in html
    # 13 : SRI Leaflet
    assert html.count('integrity="sha256-') == 2
    # 9 : version.json écrit, polling présent
    assert json.loads((tmp_path / "version.json").read_text())["ts"] > 0
    assert "version.json?_=" in html
    assert "setTimeout(reload" not in html and "++fails>=3" in html
    # 12 : source désactivée affichée comme telle et absente du pied de page
    assert "Boulanger · désactivé" in html
    footer = html[html.index("<footer>"):html.index("</footer>")]
    assert "Boulanger" not in footer and "Castorama" in footer
    # 12 : +4 < seuil 3 ? non -> +4 >= 3 -> Réassort
    assert "Réassort +4" in html
    # C/E/G
    assert "Le plus proche" in html and "Créteil" in html
    assert "3 magasins + 1 site" in html
    assert "content:'\\25BE'" in html          # flèche CSS non cassée
    assert 'id="radius"' in html and 'id="more"' in html
    # B : ordre carte -> courbe -> liste
    assert html.index('id="map"') < html.index('id="natchart"') < html.index('id="grid"')


def test_small_rise_is_en_hausse(tmp_path):
    hist = {"stores": {"Castorama|e|a": {"series": [[1, 10], [2, 11]]}}, "national": []}
    out = dashboard.render([_r("Castorama A", 5, 11, key="a")], tmp_path / "i.html",
                           history=hist, min_restock_delta=3)
    h = out.read_text(encoding="utf-8")
    assert "En hausse" in h and "Réassort +" not in h


def test_card_title_strips_retailer():
    assert dashboard._title(_r("Castorama Créteil", 1, 1)) == "Créteil"
