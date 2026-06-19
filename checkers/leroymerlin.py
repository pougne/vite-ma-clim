"""Checker Leroy Merlin — API contextlayer "store-search-result" (renvoie du HTML).

ATTENTION : Leroy Merlin est protégé par DataDome. Cette enseigne ne fonctionne
que si le navigateur passe le contrôle anti-bot au chargement de la fiche
(obtention d'un cookie `datadome` valide). Depuis une IP de datacenter (cloud),
ce n'est pas garanti : si DataDome bloque, le checker le détecte, renvoie UNKNOWN
et le signale dans les logs — il suffit alors de mettre `enabled: false` dans
config.yaml pour cette enseigne.

L'API prend latitude/longitude/productRef et renvoie une fiche
<article class="m-store-search-card"> par magasin proche, avec un badge de
stock (rouge = indisponible) et un libellé.
"""
from __future__ import annotations

import re

from models import Availability, IN_STOCK, OUT_OF_STOCK, UNKNOWN
from .base import Checker, log

API = "/store-header-module/services/contextlayer/store-search-result"
ZONES_BATCH = 8

_BATCH_JS = """
async ([apiPath, ref, latlons, batchSize]) => {
  async function inBatches(items, size, fn) {
    const out = [];
    for (let i = 0; i < items.length; i += size) {
      out.push(...await Promise.all(items.slice(i, i + size).map(fn)));
    }
    return out;
  }
  return await inBatches(latlons, batchSize, async (ll) => {
    const u = `${apiPath}?latitude=${ll[0]}&longitude=${ll[1]}&productRef=${ref}&storeSearchType=STOCK`;
    try {
      const r = await fetch(u, {credentials: 'include',
        headers: {'accept': 'text/html', 'accept-language': 'fr-FR'}});
      const t = await r.text();
      return {status: r.status, body: t.slice(0, 200000)};
    } catch (e) { return {status: -1, body: '', error: String(e)}; }
  });
}
"""

CARD_RE = re.compile(r'<article class="m-store-search-card"(.*?)</article>', re.S)


def _attr(s: str, name: str):
    m = re.search(name + r'="([^"]*)"', s)
    return m.group(1).strip() if m else None


def _text(s: str, cls: str):
    m = re.search(r'class="' + re.escape(cls) + r'"[^>]*>(.*?)</', s, re.S)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else None


class LeroyMerlinChecker(Checker):
    name = "Leroy Merlin"

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
        ref = str(product["ref"])
        label = product.get("label", ref)
        url = product["url"]
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(1500)
            cur, html = page.url, page.content()
        except Exception as e:
            log(f"LeroyMerlin: chargement KO: {e}")
            return [self._mk(label, ref, "page", "Leroy Merlin (page KO)", "",
                             UNKNOWN, "page inaccessible", url)]

        if "captcha-delivery" in html or "captcha-delivery" in cur or "DataDome" in html:
            log("LeroyMerlin: DataDome a bloqué le chargement (cloud) -> enseigne indisponible.")
            return [self._mk(label, ref, "ddome", "Leroy Merlin (bloqué DataDome)", "",
                             UNKNOWN, "anti-bot DataDome", url)]

        zs = [z for z in zones if z.get("lat") is not None and z.get("lon") is not None]
        latlons = [[z["lat"], z["lon"]] for z in zs]
        try:
            res = page.evaluate(_BATCH_JS, [API, ref, latlons, ZONES_BATCH])
        except Exception as e:
            log(f"LeroyMerlin: appels API KO: {e}")
            return [self._mk(label, ref, "api", "Leroy Merlin (API KO)", "",
                             UNKNOWN, str(e)[:80], url)]

        stores: dict[str, dict] = {}
        blocked = 0
        for r in res:
            body = r.get("body", "") or ""
            if r.get("status") != 200 or "m-store-search-card" not in body:
                if "captcha-delivery" in body or "DataDome" in body:
                    blocked += 1
                continue
            for card in self._parse_cards(body):
                prev = stores.get(card["key"])
                if prev is None or (card["dist_val"] or 1e9) < (prev["dist_val"] or 1e9):
                    stores[card["key"]] = card

        if not stores:
            detail = "anti-bot DataDome" if blocked else "aucun magasin renvoyé"
            log(f"LeroyMerlin: aucune fiche exploitable ({detail}; {blocked} zone(s) bloquée(s)).")
            return [self._mk(label, ref, "vide", f"Leroy Merlin ({detail})", "",
                             UNKNOWN, detail, url)]

        n_in = sum(1 for c in stores.values() if c["status"] == IN_STOCK)
        log(f"LeroyMerlin: {len(stores)} magasin(s) lus, {n_in} dispo.")
        return [self._mk(label, ref, f"lm-{c['key']}", c["name"], c["city"],
                         c["status"], c["detail"], url) for c in stores.values()]

    @staticmethod
    def _parse_cards(body: str):
        cards = []
        for inner in CARD_RE.findall(body):
            city = _attr(inner, "data-store-city") or ""
            name = _text(inner, "m-store-info-header--title") or city or "?"
            dist_txt = _text(inner, "m-store-info-header__store-distance") or ""
            dist_val = None
            mdv = re.search(r"([\d,.]+)\s*km", dist_txt)
            if mdv:
                try:
                    dist_val = float(mdv.group(1).replace(",", "."))
                except ValueError:
                    pass
            mb = re.search(r"stock-status_badge--(\w+)", inner)
            badge = mb.group(1).lower() if mb else None
            low = inner.lower()
            if "indisponible" in low or badge == "red":
                status, lab = OUT_OF_STOCK, "indisponible"
            elif (badge in ("green", "orange", "yellow")
                  or "en stock" in low
                  or ("disponible" in low and "indisponible" not in low)):
                status, lab = IN_STOCK, "disponible"
            else:
                status, lab = UNKNOWN, "statut ?"
            detail = " · ".join(x for x in [dist_txt or None, lab] if x)
            key = re.sub(r"[^a-z0-9]+", "", (name + city).lower()) or name
            cards.append({"key": key, "name": name,
                          "city": city.title() if city else "",
                          "dist_val": dist_val, "status": status, "detail": detail})
        return cards

    @staticmethod
    def _mk(label, ref, store_key, store_name, city, status, detail, url):
        return Availability(
            retailer="Leroy Merlin", product_label=label, product_ref=ref,
            store_key=store_key, store_name=store_name, store_city=city,
            status=status, detail=detail, url=url,
            distance_km=None, lat=None, lon=None,
        )
