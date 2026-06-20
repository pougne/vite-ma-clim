"""Envoi des notifications : email (SMTP) et/ou ntfy (push mobile)."""
from __future__ import annotations

import smtplib
import ssl
import urllib.request
import urllib.error
from email.message import EmailMessage

from models import Availability


def _km(r: Availability) -> str:
    d = getattr(r, "distance_km", None)
    return "" if d is None else f"{d:.0f} km"


def _by_distance(results: list[Availability]):
    return sorted(results, key=lambda x: (
        x.distance_km if x.distance_km is not None else 1e9,
        x.retailer, x.store_city or "", x.store_name))


def _place(r: Availability) -> str:
    """Nom du point de vente sans doubler l'enseigne (ex. 'Castorama Limoges' -> 'Limoges')."""
    name = (r.store_name or "").strip()
    if r.retailer and name.lower().startswith(r.retailer.lower()):
        name = name[len(r.retailer):].strip(" -—")
    return name or (r.store_city or r.retailer or "")


def _format_lines(results: list[Availability]) -> str:
    lines = []
    urls = []
    for r in _by_distance(results):
        head = f"• {r.retailer} {_place(r)}".rstrip()
        d = _km(r)
        if d:
            head += f" · {d}"
        if getattr(r, "restock", False):
            head += " · 🔁 RÉASSORT"
        lines.append(f"{head}\n  {r.detail or 'dispo'}")
        if r.url:
            urls.append(r.url)
    body = "\n".join(lines)
    # Lien(s) produit affiché(s) une seule fois en bas : l'URL est identique pour
    # tous les magasins d'une enseigne, inutile de la répéter à chaque ligne.
    for u in dict.fromkeys(urls):          # dédoublonne en gardant l'ordre
        body += f"\n→ {u}"
    return body


def notify_email(cfg: dict, results: list[Availability]) -> None:
    if not cfg.get("enabled"):
        return
    body = (
        "Nouvelle(s) disponibilité(s) détectée(s) pour le Midea PortaSplit :\n\n"
        + _format_lines(results)
        + "\n\n— Vite Ma Clim"
    )
    msg = EmailMessage()
    msg["Subject"] = f"[Vite Ma Clim] {len(results)} dispo(s) PortaSplit !"
    msg["From"] = cfg["from_addr"]
    msg["To"] = ", ".join(cfg["to_addrs"])
    msg.set_content(body)

    ctx = ssl.create_default_context()
    if cfg.get("use_ssl", True):
        with smtplib.SMTP_SSL(cfg["host"], cfg.get("port", 465), context=ctx) as s:
            s.login(cfg["user"], cfg["password"])
            s.send_message(msg)
    else:
        with smtplib.SMTP(cfg["host"], cfg.get("port", 587)) as s:
            s.starttls(context=ctx)
            s.login(cfg["user"], cfg["password"])
            s.send_message(msg)


def notify_ntfy(cfg: dict, results: list[Availability]) -> None:
    if not cfg.get("enabled"):
        return
    res = _by_distance(results)
    primary = res[0]
    n = len(res)
    topic_url = cfg["topic_url"].rstrip("/")

    # Titre : enseigne + vraie ville la plus proche (pas la zone de recherche).
    km = _km(primary)
    near = f"{primary.retailer} {_place(primary)}".rstrip() + (f" · {km}" if km else "")
    if n == 1:
        title = f"PortaSplit dispo · {near}"
    else:
        title = f"{n} dispos PortaSplit · au plus près : {near}"

    # Boutons tappables : fiche produit (+ itineraire si on a les coordonnees).
    # NB: l'en-tete ntfy "Actions" separe les champs par des virgules -> on
    # encode la virgule des coordonnees Google Maps en %2C, sinon ntfy renvoie 400.
    actions = [f"view, Voir la fiche, {primary.url}, clear=true"]
    if primary.lat is not None and primary.lon is not None:
        gmaps = (f"https://www.google.com/maps/dir/?api=1"
                 f"&destination={primary.lat}%2C{primary.lon}")
        actions.append(f"view, Itineraire, {gmaps}")

    # Les en-tetes HTTP doivent etre encodables en latin-1 ; on neutralise
    # tout caractere exotique (apostrophe typographique, tiret cadratin, emoji...).
    def _h(v: str) -> str:
        return str(v).encode("latin-1", "replace").decode("latin-1")

    req = urllib.request.Request(
        topic_url,
        data=_format_lines(res).encode("utf-8"),
        headers={
            "Title": _h(title),
            "Priority": "high",
            "Tags": "snowflake",
            "Click": _h(primary.url or topic_url),
            "Actions": _h("; ".join(actions[:3])),
        },
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=15)
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        raise RuntimeError(f"ntfy HTTP {e.code}: {body}") from e


