"""Checker Castorama — API internes (Kingfisher), appels PARALLÉLISÉS par lots.

On charge la fiche produit une fois (origine + cookies), puis :
  1. UN appel page.evaluate qui interroge toutes les villes en parallèle (par
     lots) -> magasins proches + coordonnées.
  2. UN appel page.evaluate qui interroge la dispo de tous les magasins en
     parallèle (par lots) via l'API same-origin fulfilment-options.

La parallélisation par lots garde une charge raisonnable tout en divisant le
temps par ~10 vs. des appels séquentiels.
"""
from __future__ import annotations

import json
import math

from models import Availability, IN_STOCK, OUT_OF_STOCK, UNKNOWN
from .base import Checker, log

AVAILABLE = "Available"
STORES_BATCH = 8       # villes interrogées simultanément
FULFIL_BATCH = 12      # magasins interrogés simultanément

ATMOSPHERE_AUTH = ("Atmosphere atmosphere_app_id="
                   "kingfisher-o4ITR0sWAyCVQBraQf4Es61jHV3dN4oO9UwJQMrS")

# --- JS : magasins proches de chaque (lat,lon), en parallèle par lots --------
_STORES_BATCH_JS = """
async ([ean, auth, latlons, batchSize]) => {
  async function inBatches(items, size, fn) {
    const out = [];
    for (let i = 0; i < items.length; i += size) {
      out.push(...await Promise.all(items.slice(i, i + size).map(fn)));
    }
    return out;
  }
  function extract(j) {
    const out = [];
    for (const s of ((j && j.data) || [])) {
      if (s.type !== 'store') continue;
      const a = s.attributes || {}, st = a.store || {};
      const geo = (st.geoCoordinates || a.geoCoordinates || {});
      const c = geo.coordinates || {};
      let lat = (c.latitude != null ? c.latitude : geo.latitude);
      let lon = (c.longitude != null ? c.longitude : geo.longitude);
      out.push({
        id: String(s.id),
        name: a.name || st.name || null,
        cp: ((geo.postalCode || '') + '').trim(),
        lat: (lat != null ? Number(lat) : null),
        lon: (lon != null ? Number(lon) : null)
      });
    }
    return out;
  }
  return await inBatches(latlons, batchSize, async (ll) => {
    const u = `https://api.kingfisher.com/v1/mobile/stores/CAFR?nearLatLong=${ll[0]}%2C${ll[1]}`
            + `&page[size]=10&include=clickAndCollect,stock&filter[ean]=${ean}`;
    try {
      const r = await fetch(u, {credentials: 'include',
        headers: {'accept': 'application/json', 'authorization': auth}});
      if (r.status !== 200) return {status: r.status, stores: []};
      const j = await r.json();
      return {status: 200, stores: extract(j)};
    } catch (e) { return {status: -1, stores: [], error: String(e)}; }
  });
}
"""

# --- JS : dispo par magasin (same-origin), en parallèle par lots -------------
_FULFILMENT_BATCH_JS = """
async ([ean, pairs, batchSize]) => {
  async function inBatches(items, size, fn) {
    const out = [];
    for (let i = 0; i < items.length; i += size) {
      out.push(...await Promise.all(items.slice(i, i + size).map(fn)));
    }
    return out;
  }
  return await inBatches(pairs, batchSize, async (p) => {
    const sid = p[0], cp = p[1];
    const u = `/casto-browse-mfe/api/fulfilment-options?compositeOfferId=${ean}`
            + `&storeId=${sid}&postalCode=${cp}`;
    try {
      const r = await fetch(u, {credentials: 'include',
        headers: {'accept': 'application/json'}});
      if (!r.ok) return {sid: sid, __error: r.status};
      return {sid: sid, data: await r.json()};
    } catch (e) { return {sid: sid, __error: String(e)}; }
  });
}
"""


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


