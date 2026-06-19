"""Dashboard « Vite Ma Clim » — design inspiré de Vite Ma Dose (CovidTracker).

Palette officielle ViteMaDose : primaire #5561d9, secondaire #ed505b,
succès #6db455, fond clair, fiches arrondies façon "centres".
"""
from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from pathlib import Path

from models import Availability, IN_STOCK, OUT_OF_STOCK

APP_NAME = "Vite Ma Clim"


def _dist(r: Availability) -> str:
    return "" if r.distance_km is None else f"{r.distance_km:.0f} km"


def _sort_key(r: Availability):
    return (r.status != IN_STOCK,
            r.distance_km if r.distance_km is not None else 1e9,
            r.retailer, r.store_name)


def _gmaps(lat, lon) -> str:
    return f"https://www.google.com/maps/dir/?api=1&destination={lat},{lon}"


def _detail_pills(detail: str) -> str:
    if not detail or detail == "—":
        return ""
    out = []
    for seg in detail.split("·"):
        seg = seg.strip()
        if not seg:
            continue
        good = any(k in seg for k in ("Available", "InStock", "achetable"))
        cls = "tag tag--good" if good else "tag tag--mut"
        out.append(f'<span class="{cls}">{html.escape(seg)}</span>')
    return f'<div class="tags">{"".join(out)}</div>'


def _card(r: Availability) -> str:
    ok = r.status == IN_STOCK
    if ok:
        statetag = '<span class="state state--ok">Disponible</span>'
    elif r.status == OUT_OF_STOCK:
        statetag = '<span class="state state--no">Indisponible</span>'
    else:
        statetag = '<span class="state state--unk">Inconnu</span>'
    dist = _dist(r)
    dist_html = f'<span class="km">{dist}</span>' if dist else ""
    btns = []
    if r.url:
        btns.append(f'<a class="btn btn--primary" href="{html.escape(r.url)}" target="_blank">Voir la fiche</a>')
    if r.lat is not None and r.lon is not None:
        btns.append(f'<a class="btn btn--ghost" href="{_gmaps(r.lat, r.lon)}" target="_blank">Itinéraire</a>')
    return (
        f'<article class="card{" card--ok" if ok else ""}">'
        f'<div class="card-head"><span class="badge {("b-casto" if r.retailer.lower().startswith("casto") else "b-boul")}">{html.escape(r.retailer)}</span>{dist_html}</div>'
        f'<h3 class="card-title">{html.escape(r.store_name)}</h3>'
        f'<div class="card-city">{html.escape(r.store_city or "")}</div>'
        f'{statetag}'
        f'{_detail_pills(r.detail)}'
        f'<div class="card-btns">{"".join(btns)}</div>'
        f'</article>'
    )


def _map_section(results, home) -> str:
    points = [
        {"lat": r.lat, "lon": r.lon, "name": r.store_name, "city": r.store_city,
         "dist": (round(r.distance_km) if r.distance_km is not None else None),
         "status": r.status, "url": r.url, "detail": r.detail}
        for r in results if r.lat is not None and r.lon is not None
    ]
    if not points:
        return ""
    script = """
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
(function(){
  var pts = __POINTS__, home = __HOME__;
  var map = L.map('map', {scrollWheelZoom:false});
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
    {maxZoom:19, attribution:'© OpenStreetMap'}).addTo(map);
  var hb = [], all = [];
  pts.forEach(function(p){
    var ok = p.status === 'in_stock';
    var m = L.circleMarker([p.lat, p.lon], {
      radius: ok ? 9 : 4, color: ok ? '#4a9e34' : '#b9c0cf',
      weight: ok ? 2 : 1, fillColor: ok ? '#6db455' : '#cdd3df',
      fillOpacity: ok ? 0.95 : 0.6
    }).addTo(map);
    var d = (p.dist!=null? p.dist+' km · ':'');
    var dir = 'https://www.google.com/maps/dir/?api=1&destination='+p.lat+','+p.lon;
    m.bindPopup('<b>'+p.name+'</b><br>'+d+(p.city||'')+'<br>'+(p.detail||'')
      +'<br><a href="'+p.url+'" target="_blank">fiche</a> · '
      +'<a href="'+dir+'" target="_blank">itinéraire</a>');
    all.push([p.lat,p.lon]); if(ok) hb.push([p.lat,p.lon]);
  });
  if(home && home.lat){
    L.marker([home.lat, home.lon]).addTo(map).bindPopup('Chez vous'); hb.push([home.lat,home.lon]);
  }
  var b = (hb.length ? hb : all);
  if(b.length===1) map.setView(b[0],11); else map.fitBounds(b,{padding:[30,30]});
})();
</script>
"""
    script = script.replace("__POINTS__", json.dumps(points, ensure_ascii=False))
    script = script.replace("__HOME__", json.dumps(home or {}))
    return '<div id="map"></div>' + script


def render(results: list[Availability], out_path: str | Path, home: dict | None = None) -> Path:
    results = sorted(results, key=_sort_key)
    n_dispo = sum(1 for r in results if r.status == IN_STOCK)
    n_total = len(results)
    now = datetime.now(timezone.utc).strftime("%d/%m/%Y à %H:%M UTC")
    map_html = _map_section(results, home)
    cards = "\n".join(_card(r) for r in results) or '<p class="empty">Aucune donnée.</p>'

    headline = (f"{n_dispo} point{'s' if n_dispo > 1 else ''} de vente "
                f"propose{'nt' if n_dispo > 1 else ''} le PortaSplit&nbsp;!"
                if n_dispo else "Aucune disponibilité pour le moment")
    sub = ("Cliquez sur une fiche disponible pour réserver." if n_dispo
           else f"Surveillance active sur {n_total} points de vente. Vous serez notifié dès qu'une dispo apparaît.")

    doc = f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="120">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<title>{APP_NAME} · Midea PortaSplit</title>
