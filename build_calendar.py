#!/usr/bin/env python3
"""
Genera un calendario .ics con las noticias macro de EUR y USD
(fuente: feed semanal público de Forex Factory).

- Solo usa la librería estándar de Python (no hay que instalar nada).
- Acumula el historial en events.json para que las semanas pasadas
  no desaparezcan de tu Google Calendar.
- Configurable con variables de entorno:
    CURRENCIES  (por defecto "EUR,USD")
    IMPACTS     (por defecto "High,Medium")  -> opciones: High, Medium, Low, Holiday
    KEEP_DAYS   (por defecto 90)             -> días de historial a conservar
    FEED_FILE   (opcional, para pruebas: lee un JSON local en vez de descargar)
"""
import hashlib
import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

FEEDS = [
    "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
    "https://nfs.faireconomy.media/ff_calendar_nextweek.json",  # puede no existir; se ignora si falla
]
CURRENCIES = {c.strip().upper() for c in os.getenv("CURRENCIES", "EUR,USD").split(",") if c.strip()}
IMPACTS = {i.strip().capitalize() for i in os.getenv("IMPACTS", "High,Medium").split(",") if i.strip()}
KEEP_DAYS = int(os.getenv("KEEP_DAYS", "90"))

HERE = os.path.dirname(os.path.abspath(__file__))
STORE = os.path.join(HERE, "events.json")
OUTPUT = os.path.join(HERE, "macro-eurusd.ics")

ICON = {"High": "🔴", "Medium": "🟡", "Low": "⚪", "Holiday": "🏦"}
IMPACT_ES = {"High": "ALTO", "Medium": "MEDIO", "Low": "BAJO", "Holiday": "FESTIVO"}


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (macro-eurusd-calendar)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def load_feed():
    if os.getenv("FEED_FILE"):
        with open(os.environ["FEED_FILE"], encoding="utf-8") as f:
            return json.load(f)
    items, ok = [], False
    for url in FEEDS:
        try:
            items += fetch(url)
            ok = True
        except Exception as e:  # nextweek no siempre está publicado
            print(f"Aviso: no se pudo leer {url}: {e}", file=sys.stderr)
    if not ok:
        sys.exit("Error: no se pudo descargar ningún feed.")
    return items


def uid_for(ev):
    # Estable aunque cambie la hora: moneda + título + día
    key = f"{ev['country']}|{ev['title']}|{ev['date'][:10]}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:20] + "@macro-eurusd"


def esc(text):
    return (text.replace("\\", "\\\\").replace(";", "\\;")
                .replace(",", "\\,").replace("\n", "\\n"))


def fold(line):
    # RFC 5545: líneas de máx. 75 octetos
    out, cur = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        if len(cur) + len(b) > 74:
            out.append(cur.decode("utf-8"))
            cur = b" " + b
        else:
            cur += b
    out.append(cur.decode("utf-8"))
    return "\r\n".join(out)


def main():
    store = {}
    if os.path.exists(STORE):
        with open(STORE, encoding="utf-8") as f:
            store = json.load(f)

    new = 0
    for ev in load_feed():
        if ev.get("country", "").upper() not in CURRENCIES:
            continue
        if ev.get("impact", "").capitalize() not in IMPACTS:
            continue
        uid = uid_for(ev)
        new += uid not in store
        store[uid] = ev  # actualiza previsión / hora si cambió

    cutoff = datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)
    store = {u: e for u, e in store.items()
             if datetime.fromisoformat(e["date"]).astimezone(timezone.utc) >= cutoff}

    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//macro-eurusd-calendar//ES",
        "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
        "X-WR-CALNAME:Macro EUR/USD",
        "X-WR-CALDESC:Noticias macro de " + "/".join(sorted(CURRENCIES)) + " (fuente: Forex Factory)",
        "X-WR-TIMEZONE:America/Bogota",
        "REFRESH-INTERVAL;VALUE=DURATION:PT6H", "X-PUBLISHED-TTL:PT6H",
    ]
    for uid, ev in sorted(store.items(), key=lambda kv: kv[1]["date"]):
        start = datetime.fromisoformat(ev["date"]).astimezone(timezone.utc)
        end = start + timedelta(minutes=30)
        impact = ev.get("impact", "").capitalize()
        summary = f"{ICON.get(impact, '')} [{ev['country']}] {ev['title']}".strip()
        desc = (f"Impacto: {IMPACT_ES.get(impact, impact)}\n"
                f"Previsión: {ev.get('forecast') or '—'}\n"
                f"Previo: {ev.get('previous') or '—'}\n"
                f"Fuente: Forex Factory")
        lines += [
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{now}",
            f"DTSTART:{start.strftime('%Y%m%dT%H%M%SZ')}",
            f"DTEND:{end.strftime('%Y%m%dT%H%M%SZ')}",
            f"SUMMARY:{esc(summary)}",
            f"DESCRIPTION:{esc(desc)}",
            "TRANSP:TRANSPARENT",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")

    with open(OUTPUT, "w", encoding="utf-8", newline="") as f:
        f.write("\r\n".join(fold(l) for l in lines) + "\r\n")
    with open(STORE, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=1, sort_keys=True)

    print(f"OK: {len(store)} eventos en el calendario ({new} nuevos).")


if __name__ == "__main__":
    main()
