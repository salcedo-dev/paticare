"""PatiCare - lógica de conversación (sin WhatsApp, para poder probarla).

Quien la use debe darle una "salida" con dos métodos:
    salida.responder(texto)           -> contesta al mensaje que se está procesando
    salida.enviar(telefono, texto)    -> escribe a otra persona
main.py conecta esto con WhatsApp; los tests usan una salida falsa.
"""
import re
import unicodedata
from datetime import datetime
from zoneinfo import ZoneInfo

import db
import ia

LIMA = ZoneInfo("America/Lima")
PROPUESTA_TTL_HORAS = 12  # una propuesta sin confirmar caduca

SERVICIOS = [
    "🐾 Consulta veterinaria",
    "💉 Vacunación",
    "🛁 Baño y grooming",
    "🩺 Cirugía",
    "🦷 Limpieza dental",
    "✂️ Corte de uñas",
    "👁️ Oftalmología",
    "🔬 Análisis de laboratorio",
]

# Confirmar: el mensaje debe estar formado SOLO por palabras afirmativas (sin tildes ni signos).
# "sip dale" y "ya pe" confirman; "sí pero a las 5" no (es una corrección y va a la IA).
AFIRMA = {
    "si", "sip", "sipi", "ok", "okay", "dale", "vale", "claro", "confirmo", "confirmado", "enviar",
    "envialo", "enviala", "mandalo", "mandala", "ya", "acuerdo",
}
RELLENO = {"pe", "pues", "gracias", "de", "por", "favor", "porfa", "nomas"}
NO = {"no", "cancelar", "cancela", "cancelalo", "cancelala", "descartar", "descartalo", "no gracias"}
REEMPLAZAR = {"reemplazar", "reemplazala", "reemplazalo", "la nueva", "nueva", "cambiar", "cambiala"}
SEGUIR = {"seguir", "sigue", "continuar", "la anterior", "anterior"}

COMPLETADO_RE = re.compile(r"✅|listo|hecho|termin[eé]|ya lo hice|completo", re.I)
INCIDENCIA_RE = re.compile(r"⚠|problema|no pude|no puedo|fall[oó]|error|ocurri[oó]|incidencia", re.I)
PENDIENTE_RE = re.compile(r"(qu[eé]|cuales?).*(tengo|tener).*(pendiente|hacer|mis tareas)", re.I)
SERVICIOS_RE = re.compile(r"servicio|ofrecen|precio|costo|plan|paquete")  # sobre texto sin tildes
CITA_CLIENTE_RE = re.compile(r"agendar|cita|reserv|turno")

AYUDA_DUENO = (
    "Hola 🐾 Soy Pati. Cuéntame qué necesitas:\n"
    "• asignar tareas a tu equipo\n"
    "• recordarle algo a un cliente\n"
    "• agendar una cita"
)


def ahora():
    return datetime.now(LIMA)


def norm(texto):
    """Minúsculas, sin tildes y sin signos en los bordes: para comparar respuestas exactas."""
    t = unicodedata.normalize("NFD", texto.lower())
    t = "".join(ch for ch in t if unicodedata.category(ch) != "Mn")
    return " ".join(re.sub(r"[^\w\s]|_", " ", t).split())  # sin signos: "sí, dale" -> "si dale"


def _es_si(t):
    palabras = [_colapsar(w) for w in t.split()]  # 'siii' -> 'si'
    return bool(palabras) and all(w in AFIRMA | RELLENO for w in palabras) and any(w in AFIRMA for w in palabras)


def _en(t, conjunto):
    """¿La respuesta está en el conjunto? También acepta letras alargadas ('siii', 'okkk')."""
    return t in conjunto or _colapsar(t) in conjunto


def _colapsar(t):
    """'siii' -> 'si'."""
    return re.sub(r"(.)\1+", r"\1", t)


def _decir(salida, persona, texto):
    """Responde y guarda lo que Pati dijo (el historial le da contexto a la IA)."""
    db.log_mensaje(persona["id"], "pati", texto)
    salida.responder(texto)


def _fallo_ia(salida, persona, e):
    print(f"[ERROR IA] {type(e).__name__}: {e}")
    if isinstance(e, ia.GeminiError):
        _decir(salida, persona, e.mensaje_usuario)
    else:
        _decir(salida, persona, "Tuve un problema inesperado. Inténtalo de nuevo en un momento.")


# ── Propuestas del dueño: validar, mostrar ───────────────────────────────────

def _tel(valor):
    """Solo dígitos; un celular peruano de 9 dígitos recibe el 51. Si no parece teléfono, None."""
    if not valor:
        return None
    d = re.sub(r"\D", "", str(valor))
    if len(d) == 9 and d.startswith("9"):
        d = "51" + d
    return d if 10 <= len(d) <= 15 else None


