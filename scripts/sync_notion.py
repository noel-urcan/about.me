#!/usr/bin/env python3
"""
sync_notion.py — Notion-Datenbank „Artikel“ → index.html und articles.html

Ablauf:
  1. Artikel und Medien aus Notion lesen (oder aus einer JSON-Datei mit --snapshot)
  2. Jeden veröffentlichten Artikel prüfen (Dateien vorhanden, Pflichtfelder)
  3. Sync-Status und Letzter Sync pro Artikel nach Notion zurückschreiben
  4. Nur wenn kein Fehler: Vorlagen aus templates/ füllen und HTML schreiben

Fehler stoppen den Sync (HTML bleibt unverändert, Exit-Code 1).
Hinweise stoppen nicht (z. B. Urheber fehlt), sie stehen im Sync-Status.

Aufruf:  python3 scripts/sync_notion.py [--snapshot datei.json] [--dump datei.json]
                                        [--no-notion-write] [--check-only]
Geheimnisse kommen aus .env (NOTION_TOKEN, NOTION_DATABASE_ID) oder der Umgebung.
"""
import argparse
import datetime as dt
import json
import os
import pathlib
import re
import sys
import zoneinfo
from urllib.parse import urlparse

ROOT = pathlib.Path(__file__).resolve().parent.parent
NOTION_VERSION = "2025-09-03"
MONATE = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
          "September", "Oktober", "November", "Dezember"]
KURZTITEL_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
LOGO_DIR = ROOT / "assets" / "img" / "logos"
IMG_DIR = ROOT / "assets" / "img"
PDF_DIR = ROOT / "assets" / "pdf"


# ---------------------------------------------------------------- Hilfen
def load_env():
    """Liest .env im Repo (Zeilen NAME=WERT), setzt nur, was noch nicht gesetzt ist."""
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def german_date(iso):
    y, m, d = (int(x) for x in iso[:10].split("-"))
    return f"{d}. {MONATE[m - 1]} {y}"


def domain_label(url):
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def ensure_webp(jpg: pathlib.Path) -> pathlib.Path:
    """Erzeugt neben dem JPG eine WebP-Datei, falls sie fehlt. Gibt den WebP-Pfad zurück."""
    webp = jpg.with_suffix(".webp")
    if not webp.exists():
        from PIL import Image
        with Image.open(jpg) as im:
            im = im.convert("RGB")
            im.save(webp, "WEBP", quality=85, method=6)
        print(f"  WebP erzeugt: {webp.relative_to(ROOT)}")
    return webp


# ---------------------------------------------------------------- Notion
class Notion:
    def __init__(self, token):
        import requests
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {token}",
                               "Notion-Version": NOTION_VERSION,
                               "Content-Type": "application/json"})
        self.base = "https://api.notion.com/v1"

    def _check(self, r):
        if r.status_code >= 400:
            raise RuntimeError(f"Notion-API {r.status_code}: {r.text[:300]}")
        return r.json()

    def get(self, path):
        return self._check(self.s.get(self.base + path, timeout=30))

    def post(self, path, body):
        return self._check(self.s.post(self.base + path, json=body, timeout=30))

    def patch(self, path, body):
        return self._check(self.s.patch(self.base + path, json=body, timeout=30))

    def data_source(self, some_id):
        """Nimmt eine Datenbank-ID oder Datenquellen-ID und liefert die Datenquelle."""
        try:
            return self.get(f"/data_sources/{some_id}")
        except RuntimeError:
            db = self.get(f"/databases/{some_id}")
            return self.get(f"/data_sources/{db['data_sources'][0]['id']}")

    def query_all(self, ds_id):
        rows, cursor = [], None
        while True:
            body = {"page_size": 100}
            if cursor:
                body["start_cursor"] = cursor
            res = self.post(f"/data_sources/{ds_id}/query", body)
            rows += res["results"]
            if not res.get("has_more"):
                return rows
            cursor = res["next_cursor"]