def notify_health(notif_cfg: dict, broken: list[str], recovered: list[str]) -> None:
    """Alerte « panne » : une ou plusieurs enseignes ne répondent plus (ou se
    rétablissent). Envoie via ntfy et/ou email, sans jamais lever d'exception."""
    if not broken and not recovered:
        return
    parts = []
    if broken:
        parts.append("⚠️ Ne répond plus : " + ", ".join(broken))
    if recovered:
        parts.append("✅ De nouveau OK : " + ", ".join(recovered))
    body = "\n".join(parts)
    title = "Vite Ma Clim - surveillance"

    ntfy = notif_cfg.get("ntfy", {})
    if ntfy.get("enabled"):
        try:
            topic_url = ntfy["topic_url"].rstrip("/")
            req = urllib.request.Request(
                topic_url, data=body.encode("utf-8"),
                headers={"Title": title, "Priority": "default",
                         "Tags": "warning" if broken else "white_check_mark"},
                method="POST")
            urllib.request.urlopen(req, timeout=15)
            print(f"[notify] health ntfy: OK ({body!r})")
        except Exception as e:
            print(f"[notify] health ntfy: ECHEC -> {e}")

    em = notif_cfg.get("email", {})
    if em.get("enabled"):
        try:
            msg = EmailMessage()
            msg["Subject"] = f"[Vite Ma Clim] {title}"
            msg["From"] = em["from_addr"]; msg["To"] = ", ".join(em["to_addrs"])
            msg.set_content(body + "\n\n— Vite Ma Clim")
            ctx = ssl.create_default_context()
            if em.get("use_ssl", True):
                with smtplib.SMTP_SSL(em["host"], em.get("port", 465), context=ctx) as srv:
                    srv.login(em["user"], em["password"]); srv.send_message(msg)
            else:
                with smtplib.SMTP(em["host"], em.get("port", 587)) as srv:
                    srv.starttls(context=ctx); srv.login(em["user"], em["password"]); srv.send_message(msg)
            print("[notify] health email: OK")
        except Exception as e:
            print(f"[notify] health email: ECHEC -> {e}")


def dispatch(notif_cfg: dict, results: list[Availability]) -> None:
    """Envoie via tous les canaux actives.
    Leve une exception si ntfy echoue (pour que mark_notified ne soit pas
    appele et qu on retente au passage suivant).
    L email est best-effort (pas bloquant).
    """
    if not results:
        return
    # Email : best-effort, jamais bloquant.
    email_cfg = notif_cfg.get("email", {})
    if email_cfg.get("enabled"):
        try:
            notify_email(email_cfg, results)
            print(f"[notify] email: OK ({len(results)} dispo)")
        except Exception as e:
            print(f"[notify] email: ECHEC (non bloquant) -> {e}")
    # ntfy : on laisse remonter l exception si ca plante,
    # pour que le caller sache que l envoi n a pas eu lieu.
    ntfy_cfg = notif_cfg.get("ntfy", {})
    if ntfy_cfg.get("enabled"):
        notify_ntfy(ntfy_cfg, results)   # leve en cas d erreur
        print(f"[notify] ntfy: OK ({len(results)} dispo)")
