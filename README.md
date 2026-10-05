[build_calendar.py](https://github.com/user-attachments/files/33067700/build_calendar.py)
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
import re
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


# ----------------------------------------------------------------------
# Explicaciones educativas (en español) para cada tipo de noticia
#   tipo "dato":    dato mayor que la previsión = bueno para la moneda
#   tipo "inverso": dato mayor que la previsión = MALO para la moneda
#   tipo "tono":    no hay número; importa si el mensaje es hawkish o dovish
#   tipo "tasa":    decisión de tasas de interés
# El orden importa: la primera regla que coincida gana.
# ----------------------------------------------------------------------
RULES = [
    # --- Bancos centrales ---
    (r"Federal Funds Rate", "tasa", "Decisión de tasas de la Fed. Es el evento más importante para el USD."),
    (r"Main Refinancing Rate|Deposit Facility Rate", "tasa", "Decisión de tasas del BCE. Es el evento más importante para el EUR."),
    (r"FOMC Meeting Minutes", "tono", "Actas detalladas de la última reunión de la Fed (salen ~3 semanas después)."),
    (r"FOMC Statement|FOMC Press Conference|FOMC Economic Projections", "tono", "Comunicado, rueda de prensa o proyecciones de la Fed tras su decisión de tasas."),
    (r"Monetary Policy Meeting Accounts", "tono", "Actas detalladas de la última reunión del BCE."),
    (r"Monetary Policy Statement|ECB Press Conference", "tono", "Comunicado y rueda de prensa del BCE tras su decisión de tasas."),
    (r"Speaks|Testifies", "tono", "Discurso de un miembro del banco central. Busca pistas sobre las próximas decisiones de tasas."),
    # --- Empleo ---
    (r"ADP Non-Farm", "dato", "Estimación privada de empleo (ADP). Se usa como anticipo del NFP, aunque no siempre coinciden."),
    (r"Non-Farm Employment Change", "dato", "Empleos creados en el mes fuera del sector agrícola (NFP). El dato de empleo más importante del mes."),
    (r"Unemployment Claims", "inverso", "Personas que pidieron subsidio de desempleo por primera vez en la semana. Termómetro semanal del empleo."),
    (r"Unemployment Rate", "inverso", "Porcentaje de la fuerza laboral que no tiene empleo."),
    (r"Average Hourly Earnings|Wage", "dato", "Crecimiento de los salarios. Salarios altos = presión inflacionaria = banco central más duro."),
    (r"JOLTS", "dato", "Vacantes de empleo disponibles. Mide cuánta demanda de trabajadores hay."),
    # --- Inflación ---
    (r"Core PCE", "dato", "Inflación PCE subyacente: el indicador de inflación favorito de la Fed."),
    (r"Inflation Expectations", "dato", "Inflación que esperan los consumidores. Si sube, preocupa al banco central."),
    (r"Core CPI", "dato", "Inflación al consumidor SIN alimentos ni energía. La que más mira el banco central."),
    (r"CPI|HICP", "dato", "Inflación al consumidor (IPC). Inflación alta = banco central más duro (tasas altas)."),
    (r"PPI", "dato", "Inflación al productor. Anticipa la inflación al consumidor."),
    # --- Actividad / crecimiento ---
    (r"ISM Services", "dato", "Encuesta a gerentes del sector servicios (~80% del PIB de EE.UU.). >50 expansión, <50 contracción."),
    (r"ISM Manufacturing", "dato", "Encuesta a gerentes del sector manufacturero. >50 expansión, <50 contracción."),
    (r"PMI", "dato", "Encuesta a gerentes de compras (PMI). >50 expansión, <50 contracción. La versión Flash mueve más que la Final."),
    (r"GDP", "dato", "PIB: cuánto creció la economía en el trimestre."),
    (r"Retail Sales", "dato", "Ventas minoristas: mide el consumo de los hogares."),
    (r"Durable Goods", "dato", "Pedidos de bienes duraderos: refleja la inversión de las empresas."),
    (r"Industrial Production", "dato", "Producción de fábricas, minas y servicios públicos."),
    (r"Factory Orders", "dato", "Pedidos a las fábricas: anticipa la producción futura."),
    (r"Empire State|Philly Fed", "dato", "Encuesta manufacturera regional de EE.UU. Anticipa el ISM."),
    (r"Trade Balance", "dato", "Exportaciones menos importaciones."),
    (r"Home|Housing|Building Permits", "dato", "Dato del sector vivienda, muy sensible a las tasas de interés."),
    # --- Sentimiento ---
    (r"ifo", "dato", "Clima empresarial en Alemania (ifo), la mayor economía de la Eurozona."),
    (r"ZEW", "dato", "Expectativas de inversores institucionales sobre Alemania y la Eurozona."),
    (r"Sentix", "dato", "Confianza de los inversores en la Eurozona."),
    (r"Consumer Sentiment|Consumer Confidence", "dato", "Confianza del consumidor: qué tan optimistas están los hogares."),
    (r"Bank Holiday", "feriado", "Feriado bancario: hay menos liquidez y el precio se puede mover de forma errática."),
]

STRONG_DIR = {"USD": "BAJAR", "EUR": "SUBIR"}   # si la moneda se fortalece, EUR/USD tiende a...
WEAK_DIR = {"USD": "SUBIR", "EUR": "BAJAR"}
BANK = {"USD": "la Fed", "EUR": "el BCE"}

RISK_RULE = {
    "High": "No abras posiciones 30 min antes ni 15 min después. Los spreads se amplían y se barren stops. "
            "Si ya estás dentro, protege (stop a break-even o reduce). Espera el cierre de la vela de 15 min "
            "y lee la reacción dentro de tu contexto MATVARD.",
    "Medium": "Evita entrar 15 min antes. Si ya estás dentro, revisa tu stop. Solo mueve el precio si sorprende "
              "mucho frente a la previsión: no cambies tu sesgo técnico por este dato, úsalo como contexto.",
    "Low": "Rara vez mueve el mercado por sí solo. Úsalo solo como contexto.",
    "Holiday": "Menos volumen: cuidado con rupturas falsas.",
}


def explain(ev):
    title, cur = ev.get("title", ""), ev.get("country", "").upper()
    kind, what = "dato", ""
    for pattern, k, text in RULES:
        if re.search(pattern, title, re.I):
            kind, what = k, text
            break
    strong, weak = STRONG_DIR.get(cur, "?"), WEAK_DIR.get(cur, "?")
    if kind == "dato":
        how = (f"Dato MAYOR que la previsión = bueno para el {cur} = EUR/USD tiende a {strong}. "
               f"Dato MENOR = malo para el {cur} = EUR/USD tiende a {weak}.")
    elif kind == "inverso":
        how = (f"⚠️ Indicador INVERSO. Dato MENOR que la previsión = bueno para el {cur} = EUR/USD tiende a {strong}. "
               f"Dato MAYOR = malo para el {cur} = EUR/USD tiende a {weak}.")
    elif kind == "tono":
        how = (f"No hay número: importa el TONO. Hawkish (preocupación por inflación, tasas altas por más tiempo) "
               f"= {cur} fuerte = EUR/USD tiende a {strong}. Dovish (recortes, preocupación por el crecimiento) "
               f"= {cur} débil = EUR/USD tiende a {weak}.")
    elif kind == "tasa":
        how = (f"Compara la decisión con lo esperado. Si {BANK.get(cur, 'el banco central')} sube tasas o anuncia "
               f"más subidas = {cur} fuerte = EUR/USD tiende a {strong}. Si recorta o anuncia recortes = EUR/USD tiende a {weak}. "
               f"Ojo: si la decisión ya estaba descontada, lo que más mueve es el comunicado y la rueda de prensa.")
    else:
        how = ""
    return what, how


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
        what, how = explain(ev)
        parts = [f"Impacto: {IMPACT_ES.get(impact, impact)} {ICON.get(impact, '')}",
                 f"Previsión: {ev.get('forecast') or '—'} | Previo: {ev.get('previous') or '—'}"]
        if what:
            parts.append(f"📊 Qué mide: {what}")
        if how:
            parts.append(f"📖 Cómo leerlo: {how}")
        if impact in RISK_RULE:
            parts.append(f"🛡️ Regla: {RISK_RULE[impact]}")
        parts.append("🔴 alto · 🟡 medio · ⚪ bajo | Fuente: Forex Factory")
        desc = "\n\n".join(parts)
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