def pv(props, name):
    """Liest einen Notion-Property-Wert als einfachen Python-Wert."""
    p = props.get(name)
    if p is None:
        return None
    t = p["type"]
    if t in ("title", "rich_text"):
        return "".join(x["plain_text"] for x in p[t]).strip()
    if t == "url":
        return (p["url"] or "").strip()
    if t == "date":
        return p["date"]["start"] if p["date"] else ""
    if t == "checkbox":
        return bool(p["checkbox"])
    if t == "number":
        return p["number"]
    if t == "select":
        return p["select"]["name"] if p["select"] else ""
    if t == "multi_select":
        return [x["name"] for x in p["multi_select"]]
    if t == "relation":
        return [x["id"] for x in p["relation"]]
    return None


def parse_medien(pages):
    out = {}
    for pg in pages:
        p = pg["properties"]
        out[pg["id"]] = {
            "name": pv(p, "Name"), "kuerzel": (pv(p, "Kürzel") or "").strip().lower(),
            "anzeigename": pv(p, "Anzeigename"), "icon": pv(p, "Icon-Datei"),
            "logo": pv(p, "Logo-Datei"), "presseleiste": bool(pv(p, "In Presseleiste")),
            "reihenfolge": pv(p, "Reihenfolge Presseleiste"),
        }
    return out


def parse_artikel(pages):
    out = []
    for pg in pages:
        p = pg["properties"]
        out.append({
            "id": pg["id"], "titel": pv(p, "Titel"), "medium": pv(p, "Medium") or [],
            "datum": pv(p, "Datum") or "", "ausgabe": pv(p, "Ausgabe") or "",
            "autor": pv(p, "Autor") or "", "teaser": pv(p, "Teaser") or "",
            "link": pv(p, "Link") or "", "link2": pv(p, "Link 2") or "",
            "link2_text": pv(p, "Link 2 Text") or "", "kurztitel": (pv(p, "Kurztitel") or "").strip(),
            "foto_vorhanden": bool(pv(p, "Foto vorhanden")), "pdf_vorhanden": bool(pv(p, "PDF vorhanden")),
            "urheber": pv(p, "Urheber") or "", "lizenz": pv(p, "Lizenz") or "",
            "credits": pv(p, "Credits") or "", "alt": pv(p, "Alt-Text") or "",
            "veroeffentlichen": bool(pv(p, "Veröffentlichen")), "startseite": bool(pv(p, "Auf Startseite")),
            "r_start": pv(p, "Reihenfolge Startseite"), "r_arbeit": pv(p, "Reihenfolge Arbeitsproben"),
        })
    return out


