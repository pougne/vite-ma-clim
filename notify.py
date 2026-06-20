"""Envoi des notifications : email (SMTP) et/ou ntfy (push mobile)."""
from __future__ import annotations

import smtplib
import ssl
import urllib.request
from email.message import EmailMessage

from models import Availability


def _km(r: Availability) -> str:
    d = getattr(r, "distance_km", None)
    return "" if d is None else f"{d:.0f} km"


def _by_distance(results: list[Availability]):
    return sorted(results, key=lambda x: (
        x.distance_km if x.distance_km is not None else 1e9,
        x.retailer, x.store_city or "", x.store_name))


def _format_lines(results: list[Availability]) -> str:
    lines = []
    for r in _by_distance(results):
        loc = (f"{r.store_name} ({r.store_city})"
               if r.store_city and r.store_city != "—" else r.store_name)
        head = f"• {r.retailer} — {loc}"
        d = _km(r)
        if d:
            head += f" · {d}"
        lines.append(f"{head}\n  {r.detail or 'dispo'}\n  {r.url}")
    return "\n".join(lines)


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

    # Titre : indique directement l'enseigne / la ville la plus proche.
    where = primary.store_city if (primary.store_city and primary.store_city != "-") else primary.retailer
    km = _km(primary)
    if n == 1:
        title = f"PortaSplit dispo - {primary.retailer} {primary.store_city or ''}".strip()
    else:
        near = f"{primary.retailer} {where}".strip() + (f" {km}" if km else "")
        title = f"{n} dispos Midea PortaSplit - au plus pres: {near}"

    # Boutons tappables : fiche produit (+ itineraire si on a les coordonnees).
    actions = [f"view, Voir la fiche, {primary.url}, clear=true"]
    if primary.lat is not None and primary.lon is not None:
        gmaps = f"https://www.google.com/maps/dir/?api=1&destination={primary.lat},{primary.lon}"
        actions.append(f"view, Itineraire, {gmaps}")

    req = urllib.request.Request(
        topic_url,
        data=_format_lines(res).encode("utf-8"),
        headers={
            "Title": title,
            "Priority": "high",
            "Tags": "snowflake",
            "Click": primary.url or topic_url,
            "Actions": "; ".join(actions[:3]),
        },
        method="POST",
    )
    urllib.request.urlopen(req, timeout=15)


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