def _normalizar(tipo, datos):
    """Python valida lo que propone la IA: responsables registrados y teléfonos."""
    if tipo == "tareas":
        reales = {w["nombre"].lower(): w["nombre"] for w in db.trabajadores()}
        for t in datos["tareas"]:
            t["responsable"] = reales.get((t.get("responsable") or "").lower())
        return datos
    datos["telefono"] = _tel(datos.get("telefono"))
    if not datos["telefono"] and datos.get("cliente"):
        fila = db.persona_por_nombre(datos["cliente"])
        if fila:
            datos["telefono"] = fila["telefono"]
    return datos


def faltantes(tipo, datos):
    """Lista de datos que impiden confirmar."""
    falta = []
    if tipo == "tareas":
        sin = [t["actividad"] for t in datos["tareas"] if not t.get("responsable")]
        if sin:
            falta.append("quién se encarga de: " + ", ".join(sin))
        return falta
    nombres = {
        "cliente": "el nombre del cliente", "telefono": "el teléfono", "mascota": "el nombre de la mascota",
        "servicio": "el servicio", "mensaje_a_enviar": "el mensaje",
    }
    campos = ("cliente", "telefono", "mensaje_a_enviar") if tipo == "recordatorio" else (
        "cliente", "telefono", "mascota", "servicio")
    for k in campos:
        if not datos.get(k):
            falta.append(nombres[k])
    if not datos.get("fecha_hora"):
        falta.append("la fecha y hora")
    elif datetime.strptime(datos["fecha_hora"], "%Y-%m-%d %H:%M") < ahora().replace(tzinfo=None):
        falta.append("la fecha y hora (la que entendí ya pasó)")
    return falta


def resumen_propuesta(prop):
    tipo, d = prop["tipo"], prop["datos"]
    if tipo == "tareas":
        lineas = ["📋 *Esto entendí:*"]
        for i, t in enumerate(d["tareas"], 1):
            quien = t.get("responsable") or "❓ sin responsable"
            extra = [x for x in (t.get("horario"), t.get("detalles")) if x]
            txt = f"{i}. {quien}: {t['actividad']}"
            if extra:
                txt += f" ({'; '.join(extra)})"
            lineas.append(txt)
    elif tipo == "recordatorio":
        lineas = [
            "🔔 *Recordatorio para un cliente:*",
            f"Para: {d.get('cliente') or '❓'}",
            f"Tel: {d.get('telefono') or '❓ sin teléfono'}",
            f"Fecha/hora: {d.get('fecha_hora') or '❓ sin fecha'}",
            f"Mensaje: {d.get('mensaje_a_enviar') or '❓'}",
        ]
    else:
        lineas = [
            "📅 *Cita:*",
            f"Cliente: {d.get('cliente') or '❓'}",
            f"Teléfono: {d.get('telefono') or '❓ sin teléfono'}",
            f"Mascota: {d.get('mascota') or '❓ sin mascota'}",
            f"Servicio: {d.get('servicio') or '❓ sin servicio'}",
            f"Fecha/hora: {d.get('fecha_hora') or '❓ sin fecha'}",
        ]
    return "\n".join(lineas)


def texto_propuesta(prop, pregunta_ia=None):
    """Resumen + qué falta o la pregunta de confirmación."""
    texto = resumen_propuesta(prop)
    falta = faltantes(prop["tipo"], prop["datos"])
    if falta:
        texto += "\n\n❓ " + (pregunta_ia or "¿Me das " + "; ".join(falta) + "?")
    else:
        verbo = {"tareas": "¿Las envío?", "recordatorio": "¿Lo programo?", "cita": "¿La agendo?"}[prop["tipo"]]
        texto += f"\n\n{verbo} Responde *SÍ* o *NO*."
    return texto


def _nueva_propuesta(tipo, datos):
    return {"tipo": tipo, "datos": datos, "creada": ahora().replace(tzinfo=None).isoformat()}


def _caducada(prop):
    creada = datetime.fromisoformat(prop["creada"])
    return (ahora().replace(tzinfo=None) - creada).total_seconds() > PROPUESTA_TTL_HORAS * 3600


def mensaje_trabajador(nombre, t):
    """Mensaje privado que Pati le manda al trabajador con su nueva tarea."""
    partes = [f"Hola {nombre} 🐾, soy Pati.", "", "Tienes una nueva tarea:", f"*{t['actividad']}*"]
    if t.get("detalles"):
        partes.append(t["detalles"])
    if t.get("horario"):
        partes.append(f"Horario: {t['horario']}")
    partes += ["", "Cuando termines, responde ✅ o cuéntame si hubo algún problema."]
    return "\n".join(partes)