# ---------------------------------------------------------------- Prüfen und aufbereiten
def build_card(a, medien):
    """Prüft einen Artikel. Gibt (karte, fehler, hinweise) zurück; karte ist None bei Fehlern."""
    fehler, hinweise = [], []
    if not a["titel"]:
        fehler.append("Titel fehlt")
    if not a["datum"]:
        fehler.append("Datum fehlt")
    if not a["teaser"]:
        fehler.append("Teaser fehlt")
    if not a["link"]:
        fehler.append("Link fehlt")
    if a["link2"] and not a["link2_text"]:
        fehler.append("Link 2 hat keinen Text (Feld „Link 2 Text“)")
    if not a["kurztitel"]:
        fehler.append("Kurztitel fehlt")
    elif not KURZTITEL_RE.match(a["kurztitel"]):
        fehler.append(f"Kurztitel „{a['kurztitel']}“ ist ungültig: nur Kleinbuchstaben, Ziffern, Bindestriche")
    med = None
    if not a["medium"]:
        fehler.append("Medium fehlt")
    elif a["medium"][0] not in medien:
        fehler.append("Medium unbekannt (nicht in der Datenbank Medien)")
    else:
        med = medien[a["medium"][0]]
        if not med["kuerzel"]:
            fehler.append(f"Medium „{med['name']}“ hat kein Kürzel")
        if not med["icon"]:
            fehler.append(f"Medium „{med['name']}“ hat keine Icon-Datei")
        elif not (LOGO_DIR / med["icon"]).exists():
            fehler.append(f"Icon-Datei fehlt: assets/img/logos/{med['icon']}")

    name = None
    if a["datum"] and med and med["kuerzel"] and a["kurztitel"]:
        name = f"{a['datum'][:10]}_{med['kuerzel']}_{a['kurztitel']}"
    foto = pdf = None
    if not a["foto_vorhanden"]:
        fehler.append("Kein Foto: Häkchen „Foto vorhanden“ nicht gesetzt (jede Karte braucht ein Foto)")
    elif name:
        jpg = IMG_DIR / f"{name}.jpg"
        if not jpg.exists():
            fehler.append(f"Foto fehlt: assets/img/{name}.jpg")
        else:
            foto = jpg
    if a["pdf_vorhanden"] and name:
        p = PDF_DIR / f"{name}.pdf"
        if not p.exists():
            fehler.append(f"PDF fehlt: assets/pdf/{name}.pdf")
        else:
            pdf = f"assets/pdf/{name}.pdf"

    if not a["urheber"]:
        hinweise.append("Urheber fehlt")
    if a["lizenz"] in ("", "offen"):
        hinweise.append("Lizenz offen")
    if not a["credits"]:
        hinweise.append("Credits fehlen")
    if not a["alt"]:
        hinweise.append("Alt-Text fehlt (Titel wird verwendet)")

    if fehler:
        return None, fehler, hinweise
    card = {
        "titel": a["titel"], "teaser": a["teaser"], "medium": med,
        "meta": german_date(a["datum"]) + (f" · {a['ausgabe']}" if a["ausgabe"] else ""),
        "link": a["link"], "link_text": domain_label(a["link"]),
        "link2": a["link2"], "link2_text": a["link2_text"],
        "pdf": pdf, "foto_jpg": foto, "foto": f"assets/img/{name}.webp",
        "alt": a["alt"] or a["titel"], "credits": a["credits"],
        "datum": a["datum"][:10], "ausgabe": a["ausgabe"], "r_start": a["r_start"], "r_arbeit": a["r_arbeit"],
        "startseite": a["startseite"],
    }
    return card, [], hinweise


def sort_key(field):
    def k(c):
        r = c[field]
        return (0, r, "") if r is not None else (1, 0, "")
    return k


def sortiert(cards, field):
    """Erst die nummerierten aufsteigend, dann die übrigen nach Datum absteigend."""
    num = sorted([c for c in cards if c[field] is not None], key=lambda c: (c[field], c["datum"]))
    # gleiches Datum: nach Ausgabe-Text (z. B. Seitenzahlen), dann Titel; Datum selbst absteigend
    rest = sorted([c for c in cards if c[field] is None], key=lambda c: (c["ausgabe"], c["titel"]))
    rest.sort(key=lambda c: c["datum"], reverse=True)
    return num + rest


