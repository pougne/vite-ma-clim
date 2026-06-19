"""Checker Castorama — appel DIRECT des API internes depuis la page.

On charge la fiche produit une fois (origine + cookies), puis on appelle :
  1. api.kingfisher.com/v1/mobile/stores/CAFR?nearLatLong=<lat,lon>
       &page[size]=10&include=clickAndCollect,stock&filter[ean]=<EAN>
     (en-tête Authorization "Atmosphere" embarqué dans le site) -> magasins proches.
  2. /casto-browse-mfe/api/fulfilment-options?compositeOfferId=<EAN>
       &storeId=<id>&postalCode=<cp>   (same-origin) -> dispo nette par magasin.

La distance affichée est calculée à vol d'oiseau depuis le point "home" de la
config (donc cohérente quel que soit la ville qui a permis de trouver le magasin).
"""
from __future__ import annotations

import json
import math

from models import Availability, IN_STOCK, OUT_OF_STOCK, UNKNOWN
from .base import Checker, accept_cookies, log

AVAILABLE = "Available"

ATMOSPHERE_AUTH = ("Atmosphere atmosphere_app_id="
                   "kingfisher-o4ITR0sWAyCVQBraQf4Es61jHV3dN4oO9UwJQMrS")

_STORES_JS = """
async ([ean, lat, lon, auth]) => {
  const u = `https://api.kingfisher.com/v1/mobile/stores/CAFR?nearLatLong=${lat}%2C${lon}`
          + `&page[size]=10&include=clickAndCollect,stock&filter[ean]=${ean}`;
  try {
    const r = await fetch(u, {credentials: 'include',
                              headers: {'accept': 'application/json',
                                        'authorization': auth}});
    const txt = await r.text();
    return {status: r.status, body: txt.slice(0, 300000)};
  } catch (e) { return {status: -1, error: String(e)}; }
}
"""

_FULFILMENT_JS = """
async ([ean, storeId, postalCode]) => {
  const u = `/casto-browse-mfe/api/fulfilment-options?compositeOfferId=${ean}`
            + `&storeId=${storeId}&postalCode=${postalCode}`;
  try {
    const r = await fetch(u, {credentials: 'include',
                              headers: {'accept': 'application/json'}});
    if (!r.ok) return {__error: r.status};
    return await r.json();
  } catch (e) { return {__error: String(e)}; }
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
        out: list[Availability] = []

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            accept_cookies(page)
            page.wait_for_timeout(1500)
        except Exception as e:
            log(f"Castorama: chargement KO {url}: {e}")
            return [self._mk(label, ean, "en-ligne", "Castorama (page KO)", "",
                             UNKNOWN, "page inaccessible", url)]

        stores = self._collect_stores(page, ean, zones)
        if not stores:
            out.append(self._mk(label, ean, "en-ligne", "Castorama (aucun magasin)",
                                "", UNKNOWN, "API magasins sans résultat", url))
            return out

        log(f"Castorama: {len(stores)} magasin(s) unique(s) à interroger.")
        for sid, s in stores.items():
            try:
                data = page.evaluate(_FULFILMENT_JS, [ean, sid, s["cp"] or ""])
            except Exception as e:
                data = {"__error": str(e)}
            status, detail = self._parse_fulfilment(data)
            out.append(self._mk(label, ean, f"casto-{sid}",
                                s["name"] or f"Magasin {sid}", s["city"],
                                status, detail, url, s["distance"], s["lat"], s["lon"]))
        return out

    def _collect_stores(self, page, ean, zones) -> dict:
        home = self.config.get("home") or {}
        hlat, hlon = home.get("lat"), home.get("lon")
        stores: dict[str, dict] = {}
        for zone in zones:
            lat, lon = zone.get("lat"), zone.get("lon")
            name = zone.get("name", "")
            if lat is None or lon is None:
                continue
            try:
                res = page.evaluate(_STORES_JS, [ean, lat, lon, ATMOSPHERE_AUTH])
            except Exception as e:
                log(f"Castorama: zone {name}: evaluate KO: {e}")
                continue
            if res.get("status") != 200:
                log(f"Castorama: zone {name}: HTTP {res.get('status')} {res.get('error','')}".strip())
                continue
            try:
                data = json.loads(res["body"])
            except Exception:
                log(f"Castorama: zone {name}: HTTP 200 mais JSON illisible")
                continue
            n_before = len(stores)
            for s in self._parse_stores(data, name):
                # distance depuis "home" si on a les coordonnées du magasin
                if hlat is not None and s["lat"] is not None:
                    s["distance"] = round(_haversine_km(hlat, hlon, s["lat"], s["lon"]), 1)
                prev = stores.get(s["id"])
                if prev is None or (s["distance"] or 1e9) < (prev["distance"] or 1e9):
                    stores[s["id"]] = s
            log(f"Castorama: zone {name}: HTTP 200, +{len(stores) - n_before} magasin(s)")
        return stores

    # ---- parsing -----------------------------------------------------------
    @staticmethod
    def _parse_stores(resp, city) -> list[dict]:
        res = []
        for s in (resp or {}).get("data", []):
            if s.get("type") != "store":
                continue
            attrs = s.get("attributes", {})
            store = attrs.get("store", {})
            geo = store.get("geoCoordinates", {}) or attrs.get("geoCoordinates", {})
            coords = geo.get("coordinates", {}) or {}
            slat = coords.get("latitude", geo.get("latitude"))
            slon = coords.get("longitude", geo.get("longitude"))
            try:
                slat = float(slat) if slat is not None else None
                slon = float(slon) if slon is not None else None
            except (TypeError, ValueError):
                slat = slon = None
            dist = None
            gsr = attrs.get("geoSearchResults", {})
            if isinstance(gsr.get("distance"), (int, float)):
                dist = float(gsr["distance"])
            else:
                raw = attrs.get("distance") or store.get("distance") or ""
                try:
                    dist = float(str(raw).split()[0].replace(",", "."))
                except Exception:
                    dist = None
            res.append({
                "id": str(s.get("id")),
                "name": attrs.get("name") or store.get("name"),
                "cp": (geo.get("postalCode") or "").strip(),
                "lat": slat, "lon": slon,
                "distance": dist,
                "city": city,
            })
        return res

    @staticmethod
    def _parse_fulfilment(data):
        if not isinstance(data, dict) or data.get("__error") is not None:
            err = data.get("__error") if isinstance(data, dict) else "?"
            return UNKNOWN, f"API KO ({err})"
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