# ── Flujo del dueño ───────────────────────────────────────────────────────────

def _historial_previo(pid, texto):
    """Historial sin el mensaje actual (que ya se guardó al llegar)."""
    h = db.historial(pid)
    if h and h[-1]["rol"] == "usuario" and h[-1]["texto"] == texto:
        h = h[:-1]
    return h


def flujo_dueno(salida, dueno, texto):
    pid = dueno["id"]
    t = norm(texto)
    estado = db.get_estado(pid)
    prop, alt = estado.get("propuesta"), estado.get("alterna")

    # Propuesta que caducó: se descarta y se avisa si el dueño intentaba confirmarla
    if prop and _caducada(prop):
        estado.pop("propuesta"), estado.pop("alterna", None)
        db.set_estado(pid, estado)
        prop = alt = None
        if _es_si(t):
            _decir(salida, dueno, "Esa propuesta ya caducó (pasaron más de "
                   f"{PROPUESTA_TTL_HORAS} horas). Cuéntamela de nuevo y la preparo.")
            return

    # Hay una propuesta nueva y una anterior sin resolver: hay que elegir
    if prop and alt:
        if _en(t, NO):
            estado.pop("propuesta"), estado.pop("alterna")
            db.set_estado(pid, estado)
            _decir(salida, dueno, "Listo, descarté las dos.")
        elif _en(t, REEMPLAZAR):
            estado["propuesta"] = estado.pop("alterna")
            db.set_estado(pid, estado)
            _decir(salida, dueno, texto_propuesta(estado["propuesta"]))
        elif _en(t, SEGUIR):
            estado.pop("alterna")
            db.set_estado(pid, estado)
            _decir(salida, dueno, "Ok, sigo con la anterior.\n\n" + texto_propuesta(prop))
        else:
            _decir(salida, dueno, "Primero dime: ¿*reemplazo* la propuesta anterior por la nueva "
                   "o *sigo* con la anterior?")
        return

    if prop:
        if _en(t, NO):
            estado.pop("propuesta")
            db.set_estado(pid, estado)
            _decir(salida, dueno, "Listo, la cancelé.")
            return
        if _es_si(t):
            _confirmar(salida, dueno, prop)
            return
    elif _es_si(t) or _en(t, NO):
        _decir(salida, dueno, "Ok 👍" if _en(t, NO) else
               "No tengo nada pendiente por confirmar. Cuéntame qué necesitas.")
        return

    # La IA interpreta; si falla, el estado queda intacto
    try:
        r = ia.interpretar_dueno(
            texto,
            [w["nombre"] for w in db.trabajadores()],
            _nombres_clientes(),
            _historial_previo(pid, texto),
            prop,
        )
    except Exception as e:
        _fallo_ia(salida, dueno, e)
        return

    if r["accion"] == "ninguna":
        resp = r["respuesta"] or AYUDA_DUENO
        if prop:
            resp += "\n\n(Sigo esperando tu *SÍ* o *NO* para la propuesta anterior.)"
        _decir(salida, dueno, resp)
        return

    nueva = _nueva_propuesta(r["tipo"], _normalizar(r["tipo"], r["datos"]))
    if prop and r["accion"] == "corregir" and prop["tipo"] == r["tipo"]:
        estado["propuesta"] = nueva  # reemplaza la anterior (no se duplica) y renueva su caducidad
    elif prop:  # pide otra cosa distinta: no se mezcla en silencio con la anterior
        estado["alterna"] = nueva
        db.set_estado(pid, estado)
        _decir(salida, dueno, "Tengo esta propuesta sin confirmar:\n\n" + resumen_propuesta(prop)
               + "\n\nY ahora me pides otra cosa. ¿La *reemplazo* por la nueva o *sigo* con la anterior?")
        return
    else:
        estado["propuesta"] = nueva
    db.set_estado(pid, estado)
    _decir(salida, dueno, texto_propuesta(estado["propuesta"], r["pregunta"]))


def _nombres_clientes():
    with db.conn() as c:
        return [f["nombre"] for f in c.execute("SELECT nombre FROM personas WHERE rol='cliente'")]