# ---------------------------------------------------------------- Hauptprogramm
def main():
    ap = argparse.ArgumentParser(description="Notion → Website")
    ap.add_argument("--snapshot", help="JSON-Datei statt Notion lesen (kein Zurückschreiben)")
    ap.add_argument("--dump", help="gelesene Daten als JSON speichern")
    ap.add_argument("--no-notion-write", action="store_true", help="Sync-Status nicht nach Notion schreiben")
    ap.add_argument("--check-only", action="store_true", help="nur prüfen, kein HTML schreiben")
    args = ap.parse_args()
    load_env()

    notion = None
    if args.snapshot:
        data = json.loads(pathlib.Path(args.snapshot).read_text(encoding="utf-8"))
        medien, artikel = data["medien"], data["artikel"]
        print(f"Daten aus Snapshot: {args.snapshot}")
    else:
        token, db_id = os.environ.get("NOTION_TOKEN"), os.environ.get("NOTION_DATABASE_ID")
        if not token or not db_id:
            sys.exit("NOTION_TOKEN oder NOTION_DATABASE_ID fehlt (.env oder Umgebung)")
        notion = Notion(token)
        ds = notion.data_source(db_id)
        rel = ds["properties"]["Medium"]["relation"]
        medien_ds = rel.get("data_source_id") or rel.get("database_id")
        medien = parse_medien(notion.query_all(notion.data_source(medien_ds)["id"]))
        artikel = parse_artikel(notion.query_all(ds["id"]))
        print(f"Aus Notion gelesen: {len(artikel)} Artikel, {len(medien)} Medien")
    if args.dump:
        pathlib.Path(args.dump).write_text(json.dumps({"medien": medien, "artikel": artikel},
                                                      ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"Daten gespeichert: {args.dump}")

    # Presseleiste
    fehler_global = []
    presse = sorted([m for m in medien.values() if m["presseleiste"]],
                    key=lambda m: (m["reihenfolge"] is None, m["reihenfolge"] or 0, m["name"]))
    for m in presse:
        if not m["logo"]:
            fehler_global.append(f"Medium „{m['name']}“ steht in der Presseleiste, hat aber keine Logo-Datei")
        elif not (LOGO_DIR / m["logo"]).exists():
            fehler_global.append(f"Logo-Datei fehlt: assets/img/logos/{m['logo']}")

    # Artikel prüfen
    cards, status, gesamt_fehler = [], {}, list(fehler_global)
    for a in artikel:
        if not a["veroeffentlichen"]:
            continue
        card, fehler, hinweise = build_card(a, medien)
        if fehler:
            status[a["id"]] = "Fehler: " + "; ".join(fehler)
            gesamt_fehler += [f"„{a['titel'] or a['id']}“: {f}" for f in fehler]
        else:
            status[a["id"]] = "OK" if not hinweise else "OK, Hinweis: " + "; ".join(hinweise)
            cards.append(card)
        print(f"- {a['titel'][:60]!s:60} {status[a['id']]}")
    for f in fehler_global:
        print(f"- Presseleiste: {f}")

    # Status nach Notion
    if notion and not args.no_notion_write:
        now = dt.datetime.now(zoneinfo.ZoneInfo("Europe/Berlin")).isoformat(timespec="seconds")
        for pid, text in status.items():
            notion.patch(f"/pages/{pid}", {"properties": {
                "Sync-Status": {"rich_text": [{"text": {"content": text[:1900]}}]},
                "Letzter Sync": {"date": {"start": now}}}})
        print(f"Sync-Status für {len(status)} Artikel nach Notion geschrieben")

    if gesamt_fehler:
        print(f"\n{len(gesamt_fehler)} Fehler, HTML wird NICHT geschrieben:")
        for f in gesamt_fehler:
            print("  •", f)
        sys.exit(1)
    if args.check_only:
        print("\nPrüfung ohne Fehler (nur geprüft, nichts geschrieben).")
        return

    # Bilder als WebP
    for c in cards:
        ensure_webp(c["foto_jpg"])
    for extra in ("profil.jpg", "extra3-sendungsbild.jpg"):
        if (IMG_DIR / extra).exists():
            ensure_webp(IMG_DIR / extra)

    # Rendern
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader(str(ROOT / "templates")), autoescape=True,
                      keep_trailing_newline=True)
    ctx = {"presseleiste": presse,
           "arbeitsproben": sortiert(cards, "r_arbeit"),
           "startseite": sortiert([c for c in cards if c["startseite"]], "r_start")}
    geschrieben = []
    for tpl, out in (("index.html.j2", "index.html"), ("articles.html.j2", "articles.html")):
        html = env.get_template(tpl).render(**ctx)
        target = ROOT / out
        if not target.exists() or target.read_text(encoding="utf-8") != html:
            target.write_text(html, encoding="utf-8")
            geschrieben.append(out)
    print("\nFertig ohne Fehler. "
          + (f"Geschrieben: {', '.join(geschrieben)}" if geschrieben else "Keine Änderung an den HTML-Dateien."))
    print(f"Startseite: {len(ctx['startseite'])} Artikel, Arbeitsproben: {len(ctx['arbeitsproben'])} Artikel")


if __name__ == "__main__":
    main()
