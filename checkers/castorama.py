"""Checker Castorama — API Kingfisher (magasins + stock + click&collect), par lots.

L'API /v1/mobile/stores/CAFR (include=clickAndCollect,stock&filter[ean]=…) renvoie,
pour chaque magasin proche, ses coordonnées ET le stock du produit :
  - attributes.stock.products[].quantity / stockLevel (InStock / LowStock / OutOfStock)
  - attributes.clickAndCollect.products[].availability (Available / NotAvailable)
Un seul lot d'appels par zone suffit donc — plus besoin de l'API fulfilment-options.
"""
from __future__ import annotations

import math

from models import Availability, IN_STOCK, OUT_OF_STOCK, UNKNOWN
from .base import Checker, log

STORES_BATCH = 8

ATMOSPHERE_AUTH = ("Atmosphere atmosphere_app_id="
                   "kingfisher-o4ITR0sWAyCVQBraQf4Es61jHV3dN4oO9UwJQMrS")

# Magasins proches de chaque (lat,lon) AVEC stock + click&collect, par lots.
_STORES_BATCH_JS = """
async ([ean, auth, latlons, batchSize]) => {
  async function inBatches(items, size, fn) {
    const out = [];
    for (let i = 0; i < items.length; i += size) {
      out.push(...await Promise.all(items.slice(i, i + size).map(fn)));
    }
    return out;
  }
  function extract(j, ean) {
    const out = [];
    for (const s of ((j && j.data) || [])) {
      if (s.type !== 'store') continue;
      const a = s.attributes || {}, st = a.store || {};
      const geo = (st.geoCoordinates || a.geoCoordinates || {});
      const c = geo.coordinates || {};
      let lat = (c.latitude != null ? c.latitude : geo.latitude);
      let lon = (c.longitude != null ? c.longitude : geo.longitude);
      let qty = null, level = null;
      const sp = ((a.stock || {}).products) || [];
      for (const p of sp) { if (!ean || p.ean === ean) { qty = (p.quantity != null ? p.quantity : null); level = p.stockLevel || null; break; } }
      let cc = null;
      const ccp = ((a.clickAndCollect || {}).products) || [];
      for (const p of ccp) { if (!ean || p.ean === ean) { cc = p.availability || null; break; } }
      if (cc == null) cc = ((a.clickAndCollect || {}).summary || {}).availability || null;
      out.push({
        id: String(s.id), name: a.name || st.name || null,
        cp: ((geo.postalCode || '') + '').trim(),
        lat: (lat != null ? Number(lat) : null), lon: (lon != null ? Number(lon) : null),
        qty: qty, level: level, cc: cc
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
      return {status: 200, stores: extract(j, ean)};
    } catch (e) { return {status: -1, stores: [], error: String(e)}; }
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

        out, n_in = [], 0
        for sid, s in stores.items():
            status, detail = self._stock_detail(s.get("qty"), s.get("level"), s.get("cc"))
            if status == IN_STOCK:
                n_in += 1
            out.append(self._mk(label, ean, f"casto-{sid}", s["name"] or f"Magasin {sid}",
                                s["city"], status, detail, url, s["distance"], s["lat"], s["lon"],
                                quantity=s.get("qty")))
        log(f"Castorama: {len(stores)} magasin(s), {n_in} avec stock.")
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
                         "lat": s.get("lat"), "lon": s.get("lon"), "distance": dist,
                         "city": name, "qty": s.get("qty"), "level": s.get("level"),
                         "cc": s.get("cc")}
                prev = stores.get(sid)
                if prev is None or (entry["distance"] or 1e9) < (prev["distance"] or 1e9):
                    stores[sid] = entry
            log(f"Castorama: zone {name}: HTTP 200, +{len(stores) - n_before} magasin(s)")
        return stores

    @staticmethod
    def _stock_detail(qty, level, cc):
        cc_ok = cc == "Available"
        if isinstance(qty, int) and qty > 0:
            unit = "pièce" if qty == 1 else "pièces"
            base = "Stock limité" if level == "LowStock" else "Stock magasin"
            bits = [f"{base} : {qty} {unit}", "Retrait 2h ✓" if cc_ok else "Retrait 2h ✗"]
            return IN_STOCK, " · ".join(bits)
        if qty == 0 or level == "OutOfStock":
            return OUT_OF_STOCK, "Rupture en magasin"
        return UNKNOWN, "stock inconnu"

    @staticmethod
    def _mk(label, ean, store_key, store_name, city, status, detail, url,
            distance=None, lat=None, lon=None, quantity=None):
        return Availability(
            retailer="Castorama", product_label=label, product_ref=ean,
            store_key=store_key, store_name=store_name, store_city=city,
            status=status, detail=detail, url=url, distance_km=distance,
            lat=lat, lon=lon, quantity=quantity,
        )