def _confirmar(salida, dueno, prop):
    falta = faltantes(prop["tipo"], prop["datos"])
    if falta:
        _decir(salida, dueno, "Todavía falta: " + "; ".join(falta) + ".")
        return
    try:
        res = db.confirmar_propuesta(dueno["id"])  # guarda y borra la propuesta en una transacción
    except Exception as e:
        print(f"[ERROR confirmar] {type(e).__name__}: {e}")
        _decir(salida, dueno, "No pude guardar eso. Sigue pendiente; intenta confirmar de nuevo.")
        return
    if res is None:  # ya se había confirmado (por ejemplo un segundo "sí")
        _decir(salida, dueno, "No tengo nada pendiente por confirmar.")
        return

    d = prop["datos"]
    if res["tipo"] == "tareas":
        enviadas, fallidas = [], []
        for it in res["items"]:
            w, t = it["trabajador"], it["tarea"]
            try:
                salida.enviar(w["telefono"], mensaje_trabajador(w["nombre"], t))
                enviadas.append(f"{w['nombre']}: {t['actividad']}")
            except Exception as e:
                print(f"[ERROR envío] tarea {it['tarea_id']} -> {w['nombre']}: {e}")
                db.registrar_envio_fallido(it["tarea_id"], str(e)[:200])
                fallidas.append(f"{w['nombre']}: {t['actividad']}")
        texto = ""
        if enviadas:
            texto += "✅ Envié estas tareas:\n" + "\n".join(f"• {x}" for x in enviadas)
        if fallidas:
            texto += "\n\n⚠️ Las guardé pero no pude enviar:\n" + "\n".join(f"• {x}" for x in fallidas)
        _decir(salida, dueno, texto.strip())
    elif res["tipo"] == "recordatorio":
        _decir(salida, dueno, f"✅ Recordatorio guardado. Lo enviaré a {d['cliente']} el {d['fecha_hora']}.")
    else:
        _decir(salida, dueno, f"✅ Cita agendada para {d['cliente']} el {d['fecha_hora']}. "
               "Le enviaré un recordatorio automático.")


# ── Flujo del trabajador ──────────────────────────────────────────────────────

def lista_tareas(tareas):
    lineas = []
    for i, t in enumerate(tareas, 1):
        estado = "" if t["estado"] == "pendiente" else f" [{t['estado']}]"
        lineas.append(f"{i}. {t['actividad']}{estado}")
    return "\n".join(lineas)


def _resumen_ids(tareas):
    return [{"id": t["id"], "actividad": t["actividad"]} for t in tareas]


def _notificar_duenos(salida, mensaje):
    for d in db.duenos():
        try:
            salida.enviar(d["telefono"], mensaje)
        except Exception as e:
            print(f"[ERROR notif dueño] {e}")


def flujo_trabajador(salida, persona, texto):
    pid = persona["id"]
    nombre = persona["nombre"]
    t = norm(texto)
    estado = db.get_estado(pid)
    esperando = estado.get("esperando")

    def limpiar():
        db.set_estado(pid, {})

    # Pasos de una conversación en curso
    if esperando in ("cual_tarea", "cual_incidencia"):
        if _en(t, NO):
            limpiar()
            _decir(salida, persona, "Ok, lo dejamos así.")
            return
        tareas = estado["tareas"]
        if texto.strip().isdigit() and 1 <= int(texto.strip()) <= len(tareas):
            elegida = tareas[int(texto.strip()) - 1]
            if esperando == "cual_tarea":
                limpiar()
                if db.completar_tarea(elegida["id"], pid):
                    _decir(salida, persona, f"✅ Listo, marqué *{elegida['actividad']}* como completada. "
                           f"¡Buen trabajo, {nombre}!")
                else:
                    _decir(salida, persona, "Esa tarea ya no está abierta.")
            else:
                db.set_estado(pid, {"esperando": "detalle_incidencia", "tarea_id": elegida["id"],
                                    "actividad": elegida["actividad"]})
                _decir(salida, persona, f"¿Qué pasó con *{elegida['actividad']}*?")
            return
        _decir(salida, persona, f"No entendí. Responde con el número (1 al {len(tareas)}).")
        return

    if esperando == "detalle_incidencia":
        limpiar()
        if db.incidencia_tarea(estado["tarea_id"], texto, pid):
            _decir(salida, persona, f"Anotado. Avisaré al dueño sobre el problema con *{estado['actividad']}*.")
            _notificar_duenos(
                salida,
                f"⚠️ *Incidencia reportada*\n{nombre}: {estado['actividad']}\nDetalle: {texto}",
            )
        else:
            _decir(salida, persona, "Esa tarea ya no está abierta.")
        return

    # Mensajes nuevos
    if PENDIENTE_RE.search(texto) or t in {"pendiente", "pendientes", "mis tareas", "que tengo"}:
        tareas = db.tareas_pendientes(pid)
        if not tareas:
            _decir(salida, persona, f"No tienes tareas pendientes, {nombre} 🎉")
        else:
            _decir(salida, persona, f"📋 Tus tareas pendientes:\n{lista_tareas(tareas)}")
        return

    if INCIDENCIA_RE.search(texto):
        tareas = db.tareas_pendientes(pid)
        if not tareas:
            _decir(salida, persona, "No tienes tareas abiertas ahora mismo.")
        elif len(tareas) == 1:
            db.set_estado(pid, {"esperando": "detalle_incidencia", "tarea_id": tareas[0]["id"],
                                "actividad": tareas[0]["actividad"]})
            _decir(salida, persona, f"Lo siento, {nombre}. ¿Qué pasó con *{tareas[0]['actividad']}*?")
        else:
            db.set_estado(pid, {"esperando": "cual_incidencia", "tareas": _resumen_ids(tareas)})
            _decir(salida, persona, f"¿El problema es con cuál tarea?\n\n{lista_tareas(tareas)}\n\n"
                   "Responde con el número.")
        return

    if COMPLETADO_RE.search(texto):
        tareas = db.tareas_pendientes(pid)
        if not tareas:
            _decir(salida, persona, f"No tienes tareas pendientes ahora mismo, {nombre}.")
        elif len(tareas) == 1:
            db.completar_tarea(tareas[0]["id"], pid)
            _decir(salida, persona, f"✅ ¡Perfecto, {nombre}! Marqué *{tareas[0]['actividad']}* como completada.")
        else:
            db.set_estado(pid, {"esperando": "cual_tarea", "tareas": _resumen_ids(tareas)})
            _decir(salida, persona, f"Tienes varias tareas. ¿Cuál completaste?\n\n{lista_tareas(tareas)}\n\n"
                   "Responde con el número.")
        return

    _decir(
        salida, persona,
        "Hola 🐾 No entendí bien. Puedes responderme:\n"
        "• ✅ o *listo* para completar una tarea\n"
        "• ⚠️ o *problema* si hubo un inconveniente\n"
        "• *qué tengo pendiente* para ver tus tareas",
    )


