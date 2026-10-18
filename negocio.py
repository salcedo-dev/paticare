"""PatiCare - perfil del negocio (rubro, servicios, textos). Se lee de <carpeta de datos>/negocio.json.

El mismo bot sirve para varios rubros: el rubro solo cambia el vocabulario. Los servicios y el
horario SIEMPRE los da el negocio; Pati no inventa ninguno.
"""
import json
import os
import re

import agenda
import db

# Vocabulario por rubro. Para un rubro nuevo basta con agregar una entrada.
PERFILES = {
    "veterinaria": {
        "rubro_texto": "una veterinaria pequeña", "sujeto": "mascota",
        "el_sujeto": "la mascota", "del_sujeto": "de la mascota", "Sujeto": "Mascota",
    },
    "dentista": {
        "rubro_texto": "un consultorio dental", "sujeto": "paciente",
        "el_sujeto": "el paciente", "del_sujeto": "del paciente", "Sujeto": "Paciente",
        "sujeto_es_cliente": True,  # el paciente es quien agenda, salvo que se diga otro nombre
    },
    "generico": {
        "rubro_texto": "un negocio pequeño", "sujeto": "cliente",
        "el_sujeto": "el cliente", "del_sujeto": "del cliente", "Sujeto": "Cliente",
    },
}


def _entero(valor, defecto, minimo, maximo):
    try:
        return max(minimo, min(int(valor), maximo))
    except (TypeError, ValueError):
        return defecto


def _hora(valor, defecto):
    """"HH:MM" válida o el valor por defecto."""
    try:
        agenda.a_minutos(valor)
        return str(valor).strip()
    except (ValueError, AttributeError):
        return defecto


def config():
    """Configuración vigente. Se lee en cada uso: editar negocio.json no exige reiniciar."""
    datos = {}
    ruta = os.path.join(db.DATA_DIR, "negocio.json")
    if os.path.exists(ruta):
        try:
            with open(ruta, encoding="utf-8") as f:
                datos = json.load(f)
        except (OSError, ValueError) as e:
            print(f"[AVISO] negocio.json ilegible ({type(e).__name__}); uso valores por defecto")
    rubro = datos.get("rubro") or "veterinaria"
    perfil = PERFILES.get(rubro) or PERFILES["generico"]
    servicios = datos.get("servicios")
    detalle = []  # cada servicio con su duración (los servicios pueden ser "Nombre" o {"nombre": ..., "duracion_min": ...})
    for item in servicios if isinstance(servicios, list) else []:
        if isinstance(item, dict) and item.get("nombre"):
            try:
                dur = max(5, int(item.get("duracion_min", 30)))
            except (TypeError, ValueError):
                dur = 30
            detalle.append({"nombre": str(item["nombre"]), "duracion_min": dur})
        elif isinstance(item, str) and item.strip():
            detalle.append({"nombre": item.strip(), "duracion_min": 30})
    ignorar = datos.get("ignorar")
    lineas = datos.get("lineas") if isinstance(datos.get("lineas"), dict) else {}
    horas = datos.get("horas_reporte")
    horas = horas if isinstance(horas, list) else []
    seg = datos.get("seguimiento") if isinstance(datos.get("seguimiento"), dict) else {}
    enc = datos.get("encuestas") if isinstance(datos.get("encuestas"), dict) else {}
    chk = datos.get("checkin") if isinstance(datos.get("checkin"), dict) else {}
    return {
        # Seguimiento automático de tareas con plazo: recordatorio al trabajador y aviso al dueño si vence sin completar
        "seguimiento": {
            "activo": seg.get("activo", True) is not False,
            "recordar_min_antes": _entero(seg.get("recordar_min_antes"), 15, 0, 1440),
            "gracia_min": _entero(seg.get("gracia_min"), 10, 0, 1440),
            "avisos_desde": _hora(seg.get("avisos_desde"), "07:00"),  # fuera de esta ventana no se envían avisos
            "avisos_hasta": _hora(seg.get("avisos_hasta"), "21:00"),
        },
        **perfil,
        "rubro": rubro,
        "nombre": datos.get("nombre") or "nuestro negocio",
        "servicios": [d["nombre"] for d in detalle],
        "servicios_detalle": detalle,
        # Si el negocio no configuró su agenda, Pati NO agenda sola (no inventa horarios): deriva a una persona
        "agenda": agenda.configurar(datos.get("agenda")),
        # Avisar a quien atiende cuando un cliente agenda o cancela una cita con Pati (por defecto sí)
        "avisar_citas": datos.get("avisar_citas", True) is not False,
        "horario": datos.get("horario") or None,
        "atender_desconocidos": datos.get("atender_desconocidos", True) is not False,
        "ignorar": [str(x) for x in ignorar] if isinstance(ignorar, list) else [],
        # Avisar al dueño apenas alguien completa una tarea (por defecto sí)
        "avisar_completadas": datos.get("avisar_completadas", True) is not False,
        "botones": datos.get("botones", True) is not False,  # botones de WhatsApp en los avisos al equipo
        # Encuestas al equipo: cuántos minutos esperar antes de recordarle UNA vez a quien no votó (0 = no recordar)
        "encuestas": {"recordar_min": _entero(enc.get("recordar_min"), 30, 0, 1440)},
        # Check-in diario ("¿cómo va tu día?") al equipo, de lunes a sábado a esa hora; apagado si no se configura
        "checkin_hora": (str(chk["hora"]).strip().zfill(5) if re.fullmatch(r"\s*([01]?\d|2[0-3]):[0-5]\d\s*", str(chk.get("hora", "")))
                         else None),
        # Horas del día (Lima) en que se envía al dueño un reporte del estado de las tareas, ej. ["13:00", "17:00"]
        "horas_reporte": [h.strip() for h in horas
                          if isinstance(h, str) and re.fullmatch(r"\s*([01]?\d|2[0-3]):[0-5]\d\s*", h)],
        # Números de las líneas de Pati (solo dígitos): central y corporativa
        "lineas": {k: re.sub(r"\D", "", str(v)) for k, v in lineas.items() if v},
    }
