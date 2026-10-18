"""PatiCare - agenda: horarios disponibles para citas.

Lógica pura (sin WhatsApp ni base de datos): se prueba sola. Pati solo agenda si el negocio configuró su
agenda en negocio.json; si no, no inventa horarios y deriva a una persona.
"""
import re
import unicodedata
from datetime import date, datetime, timedelta

NOMBRES_DIA = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]  # 0 = lunes
ABREV_DIA = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]


def _sin_tildes(texto):
    t = unicodedata.normalize("NFD", str(texto).lower())
    return "".join(ch for ch in t if unicodedata.category(ch) != "Mn").strip()


_INDICE_DIA = {_sin_tildes(n): i for i, n in enumerate(NOMBRES_DIA)}


def a_minutos(hhmm):
    h, m = str(hhmm).strip().split(":")
    h, m = int(h), int(m)
    if not (0 <= h <= 24 and 0 <= m < 60):
        raise ValueError("hora fuera de rango")
    return h * 60 + m


def a_hhmm(minutos):
    return f"{minutos // 60:02d}:{minutos % 60:02d}"


def configurar(datos):
    """Valida la sección "agenda" de negocio.json. Devuelve un dict listo para usar, o None si no se puede agendar."""
    if not isinstance(datos, dict):
        return None
    try:
        dias = sorted({_INDICE_DIA[_sin_tildes(d)] for d in datos["dias"]})
        desde, hasta = a_minutos(datos["desde"]), a_minutos(datos["hasta"])
        paso = max(5, min(int(datos.get("paso_min", 30)), 240))
        simultaneas = max(1, int(datos.get("simultaneas", 1)))
        anticipacion = max(0.0, float(datos.get("anticipacion_horas", 2)))
        adelante = max(1, min(int(datos.get("dias_adelante", 14)), 60))
        cerrado = {str(x).strip() for x in datos.get("cerrado", []) if x}
    except (KeyError, ValueError, TypeError, AttributeError):
        return None
    if not dias or hasta <= desde:
        return None
    return {"dias": dias, "desde": desde, "hasta": hasta, "paso": paso, "simultaneas": simultaneas,
            "anticipacion_h": anticipacion, "dias_adelante": adelante, "cerrado": cerrado}


def huecos(cfg, dia, duracion, ocupadas, ahora):
    """Horas de inicio ("HH:MM") libres en un día.

    ocupadas: [(inicio_min, fin_min)] de las citas ya agendadas ese día. ahora: datetime sin zona horaria.
    """
    if dia.weekday() not in cfg["dias"] or dia.isoformat() in cfg["cerrado"]:
        return []
    limite = ahora + timedelta(hours=cfg["anticipacion_h"])
    libres, inicio = [], cfg["desde"]
    while inicio + duracion <= cfg["hasta"]:
        if datetime.combine(dia, datetime.min.time()) + timedelta(minutes=inicio) >= limite:
            solapadas = sum(1 for a, b in ocupadas if a < inicio + duracion and b > inicio)
            if solapadas < cfg["simultaneas"]:
                libres.append(a_hhmm(inicio))
        inicio += cfg["paso"]
    return libres


def dias_con_huecos(cfg, duracion, ocupadas_de, ahora, maximo=5):
    """Los próximos días (hasta `maximo`) que tienen al menos un horario libre. ocupadas_de(dia) -> [(ini, fin)]."""
    salida = []
    for i in range(cfg["dias_adelante"] + 1):
        dia = ahora.date() + timedelta(days=i)
        if huecos(cfg, dia, duracion, ocupadas_de(dia), ahora):
            salida.append(dia)
            if len(salida) == maximo:
                break
    return salida


def etiqueta_dia(dia, hoy):
    base = f"{ABREV_DIA[dia.weekday()]} {dia.day:02d}/{dia.month:02d}"
    if dia == hoy:
        return f"Hoy ({base})"
    if dia == hoy + timedelta(days=1):
        return f"Mañana ({base})"
    return base


def parse_dia(texto, hoy):
    """Entiende "hoy", "mañana", "pasado mañana", "lunes", "25/10", "25-10-2030". Devuelve un date o None."""
    t = _sin_tildes(texto)
    if t in ("hoy",):
        return hoy
    if t in ("manana",):
        return hoy + timedelta(days=1)
    if t in ("pasado manana", "pasado"):
        return hoy + timedelta(days=2)
    if t in _INDICE_DIA:
        return hoy + timedelta(days=(_INDICE_DIA[t] - hoy.weekday()) % 7)
    m = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?", t)
    if m:
        d, mes = int(m.group(1)), int(m.group(2))
        anio = int(m.group(3)) if m.group(3) else hoy.year
        anio += 2000 if anio < 100 else 0
        try:
            dia = date(anio, mes, d)
        except ValueError:
            return None
        return dia if dia >= hoy else None
    return None


def parse_hora(texto):
    """Entiende "15:30", "3pm", "3 pm", "9am", "15". Devuelve "HH:MM" o None. Sin am/pm, 1 a 7 se toma como tarde."""
    t = _sin_tildes(texto).replace(".", "")
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm|a m|p m)?", t)
    if not m:
        return None
    h, minutos, sufijo = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").replace(" ", "")
    if minutos > 59 or h > 23:
        return None
    if sufijo == "pm" and h < 12:
        h += 12
    elif sufijo == "am" and h == 12:
        h = 0
    elif not sufijo and 1 <= h <= 7:
        h += 12
    return f"{h:02d}:{minutos:02d}"