# ── Flujo del cliente ─────────────────────────────────────────────────────────

def flujo_cliente(salida, persona, texto):
    t = norm(texto)
    if SERVICIOS_RE.search(t):
        _decir(salida, persona, "🐾 *Servicios disponibles:*\n\n" + "\n".join(SERVICIOS)
               + "\n\n¿Te interesa agendar una cita? Escribe *agendar cita* y te derivaremos con un asesor.")
        return
    if CITA_CLIENTE_RE.search(t):
        _decir(salida, persona, "📅 *Agendamiento de citas*\n\nPronto te derivaremos con un asesor "
               "que te ayudará con los detalles.")
        _notificar_duenos(
            salida,
            f"📞 Un cliente quiere agendar una cita:\n{persona['nombre']} ({persona['telefono']})\nMensaje: {texto}",
        )
        return
    _decir(salida, persona, "Hola 🐾 Puedo ayudarte con:\n• *Servicios* - ver qué ofrecemos\n"
           "• *Agendar cita* - reservar una consulta\n\n¿Qué necesitas?")


# ── Entrada única ─────────────────────────────────────────────────────────────

def procesar(salida, persona, texto):
    """Atiende un mensaje de una persona registrada según su rol."""
    db.log_mensaje(persona["id"], "usuario", texto)
    if persona["rol"] == "maestro":
        flujo_dueno(salida, persona, texto)
    elif persona["rol"] == "trabajador":
        flujo_trabajador(salida, persona, texto)
    elif persona["rol"] == "cliente":
        flujo_cliente(salida, persona, texto)


# ── Resumen diario ────────────────────────────────────────────────────────────

def texto_resumen_diario():
    """Texto del resumen de fin de día para el dueño."""
    datos = db.resumen_diario()
    lineas = [f"📊 *Resumen del día {ahora().strftime('%d/%m/%Y')}*", ""]
    atencion = []
    for w in datos:
        lineas.append(f"👤 *{w['nombre']}*")
        lineas.append(f"  Asignadas: {w['asignadas']} | Completadas: {w['completadas']} | Pendientes: {w['pendientes']}")
        for inc in w["incidencias"]:
            lineas.append(f"  ⚠️ {inc}")
        if w["incidencias"]:
            atencion.append(w["nombre"])
        lineas.append("")
    if atencion:
        lineas.append("🔴 *Requiere tu atención:*")
        lineas += [f"  • {n} reportó una incidencia" for n in atencion]
    else:
        lineas.append("✅ Todo en orden, sin incidencias.")
    return "\n".join(lineas)