class CastoramaChecker(Checker):
    name = "Castorama"

    def check(self, context, products, zones):
        results: list[Availability] = []
        page = context.new_page()
        try:
            for product in products:
                results.extend(self._check_product(page, product, zones))
        finally:
            page.close()
        return results

    def _check_product(self, page, product, zones):
        ean = str(product["ref"]).split("_")[0]
        label = product.get("label", ean)
        url = product["url"]

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(1200)
        except Exception as e:
            log(f"Castorama: chargement KO {url}: {e}")
            return [self._mk(label, ean, "en-ligne", "Castorama (page KO)", "",
                             UNKNOWN, "page inaccessible", url)]

        stores = self._collect_stores(page, ean, zones)
        if not stores:
            return [self._mk(label, ean, "en-ligne", "Castorama (aucun magasin)",
                             "", UNKNOWN, "API magasins sans résultat", url)]

        # Dispo de tous les magasins en parallèle (par lots).
        pairs = [[sid, s["cp"] or ""] for sid, s in stores.items()]
        try:
            fres = page.evaluate(_FULFILMENT_BATCH_JS, [ean, pairs, FULFIL_BATCH])
        except Exception as e:
            log(f"Castorama: fulfilment batch KO: {e}")
            fres = []
        by_sid = {}
        for r in fres:
            sid = str(r.get("sid"))
            if r.get("__error") is not None:
                by_sid[sid] = (UNKNOWN, f"API KO ({r.get('__error')})")
            else:
                by_sid[sid] = self._parse_fulfilment(r.get("data"))

        log(f"Castorama: {len(stores)} magasin(s), dispo récupérée pour {len(by_sid)}.")
        out = []
        for sid, s in stores.items():
            status, detail = by_sid.get(sid, (UNKNOWN, "pas de réponse"))
            out.append(self._mk(label, ean, f"casto-{sid}",
                                s["name"] or f"Magasin {sid}", s["city"],
                                status, detail, url, s["distance"], s["lat"], s["lon"]))
        return out

    def _collect_stores(self, page, ean, zones) -> dict:
        home = self.config.get("home") or {}
        hlat, hlon = home.get("lat"), home.get("lon")
        zs = [z for z in zones if z.get("lat") is not None and z.get("lon") is not None]
        latlons = [[z["lat"], z["lon"]] for z in zs]
        names = [z.get("name", "") for z in zs]
        try:
            results = page.evaluate(_STORES_BATCH_JS, [ean, ATMOSPHERE_AUTH, latlons, STORES_BATCH])
        except Exception as e:
            log(f"Castorama: stores batch KO: {e}")
            return {}

        stores: dict[str, dict] = {}
        for name, res in zip(names, results):
            if res.get("status") != 200:
                log(f"Castorama: zone {name}: HTTP {res.get('status')} {res.get('error', '')}".strip())
                continue
            n_before = len(stores)
            for s in res.get("stores", []):
                sid = s.get("id")
                if not sid:
                    continue
                dist = None
                if hlat is not None and s.get("lat") is not None:
                    dist = round(_haversine_km(hlat, hlon, s["lat"], s["lon"]), 1)
                entry = {"name": s.get("name"), "cp": (s.get("cp") or ""),
                         "lat": s.get("lat"), "lon": s.get("lon"),
                         "distance": dist, "city": name}
                prev = stores.get(sid)
                if prev is None or (entry["distance"] or 1e9) < (prev["distance"] or 1e9):
                    stores[sid] = entry
            log(f"Castorama: zone {name}: HTTP 200, +{len(stores) - n_before} magasin(s)")
        return stores

    @staticmethod
    def _parse_fulfilment(data):
        if not isinstance(data, dict):
            return UNKNOWN, "réponse inattendue"
        try:
            attrs = data["data"][0]["attributes"]
        except (KeyError, IndexError, TypeError):
            return UNKNOWN, "réponse inattendue"
        cc = (attrs.get("clickAndCollectStorePick") or {}).get("availability")
        instore = (attrs.get("inStore") or {}).get("availability")
        home = (attrs.get("homeDelivery") or {}).get("availability")
        bits = []
        if cc:
            bits.append(f"retrait:{cc}")
        if instore:
            bits.append(f"magasin:{instore}")
        if home:
            bits.append(f"livraison:{home}")
        detail = " · ".join(bits) or "—"
        if cc == AVAILABLE or instore in ("InStock", "Available"):
            return IN_STOCK, detail
        return OUT_OF_STOCK, detail

    @staticmethod
    def _mk(label, ean, store_key, store_name, city, status, detail, url,
            distance=None, lat=None, lon=None):
        return Availability(
            retailer="Castorama", product_label=label, product_ref=ean,
            store_key=store_key, store_name=store_name, store_city=city,
            status=status, detail=detail, url=url, distance_km=distance,
            lat=lat, lon=lon,
        )