<style>
  :root {{
    --primary:#5561d9; --primary-dark:#414dc4; --primary-tint:#edeefb;
    --secondary:#ed505b; --success:#6db455; --success-dark:#4a9e34; --success-tint:#eef6e9;
    --bg:#f4f5fb; --card:#ffffff; --line:#e7e9f2; --txt:#23262f; --mut:#737a8c;
    font-family:-apple-system,'Segoe UI','Helvetica Neue',Arial,sans-serif;
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--txt); }}
  .topbar {{ background:#fff; border-bottom:1px solid var(--line); }}
  .topbar .in {{ max-width:1100px; margin:0 auto; padding:16px 20px; display:flex;
                 align-items:center; justify-content:space-between; }}
  .logo {{ font-size:22px; font-weight:800; letter-spacing:-.5px; }}
  .logo .a {{ color:var(--txt); }} .logo .b {{ color:var(--primary); }}
  .topbar .upd {{ color:var(--mut); font-size:12px; }}
  .hero {{ max-width:1100px; margin:0 auto; padding:30px 20px 8px; }}
  .hero h1 {{ font-size:26px; margin:0 0 6px; font-weight:800; letter-spacing:-.4px; }}
  .hero p {{ margin:0; color:var(--mut); font-size:15px; }}
  .hero h1 .hl {{ color:var(--success-dark); }}
  .chips {{ display:flex; gap:10px; max-width:1100px; margin:18px auto 0; padding:0 20px; flex-wrap:wrap; }}
  .chip {{ background:#fff; border:1px solid var(--line); border-radius:14px; padding:12px 16px;
           font-size:13px; color:var(--mut); box-shadow:0 1px 2px rgba(20,23,40,.04); }}
  .chip b {{ display:block; font-size:20px; color:var(--txt); }}
  .chip.go b {{ color:var(--success-dark); }}
  #map {{ height:360px; max-width:1100px; margin:20px auto 0; border-radius:18px;
          border:1px solid var(--line); box-shadow:0 2px 10px rgba(20,23,40,.05); }}
  .leaflet-popup-content {{ font-size:13px; }}
  .grid {{ max-width:1100px; margin:24px auto 64px; padding:0 20px;
           display:grid; grid-template-columns:repeat(auto-fill,minmax(280px,1fr)); gap:16px; }}
  .card {{ background:var(--card); border:1px solid var(--line); border-radius:18px; padding:18px;
           box-shadow:0 2px 8px rgba(20,23,40,.05); display:flex; flex-direction:column; gap:8px; }}
  .card--ok {{ border:1.5px solid var(--success); box-shadow:0 4px 18px rgba(109,180,85,.18); }}
  .card-head {{ display:flex; align-items:center; justify-content:space-between; }}
  .km {{ color:var(--mut); font-size:13px; font-weight:700; font-variant-numeric:tabular-nums; }}
  .badge {{ padding:3px 10px; border-radius:8px; font-size:11px; font-weight:800; color:#fff; letter-spacing:.3px; }}
  .b-casto {{ background:#0a8a3f; }} .b-boul {{ background:#e2541d; }}
  .card-title {{ font-size:16px; margin:2px 0 0; font-weight:700; }}
  .card-city {{ color:var(--mut); font-size:13px; }}
  .state {{ align-self:flex-start; padding:5px 12px; border-radius:999px; font-size:12px; font-weight:800; }}
  .state--ok {{ background:var(--success-tint); color:var(--success-dark); }}
  .state--no {{ background:#f1f2f6; color:#8b92a3; }}
  .state--unk {{ background:#fdf2e2; color:#c77d22; }}
  .tags {{ display:flex; flex-wrap:wrap; gap:6px; }}
  .tag {{ padding:3px 9px; border-radius:7px; font-size:11px; font-weight:600; }}
  .tag--good {{ background:var(--success-tint); color:var(--success-dark); }}
  .tag--mut {{ background:#f1f2f6; color:#8b92a3; }}
  .card-btns {{ display:flex; gap:8px; margin-top:6px; }}
  .btn {{ flex:1; text-align:center; padding:9px 12px; border-radius:11px; font-size:13px;
          font-weight:700; text-decoration:none; }}
  .btn--primary {{ background:var(--primary); color:#fff; }}
  .btn--primary:hover {{ background:var(--primary-dark); }}
  .btn--ghost {{ background:#fff; color:var(--primary); border:1.5px solid var(--primary-tint); }}
  .btn--ghost:hover {{ border-color:var(--primary); }}
  .empty {{ text-align:center; color:var(--mut); }}
  footer {{ text-align:center; color:var(--mut); font-size:12px; padding:0 20px 40px; }}
</style></head>
<body>
<div class="topbar"><div class="in">
  <div class="logo"><span class="a">Vite Ma </span><span class="b">Clim</span> ❄️</div>
  <div class="upd">Mis à jour le {now} · auto 2 min</div>
</div></div>

<div class="hero">
  <h1>{'<span class="hl">' + headline + '</span>' if n_dispo else headline}</h1>
  <p>{sub}</p>
</div>

<div class="chips">
  <div class="chip"><b>{n_total}</b>points de vente suivis</div>
  <div class="chip {'go' if n_dispo else ''}"><b>{n_dispo}</b>disponible{'s' if n_dispo != 1 else ''}</div>
  <div class="chip"><b>≈ 2 h</b>autour de chez vous & +</div>
</div>

{map_html}

<div class="grid">
{cards}
</div>

<footer>{APP_NAME} · données Castorama &amp; Boulanger · classé par distance depuis chez vous</footer>
</body></html>"""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(doc, encoding="utf-8")
    return out
