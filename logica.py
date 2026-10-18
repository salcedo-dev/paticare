"""PatiCare - lógica de conversación (sin WhatsApp, para poder probarla).

Quien la use debe darle una "salida" con dos métodos y un atributo:
    salida.responder(texto)           -> contesta al mensaje que se está procesando (por la misma línea)
    salida.enviar(telefono, texto)    -> escribe a otra persona por la línea CENTRAL (avisos internos)
    salida.linea                      -> "central" (dueño y equipo) o "corporativa" (clientes del negocio)
main.py conecta esto con WhatsApp; los tests usan una salida falsa.
"""
import re
import time
import unicodedata
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import agenda
import db
import ia
import negocio

LIMA = ZoneInfo("America/Lima")
PROPUESTA_TTL_HORAS = 12  # una propuesta sin confirmar caduca

PAUSA_HUMANO_MIN = 5          # Pati se calla en un chat hasta 5 min después del último mensaje de una persona
MAX_RESPUESTAS_CLIENTE_HORA = 10  # tope de respuestas por cliente y hora (evita bucles y spam)

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
# Para elegir entre dos propuestas se acepta cualquier frase con la palabra: "sí reemplaza", "reemplázala", "sigo con la anterior"
REEMPLAZAR_PALABRAS = ("reemplaz", "cambia", "nueva")
SEGUIR_PALABRAS = ("segui", "sigue", "sigo", "continu", "anterior", "mantene")

COMPLETADO_RE = re.compile(r"✅|listo|hecho|termin[eé]|ya lo hice|completo", re.I)
INCIDENCIA_RE = re.compile(r"⚠|problema|no pude|no puedo|fall[oó]|error|ocurri[oó]|incidencia", re.I)
PENDIENTE_RE = re.compile(r"(qu[eé]|cuales?).*(tengo|tener).*(pendiente|hacer|mis tareas)", re.I)
SERVICIOS_RE = re.compile(r"servicio|ofrecen|precio|costo|plan|paquete|horario|atienden")  # texto sin tildes
CITA_CLIENTE_RE = re.compile(r"agendar|cita|reserv|turno")
PERSONA_RE = re.compile(r"persona|asesor|humano|alguien|recepcion|hablar con|llamen|llamar")

MAX_EDAD_MENSAJE_SEG = 600  # mensajes más viejos que esto (historial, caídas) se ignoran
MAX_EDAD_PROPIO_SEG = 120   # un mensaje "propio" más viejo que esto es historial, no una persona escribiendo

# Mensajes cortos con los que alguien que también recibe tareas (ej. el dueño) responde como trabajador
INTENCION_TRABAJADOR = {
    "listo", "hecho", "terminado", "completado", "termine", "ya termine", "ya lo hice", "problema",
    "tengo un problema", "pendientes", "mis tareas", "que tengo", "que tengo pendiente", "mas tiempo",
    "necesito mas tiempo",
}

# Frases claras con las que el dueño pregunta por el estado de las tareas (texto sin tildes). Responde Pati con
# datos reales, sin IA. Lo demás ("no, haz el reporte sin consultarles"...) lo clasifica la IA con el contexto.
CONSULTA_ESTADO_RES = [
    re.compile(r"\bcomo (va|van|vamos|andan|anda)\b"),
    re.compile(r"\b(quien|quienes|cuales|cuantos|cuantas)\b.*\b(termin|culmin|complet|cumpl|hizo|hicieron|hecho|acab|falta|pendient)"),
    re.compile(r"\bque (le |les )?(falta|faltan)\b"),
    re.compile(r"\b(reporte|estado|avance|resumen|informe)\b.*\b(tareas|equipo|todos|pendientes|todo)\b"),
    re.compile(r"\b(tareas|pendientes)\b.*\b(de todos|del equipo|de hoy|del dia)\b"),
    re.compile(r"^(dame |hazme |haz |manda |mandame |enviame )?(el |un )?(reporte|avance|informe|estado)( por favor)?$"),
]

# Preguntas de ayuda ("qué funciones tienes", "para qué sirves", "/ayuda"...). Pati responde con textos fijos que
# listan SOLO lo que de verdad puede hacer (texto sin tildes). Sin IA: no puede inventar funciones.
AYUDA_EXACTA = {"ayuda", "help", "menu", "comandos", "opciones", "instrucciones", "ayudame", "necesito ayuda", "info"}
AYUDA_RES = [
    re.compile(r"\b(que|cuales)\b.*\b(funciones|funcion|comandos|opciones|haces|sabes hacer|puedes hacer|puedes)\b"),
    re.compile(r"\bpara que (sirves|sirve pati|eres)\b"),
    re.compile(r"\b(quien|que) (eres|es pati)\b"),
    re.compile(r"\bcomo (funcionas|te uso|se usa|funciona pati|funciona esto)\b"),
    re.compile(r"\bque puedo (hacer|pedirte|decirte|escribirte|preguntarte)\b"),
    re.compile(r"\b(explicame|ensename|dime)\b.*\b(como funcionas|que haces|que eres|como usarte)\b"),
    re.compile(r"\bcomo (completo|termino|marco|cierro|finalizo|reporto|aviso|informo|veo|consulto)\b"),
]
TEMAS_AYUDA_TRABAJADOR = [
    (re.compile(r"\bcomo (completo|termino|marco|cierro|finalizo)\b"),
     "Para completar una tarea escribe *listo* (o ✅). Si tienes varias, te muestro la lista y me respondes con el "
     "número de la que terminaste."),
    (re.compile(r"\bcomo (reporto|aviso|informo)\b"),
     "Si algo salió mal escribe *problema* (o ⚠️). Te pregunto qué pasó y se lo aviso al dueño de inmediato."),
    (re.compile(r"\bcomo (veo|consulto)\b"),
     "Escribe *qué tengo pendiente* y te muestro tus tareas abiertas, numeradas."),
]


def _es_pedido_ayuda(t):
    return len(t) <= 80 and (t in AYUDA_EXACTA or any(r.search(t) for r in AYUDA_RES))


def texto_ayuda_trabajador(persona, t=""):
    """Qué puede hacer Pati por un trabajador (o una explicación corta si preguntó por algo concreto)."""
    for patron, texto in TEMAS_AYUDA_TRABAJADOR:
        if patron.search(t):
            return texto + "\n\nEscribe *ayuda* para ver todo lo que puedo hacer."
    lineas = [
        f"Hola {persona['nombre']} 🐾 Te aviso aquí de tus tareas. Respóndeme:",
        "✅ *listo* · ⚠️ *problema* · ⏳ *más tiempo* · 📋 *pendientes*",
    ]
    if "atencion" in _extras(persona):  # quien atiende a los clientes tiene funciones extra
        lineas.append("📅 *citas hoy* (o mañana, esta semana) · te aviso si un cliente pide hablar con una persona")
    lineas.append("Solo veo tus propias tareas; lo demás que me escribas se lo paso al dueño.")
    return "\n".join(lineas)


def texto_ayuda_dueno(persona):
    """Qué puede hacer Pati por el dueño. Solo lista funciones que existen."""
    neg = negocio.config()
    avisos = "te aviso cuando alguien completa una tarea o reporta un problema"
    if neg["horas_reporte"]:
        avisos += f", te envío el estado de las tareas a las {', '.join(neg['horas_reporte'])}"
    avisos += " y el resumen del día a las 7:00 p. m"
    atencion = [p["nombre"] for p in db.personas_con_extra("atencion")]
    quien = " y ".join(atencion) if atencion else "ti"
    lineas = [
        f"Hola {persona['nombre']} 🐾 Soy Pati. Escríbeme como hablas, aunque sea desordenado. Esto puedo hacer:\n",
        "• *Tareas:* \"Mafer limpia el quirófano y Ashly baña al perro pa las 5\". Te muestro lo que entendí y las envío "
        "cuando confirmes con SÍ. Puedes corregir antes (\"mejor a las 6\").",
        "• *Recordatorios y citas para clientes:* \"recuérdale a Lucía mañana a las 10 su cita\". También los confirmas "
        "antes de que salgan.",
        "• *Estado del equipo:* \"¿cómo va todo?\", \"¿cómo va Mafer?\", \"¿quiénes ya terminaron?\".",
        "• *Agenda:* \"¿qué citas hay mañana?\", \"citas de hoy\", \"citas de esta semana\".",
        f"• *Avisos y reportes:* {avisos}.",
        "• *Plazos:* si dices una hora (\"para las 5\"), recuerdo a la persona antes de que venza y te aviso si no "
        "lo completa a tiempo.",
        "• *Control:* \"cancela lo de Mafer\", \"pasa lo de Mafer a Xiomara\", \"cambia el plazo de Mafer a las 6\", "
        "\"recuérdale a Mafer\", \"avisa al equipo que mañana abrimos tarde\", \"historial de Mafer\" y *deshacer* "
        "(las tareas que acabo de enviar). Avisan a la gente, así que antes me pides tu SÍ.",
        "• *Al equipo:* \"recuérdales sus tareas pendientes\" (a cada uno solo las suyas), \"las incidencias ya están "
        "resueltas\" (vuelven a pendiente y les aviso) y \"cancela todas las tareas pendientes\". Siempre con tu SÍ.",
        "• *Comandos:* si prefieres órdenes exactas, escribe *.ayuda* y te muestro la lista (empiezan con punto: "
        "*.tarea*, *.encuesta*, *.estado*, *.resultados*, *.recordar*...).",
        "• *Encuestas al equipo:* \"encuesta ¿Quién cubre el sábado? opciones: yo, no puedo\" o \"pregúntale al equipo si "
        "pueden quedarse hasta las 8\". Tocan una opción en WhatsApp y te mando los resultados. \"¿Cómo va la encuesta?\" "
        "te dice quién votó y quién falta.",
    ]
    if neg.get("agenda"):
        lineas.append(f"• *Clientes:* atiendo el número del negocio: les muestro los servicios, les agendo citas en los "
                      f"horarios libres (también pueden cancelarlas o cambiarlas) y aviso a {quien} de cada cita y si "
                      "piden hablar con una persona. Les pregunto su nombre y el de su mascota para ir armando su ficha.")
    else:
        lineas.append(f"• *Clientes:* atiendo el número del negocio con un menú de servicios y aviso a {quien} si piden "
                      "hablar con una persona.")
    if _es_asignable(persona):
        lineas.append("• *Tus tareas:* también puedes asignártelas (\"yo reviso la caja\"); responde *listo* o *problema* como el equipo.")
    lineas.append("\nNunca envío nada a otras personas sin tu confirmación y no invento datos: si falta algo, te pregunto.")
    return "\n".join(lineas)


# Preguntas sobre la agenda de citas ("qué citas hay mañana"). Sin dígitos, para no confundirlas con una orden como
# "cita para mañana a las 10 de luna" (texto sin tildes).
CONSULTA_AGENDA_RES = [
    re.compile(r"\b(que|cuales|cuantas|cuantos)\b.*\bcitas?\b"),
    re.compile(r"\bcitas? (de|del|para)( el| la)? (hoy|manana|pasado manana|semana|esta semana|lunes|martes|miercoles|jueves|viernes|sabado|domingo)\b"),
    re.compile(r"\bagenda (de|del|para)( el| la)? (hoy|manana|pasado manana|semana|esta semana|lunes|martes|miercoles|jueves|viernes|sabado|domingo)\b"),
    re.compile(r"^(las )?(citas|agenda|mi agenda)( de hoy| de manana| de la semana)?$"),
]

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


def _elige(t, buscadas, contrarias):
    """¿La respuesta menciona una de las palabras `buscadas` y ninguna de las `contrarias`?"""
    palabras = t.split()
    tiene = lambda grupo: any(p.startswith(g) for p in palabras for g in grupo)
    return tiene(buscadas) and not tiene(contrarias)


def _colapsar(t):
    """'siii' -> 'si'."""
    return re.sub(r"(.)\1+", r"\1", t)


def _linea(salida):
    return getattr(salida, "linea", "central")


def _decir(salida, persona, texto, botones=None):
    """Responde y guarda lo que Pati dijo (el historial le da contexto a la IA). `botones`: [(etiqueta, id)] opcional."""
    db.log_mensaje(persona["id"], "pati", texto, _linea(salida))
    if botones and _botones_activos():
        salida.responder(texto, botones=botones)
    else:
        salida.responder(texto)


def _botones_activos():
    """¿El negocio usa botones de WhatsApp en los avisos al equipo? (negocio.json: "botones"; apagado si no se dice)."""
    return bool(negocio.config().get("botones", False))


def botones_tarea(tarea_id, con_mas_tiempo=True, con_problema=True):
    """Botones de una tarea: cada id lleva el número de la tarea, así "listo" nunca es ambiguo (máximo 3 por mensaje)."""
    b = [("✅ Listo", f"listo:{tarea_id}")]
    if con_problema:
        b.append(("⚠️ Problema", f"problema:{tarea_id}"))
    if con_mas_tiempo:
        b.append(("⏳ Más tiempo", f"mas:{tarea_id}"))
    return b


def _enviar_con_botones(salida, telefono, texto, botones):
    """salida.enviar con botones si están activos (sin pasar `botones` si no: otras salidas no lo conocen)."""
    if botones and _botones_activos():
        salida.enviar(telefono, texto, botones=botones)
    else:
        salida.enviar(telefono, texto)


def edad_segundos(ts, ahora_s=None):
    """Antigüedad de un mensaje en segundos; la marca de tiempo de WhatsApp viene en milisegundos."""
    ts = ts / 1000 if ts > 1e12 else ts
    return (time.time() if ahora_s is None else ahora_s) - ts


def es_linea_propia(*ids):
    """True si alguno de los números es una línea de Pati: las líneas nunca se responden entre sí."""
    propias = set(negocio.config()["lineas"].values())
    return any(i and i in propias for i in ids)


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
        reales = {w["nombre"].lower(): w["nombre"] for w in db.asignables()}
        ahora_iso = _hoy_naive().strftime("%Y-%m-%d %H:%M")
        for t in datos["tareas"]:
            t["responsable"] = reales.get((t.get("responsable") or "").lower())
            if t.get("vence") and t["vence"] <= ahora_iso:  # un plazo que ya pasó no sirve: se descarta
                t["vence"] = None
        return datos
    datos["telefono"] = _tel(datos.get("telefono"))
    if negocio.config().get("sujeto_es_cliente") and not datos.get("mascota") and datos.get("cliente"):
        datos["mascota"] = datos["cliente"]
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
        "cliente": "el nombre del cliente", "telefono": "el teléfono",
        "mascota": f"el nombre {negocio.config()['del_sujeto']}",
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
            cuando = f"⏰ {etiqueta_plazo(t['vence'])}" if t.get("vence") else t.get("horario")
            extra = [x for x in (cuando, t.get("detalles")) if x]
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
            f"{negocio.config()['Sujeto']}: {d.get('mascota') or '❓ sin dato'}",
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


def mensaje_trabajador(nombre, t, botones=False):
    """Mensaje privado que Pati le manda al trabajador con su nueva tarea (con botones no hace falta explicar cómo responder)."""
    partes = [f"🆕 *{t['actividad']}*"]
    if t.get("detalles"):
        partes.append(t["detalles"])
    if t.get("vence"):
        partes.append(f"⏰ {etiqueta_plazo(t['vence'])}")
    elif t.get("horario"):
        partes.append(f"🕒 {t['horario']}")
    if not botones:
        partes += ["", "Responde ✅ *listo* o ⚠️ *problema*."]
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

    # Comandos (empiezan con punto): reglas fijas, siempre hacen lo mismo. Ver comandos.py
    from comandos import ejecutar_comando
    if ejecutar_comando(salida, dueno, texto):
        return

    # Hay una propuesta nueva y una anterior sin resolver: hay que elegir
    if prop and alt:
        if _en(t, NO):
            estado.pop("propuesta"), estado.pop("alterna")
            db.set_estado(pid, estado)
            _decir(salida, dueno, "Listo, descarté las dos.")
        elif _elige(t, REEMPLAZAR_PALABRAS, SEGUIR_PALABRAS):
            estado["propuesta"] = estado.pop("alterna")
            db.set_estado(pid, estado)
            _decir(salida, dueno, texto_propuesta(estado["propuesta"]))
        elif _elige(t, SEGUIR_PALABRAS, REEMPLAZAR_PALABRAS):
            estado.pop("alterna")
            db.set_estado(pid, estado)
            _decir(salida, dueno, "Ok, sigo con la anterior.\n\n" + texto_propuesta(prop))
        else:
            _decir(salida, dueno, "Primero dime: ¿*reemplazo* la propuesta anterior por la nueva "
                   "o *sigo* con la anterior?")
        return

    # Si el dueño también recibe tareas, "listo" / "problema" / "qué tengo pendiente" se tratan como las de un trabajador
    if not prop and _es_asignable(dueno) and _responde_como_trabajador(dueno, estado, t, texto):
        flujo_trabajador(salida, dueno, texto)
        return

    # Respuesta a una orden de control pendiente (cancelar, reasignar...): SÍ / NO / número
    if _responder_control(salida, dueno, estado, t):
        return

    # Encuestas al equipo (crear, resultados, check-in): antes que "cómo va..." para que no se confunda con el estado de tareas
    if _encuestas_dueno(salida, dueno, texto, t, prop):
        return

    # Pide ayuda ("qué funciones tienes"): texto fijo, sin IA, sin tocar la propuesta pendiente
    if _es_pedido_ayuda(t):
        texto_a = texto_ayuda_dueno(dueno)
        if prop:
            texto_a += "\n\n(Sigo esperando tu *SÍ* o *NO* para la propuesta anterior.)"
        _decir(salida, dueno, texto_a)
        return

    # Pregunta por la agenda de citas: datos reales, sin IA
    if _es_consulta_agenda(t):
        _responder_agenda(salida, dueno, t, prop)
        return

    # Pregunta por el estado de las tareas: se responde con datos reales (sin IA)
    if _es_consulta_estado(t) and not RECORDAR_EQUIPO_RE.match(t):  # "mándales el estado de sus tareas" es recordarles, no un reporte
        _responder_estado(salida, dueno, _nombre_en_texto(t), prop)
        return

    # Cancelar / reasignar / cambiar plazo / recordar / avisar al equipo / historial / deshacer
    if not _en(t, NO) and _control_nuevo(salida, dueno, texto, t, bloqueado=bool(prop)):
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
            [w["nombre"] for w in db.asignables()],
            _nombres_clientes(),
            _historial_previo(pid, texto),
            prop,
            negocio=negocio.config(),
            dueno=dueno["nombre"],
        )
    except Exception as e:
        _fallo_ia(salida, dueno, e)
        return

    if r["accion"] == "consulta":  # la IA entendió que pregunta por el estado de las tareas
        _responder_estado(salida, dueno, r["datos"]["persona"], prop)
        return

    if r["accion"] == "ninguna":
        resp = r["respuesta"] or AYUDA_DUENO
        if prop:
            resp += "\n\n(Sigo esperando tu *SÍ* o *NO* para la propuesta anterior.)"
        _decir(salida, dueno, resp)
        return

    estado.pop("control", None)  # una orden nueva a la IA reemplaza cualquier control sin confirmar
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


# ── Control del dueño sobre tareas ya enviadas ────────────────────────────────
#
# Órdenes con reglas fijas (sin IA, sin gastar cupo): cancelar, reasignar, cambiar plazo, recordar, avisar a todo el
# equipo, historial y deshacer. Todo lo que escribe a otras personas pide SÍ/NO antes. La orden pendiente vive en
# estado["control"]; "deshacer" usa estado["ultimo_lote"] (las tareas del último envío).

CONTROL_TTL_MIN = 30
DESHACER_MIN = 30

CANCELAR_RE = re.compile(r"^(cancela|cancelar|anula|anular|elimina|eliminar|quita|quitale|borra)\b")
REASIGNAR_RE = re.compile(r"^(pasa|pasale|reasigna|reasignale|cambia|cambiale|traspasa)\b")
PLAZO_RE = re.compile(r"^(cambia|cambiale|mueve|muevele|extiende|extiendele|amplia|adelanta|adelantale|posterga|"
                      r"atrasa|modifica|ponle)\b")
RECORDAR_RE = re.compile(r"^(recuerdale|recuerdales|recordale|apurale|apura a|insistele|presionale)\b")
CHECKIN_RE = re.compile(r"^(?:(?:haz|manda|mandales|envia|enviales|lanza)\s+)?(?:una\s+|el\s+)?"
                        r"(?:encuesta de prueba|prueba de encuesta|check ?in)\b")
CHECKIN_PREGUNTA = "¿Cómo va tu día?"
CHECKIN_OPCIONES = ["✅ Todo bien", "⚠️ Tengo un problema", "⏳ Necesito más tiempo"]
ENCUESTA_RE = re.compile(r"^\s*(?:(?:haz|manda|mandales|envia|enviales|crea|lanza|arma)\s+)?(?:una\s+)?encuesta\b"
                         r"(?:\s+al\s+equipo)?\s*[:,\-]?\s*", re.I)
PREGUNTAR_EQUIPO_RE = re.compile(r"^\s*(?:preguntale|preguntales|pregunta)\s+al\s+equipo\s*[:,]?\s*", re.I)
RESULTADOS_ENCUESTA_RE = re.compile(r"\bencuesta\b.*\b(resultados?|como va|quien|quienes|votaron|votos|respondieron|falta|faltan)\b"
                                    r"|\b(resultados?|como va|quien|quienes|votos)\b.*\bencuesta\b")
RESULTADOS_SOLOS_RE = re.compile(r"^(?:(?:avisame|dame|mandame|enviame|muestrame|pasame|quiero|ver|y)\s+)*(?:los\s+|el\s+)?"
                                 r"(?:resultados?|resumen|como va|como van|como vamos)(?:\s+(?:ya|ahora|por favor))?$")
OPCIONES_RE = re.compile(r"\bopciones?\b\s*[:\-]?\s*", re.I)
SEPARADOR_OPCIONES_RE = re.compile(r"\s*[,/|;]\s*|\s+o\s+")
OPCIONES_SI_NO = ["✅ Sí", "❌ No"]
ANUNCIO_RE = re.compile(r"^\s*(?:avisa(?:le|les)?|anuncia(?:le|les)?|comunica(?:le|les)?|dile|diles)\s+"
                        r"(?:a\s+)?(?:todos|todo el equipo|al equipo|el equipo)\s*(?:que|:|,)?\s*(.+)$", re.I | re.S)
HISTORIAL_RE = re.compile(r"\b(historial|bitacora|que paso con|que paso en la tarea)\b")
DESHACER_RE = re.compile(r"^(deshacer|deshaz|deshazlo|desharlo|me equivoque|retrocede|anula lo ultimo|cancela lo ultimo)\b")
TAREA_RE = re.compile(r"\b(tarea|tareas|encargo|encargos|pendiente|pendientes|lo de|lo que tiene)\b")
PLAZO_PALABRAS_RE = re.compile(r"\b(plazo|limite|vence|hora|hasta)\b")


def _sin_tildes(texto):
    """Quita las tildes sin cambiar el largo ni las mayúsculas (para cortar el texto original por posición)."""
    return "".join(ch for ch in unicodedata.normalize("NFD", texto) if unicodedata.category(ch) != "Mn")


def _nombres_en_orden(t):
    """Personas del equipo mencionadas en el texto (sin tildes), en el orden en que aparecen."""
    palabras = t.split()
    halladas = []
    for w in db.asignables():
        n = norm(w["nombre"])
        if n in palabras:
            halladas.append((palabras.index(n), w))
    return [w for _, w in sorted(halladas, key=lambda x: x[0])]


def _tareas_de(persona_id, t):
    """Tareas abiertas de esa persona a las que se refiere el texto ("quirófano"); si no precisa, todas."""
    abiertas = db.tareas_pendientes(persona_id)
    if "todas" in t.split():
        return abiertas
    palabras = [w[:5] for w in t.split() if len(w) >= 4]
    filtradas = [x for x in abiertas if any(p in norm(x["actividad"]) for p in palabras)]
    return filtradas or abiertas


def _ahora_naive():
    return ahora().replace(tzinfo=None)


def _control_caducado(ctl):
    return (_ahora_naive() - datetime.fromisoformat(ctl["creada"])).total_seconds() > CONTROL_TTL_MIN * 60


def _listar(filas):
    return "\n".join(f"• {x['actividad']}" for x in filas)


def _guardar_control(dueno, **datos):
    estado = db.get_estado(dueno["id"])
    estado["control"] = {**datos, "creada": _ahora_naive().isoformat()}
    db.set_estado(dueno["id"], estado)


def _texto_confirmar_control(ctl, filas):
    a = ctl["accion"]
    if a == "cancelar":
        quien = filas[0]["resp_nombre"] if len({f["responsable_id"] for f in filas}) == 1 else "el equipo"
        cabeza = "Voy a cancelar" + (" (deshacer)" if ctl.get("deshacer") else "") + f" estas tareas de {quien}:"
        return f"{cabeza}\n{_listar(filas)}\n\nLes aviso a quienes las tenían. ¿Confirmas? Responde *SÍ* o *NO*."
    if a == "reasignar":
        nuevo = db.persona_por_pk(ctl["destino"])
        return (f"Voy a pasar de {filas[0]['resp_nombre']} a {nuevo['nombre']}:\n{_listar(filas)}\n\n"
                "Aviso a los dos. ¿Confirmas? Responde *SÍ* o *NO*.")
    if a == "plazo":
        return (f"Voy a cambiar el plazo de:\n{_listar(filas)}\na *{etiqueta_plazo(ctl['vence'])}* "
                f"y le aviso a {filas[0]['resp_nombre']}. ¿Confirmas? Responde *SÍ* o *NO*.")
    if a == "recordar":
        return (f"Voy a recordarle a {filas[0]['resp_nombre']} sus pendientes:\n{_listar(filas)}\n\n"
                "¿Se lo envío? Responde *SÍ* o *NO*.")
    if a == "equipo":
        return _texto_equipo(ctl, filas)
    if a == "encuesta":
        return (f"Voy a enviar esta encuesta a {', '.join(ctl['nombres'])}:\n\n📊 *{ctl['pregunta']}*\n"
                + "\n".join(f"• {o}" for o in ctl["opciones"])
                + "\n\nCuando quieras ver cómo va, escribe *.resultados* (si votan todos, te aviso solo). "
                  "A quien falte se lo recuerdo una vez. ¿La envío? Responde *SÍ* o *NO*.")
    return (f"Voy a enviar este aviso a {', '.join(ctl['nombres'])}:\n\n📢 {ctl['texto']}\n\n"
            "¿Lo envío? Responde *SÍ* o *NO*.")


def _proponer_control(salida, dueno, ctl, filas):
    """Guarda la orden y pide el SÍ; si hay varias tareas posibles, antes pregunta cuál."""
    if len(filas) > 1 and ctl["accion"] in ("cancelar", "reasignar", "plazo") and not ctl.get("elegidas"):
        lista = "\n".join(f"{i}. {x['actividad']}" for i, x in enumerate(filas, 1))
        _guardar_control(dueno, **{**ctl, "esperando": "cual", "ids": [x["id"] for x in filas]})
        _decir(salida, dueno, f"{filas[0]['resp_nombre']} tiene varias tareas:\n{lista}\n\n"
               "¿Cuál? Responde el número o *todas*.")
        return
    _guardar_control(dueno, **{**ctl, "esperando": "confirmar", "ids": [x["id"] for x in filas]})
    _decir(salida, dueno, _texto_confirmar_control(ctl, filas))


def _avisar(salida, tel, mensaje, fallidas, quien, botones=None):
    try:
        _enviar_con_botones(salida, tel, mensaje, botones)
    except Exception as e:
        print(f"[ERROR aviso control] {quien}: {e}")
        fallidas.append(quien)


def _ejecutar_control(salida, dueno, ctl):
    """Hace lo que el dueño confirmó. Si lo pedido ya cambió (tarea completada, etc.) lo dice y no inventa."""
    a, fallidas = ctl["accion"], []
    filas = db.tareas_por_ids(ctl.get("ids") or [])
    if a == "encuesta":
        _, enviadas, fallidas = enviar_encuesta_equipo(salida, ctl["pregunta"], ctl["opciones"], ctl.get("tipo", "equipo"),
                                                       creador_id=dueno["id"], solo=ctl.get("nombres"))
        resultado = (f"✅ Encuesta enviada a {len(enviadas)} persona(s). Escribe *.resultados* cuando quieras ver cómo va."
                     if enviadas else "No pude enviar la encuesta a nadie.")
    elif a == "anuncio":
        mensaje = f"📢 Aviso de {dueno['nombre']}:\n{ctl['texto']}"
        for w in db.asignables():
            if w["id"] != dueno["id"] and (not ctl.get("nombres") or w["nombre"] in ctl["nombres"]):
                _avisar(salida, w["telefono"], mensaje, fallidas, w["nombre"])
        resultado = "✅ Aviso enviado al equipo."
    elif a == "equipo":
        resultado = _ejecutar_equipo(salida, dueno, ctl, fallidas)
    elif not filas:
        resultado = "Esas tareas ya no están pendientes (se completaron o se cancelaron). No cambié nada."
    elif a == "cancelar":
        hechas = [f for f in filas if db.cancelar_tarea(f["id"])]
        for f in hechas:
            if f["responsable_id"] != dueno["id"]:
                _avisar(salida, f["resp_telefono"], f"❌ {f['resp_nombre']}, se canceló: *{f['actividad']}*. "
                        "Ya no necesitas hacerla.", fallidas, f["resp_nombre"])
        resultado = "✅ Cancelé:\n" + _listar(hechas)
    elif a == "reasignar":
        nuevo = db.persona_por_pk(ctl["destino"])
        hechas = [f for f in filas if db.reasignar_tarea(f["id"], nuevo["id"])]
        for f in hechas:
            _avisar(salida, nuevo["telefono"], mensaje_trabajador(
                nuevo["nombre"], {"actividad": f["actividad"], "detalles": f["detalles"], "horario": f["horario"],
                                  "vence": f["vence_en"]}, botones=_botones_activos()), fallidas, nuevo["nombre"],
                    botones=botones_tarea(f["id"], con_mas_tiempo=bool(f["vence_en"])))
            if f["responsable_id"] != dueno["id"]:
                _avisar(salida, f["resp_telefono"], f"ℹ️ {f['resp_nombre']}, *{f['actividad']}* ahora la hace "
                        f"{nuevo['nombre']}. Ya no es tuya.", fallidas, f["resp_nombre"])
        resultado = f"✅ Pasé a {nuevo['nombre']}:\n" + _listar(hechas)
    elif a == "plazo":
        hechas = [f for f in filas if db.cambiar_vence(f["id"], f["responsable_id"], ctl["vence"])]
        for f in hechas:
            db.registrar_evento(f["id"], "plazo", etiqueta_plazo(ctl["vence"]))
            if f["responsable_id"] != dueno["id"]:
                _avisar(salida, f["resp_telefono"], f"⏰ {f['resp_nombre']}, nuevo plazo para *{f['actividad']}*: "
                        f"{etiqueta_plazo(ctl['vence'])}.", fallidas, f["resp_nombre"])
        resultado = f"✅ Nuevo plazo ({etiqueta_plazo(ctl['vence'])}):\n" + _listar(hechas)
    else:  # recordar
        f0 = filas[0]
        lista = lista_tareas(filas)
        _avisar(salida, f0["resp_telefono"], f"🔔 {f0['resp_nombre']}, {dueno['nombre']} te recuerda tus pendientes:\n"
                f"{lista}\nResponde *listo* cuando termines o *problema* si algo falla.", fallidas, f0["resp_nombre"])
        for f in filas:
            db.registrar_evento(f["id"], "recordada", None)
        resultado = f"✅ Le recordé a {f0['resp_nombre']}."
    if fallidas:
        resultado += "\n\n⚠️ No pude avisar a: " + ", ".join(sorted(set(fallidas))) + "."
    estado = db.get_estado(dueno["id"])
    estado.pop("control", None)
    if ctl.get("deshacer") or a == "cancelar":
        estado.pop("ultimo_lote", None)
    db.set_estado(dueno["id"], estado)
    _decir(salida, dueno, resultado)


ORDENES_LEGIBLES = {"encuesta": "la encuesta", "anuncio": "el aviso", "cancelar": "la cancelación", "reasignar": "el cambio de "
                    "responsable", "plazo": "el cambio de plazo", "recordar": "el recordatorio", "equipo": "esa orden para el equipo"}
EXCLUIR_CLAVES = ("sin ", " menos ", "excepto", "excluye", "excluir", "quita", "quitale", "saca", "no le env", "no se la env",
                  "no se lo env", "no le mand", "no se la mand", "no se lo mand", "no incluy", "no lo env", "no la env",
                  "no se envie", "no envies", "no mandes")
SOLO_RE = re.compile(r"\b(solo|solamente|unicamente|nomas)\b")


def _ajustar_destinatarios(ctl, t):
    """"sin Marco" / "a marco no le envíes" / "solo a mafer y ashly" sobre una encuesta o aviso pendiente.

    Devuelve la nueva lista de destinatarios, o None si el mensaje no es de eso.
    """
    mencionados = [w["nombre"] for w in _nombres_en_orden(t)]
    if not mencionados:
        return None
    if SOLO_RE.search(t):
        return [n for n in ctl["nombres"] if n in mencionados]
    if any(c in f" {t} " for c in EXCLUIR_CLAVES):
        return [n for n in ctl["nombres"] if n not in mencionados]
    return None


def _responder_control(salida, dueno, estado, t):
    """Respuesta del dueño a una orden de control pendiente. True si el mensaje era para ella."""
    ctl = estado.get("control")
    if not ctl:
        return False
    if _control_caducado(ctl):
        estado.pop("control")
        db.set_estado(dueno["id"], estado)
        if ctl.get("esperando") == "confirmar" and _es_si(t):
            _decir(salida, dueno, "Esa orden caducó. Escríbemela de nuevo.")
            return True
        return False
    if _en(t, NO):
        estado.pop("control")
        db.set_estado(dueno["id"], estado)
        _decir(salida, dueno, "Ok, lo dejé como estaba.")
        return True
    if ctl["esperando"] == "confirmar":
        if _es_si(t):
            _ejecutar_control(salida, dueno, ctl)
            return True
        if ctl["accion"] in ("encuesta", "anuncio"):  # "sin Marco", "a marco no le envíes", "solo a mafer y ashly"
            nuevos = _ajustar_destinatarios(ctl, t)
            if nuevos is not None:
                if not nuevos:
                    _decir(salida, dueno, "Así no quedaría nadie. Dime a quién se la envío, o responde *NO* para descartarla.")
                    return True
                ctl["nombres"] = nuevos
                estado["control"] = ctl
                db.set_estado(dueno["id"], estado)
                _decir(salida, dueno, _texto_confirmar_control(ctl, []))
                return True
        # Hay una orden esperando su SÍ: lo que no es SÍ/NO no se lo pasa a la IA (la confundiría con una tarea nueva)
        _decir(salida, dueno, f"Tengo {ORDENES_LEGIBLES.get(ctl['accion'], 'una orden')} esperando tu *SÍ* o *NO*."
               + (" O dime a quién quitar (ej. *sin Marco*)." if ctl["accion"] in ("encuesta", "anuncio") else ""))
        return True
    filas = db.tareas_por_ids(ctl["ids"])  # esperando "cual"
    if t == "todas" or t == "todo":
        elegidas = filas
    elif t.isdigit() and 1 <= int(t) <= len(filas):
        elegidas = [filas[int(t) - 1]]
    else:
        return False
    if not elegidas:
        estado.pop("control")
        db.set_estado(dueno["id"], estado)
        _decir(salida, dueno, "Esas tareas ya no están pendientes. No cambié nada.")
        return True
    _proponer_control(salida, dueno, {**ctl, "elegidas": True}, elegidas)
    return True


def _pedir_nombre(salida, dueno, frase):
    _decir(salida, dueno, f"¿De quién del equipo? Por ejemplo: *{frase}*.")


def _control_nuevo(salida, dueno, texto, t, bloqueado=False):
    """¿Es una orden de control? Si lo es, la atiende y devuelve True. Si no, False y sigue el flujo normal."""
    nombres = _nombres_en_orden(t)
    ahora_n = _ahora_naive()

    def proponer(ctl, filas):
        if bloqueado:  # hay una propuesta sin confirmar: no se mezclan
            _decir(salida, dueno, "Primero responde *SÍ* o *NO* a la propuesta que tengo pendiente, y luego vemos eso.")
        else:
            _proponer_control(salida, dueno, ctl, filas)

    m = ANUNCIO_RE.match(_sin_tildes(texto.strip()))
    if m:
        cuerpo = texto.strip()[m.start(1):].strip()
        cuerpo = cuerpo[:1].upper() + cuerpo[1:]
        destino = [w["nombre"] for w in db.asignables() if w["id"] != dueno["id"]]
        if not destino:
            _decir(salida, dueno, "No hay nadie más en el equipo a quien avisar.")
        else:
            proponer({"accion": "anuncio", "texto": cuerpo, "nombres": destino}, [])
        return True

    if DESHACER_RE.match(t):
        lote = db.get_estado(dueno["id"]).get("ultimo_lote")
        vigente = lote and (ahora_n - datetime.fromisoformat(lote["hora"])).total_seconds() <= DESHACER_MIN * 60
        filas = db.tareas_por_ids(lote["ids"]) if vigente else []
        if not filas:
            _decir(salida, dueno, f"No tengo nada reciente que deshacer. Puedo deshacer las tareas que envié en los "
                   f"últimos {DESHACER_MIN} minutos si siguen sin completarse.")
        else:
            proponer({"accion": "cancelar", "deshacer": True, "elegidas": True}, filas)
        return True

    if HISTORIAL_RE.search(t):
        if len(nombres) != 1:
            _pedir_nombre(salida, dueno, "historial de Mafer")
        else:
            _decir(salida, dueno, texto_historial(nombres[0]))
        return True

    if _control_equipo(t, nombres, lambda m: _decir(salida, dueno, m), proponer):
        return True

    if CANCELAR_RE.match(t) and not nombres and TODAS_TAREAS_RE.search(t):
        todas = [f for f in db.tareas_abiertas_de_todos() if f["resp_rol"] != "maestro"]
        if not todas:
            _decir(salida, dueno, "No hay tareas pendientes que cancelar.")
        else:
            proponer({"accion": "cancelar", "elegidas": True}, db.tareas_por_ids([f["id"] for f in todas]))
        return True

    if CANCELAR_RE.match(t) and len(nombres) == 1 and "cita" not in t.split():
        filas = _tareas_de(nombres[0]["id"], t)
        if not filas:
            _decir(salida, dueno, f"{nombres[0]['nombre']} no tiene tareas pendientes.")
        else:
            proponer({"accion": "cancelar"}, db.tareas_por_ids([f["id"] for f in filas]))
        return True

    if REASIGNAR_RE.match(t) and len(nombres) == 2 and TAREA_RE.search(t):
        origen, destino = nombres
        filas = _tareas_de(origen["id"], t)
        if not filas:
            _decir(salida, dueno, f"{origen['nombre']} no tiene tareas pendientes.")
        else:
            proponer({"accion": "reasignar", "destino": destino["id"]},
                              db.tareas_por_ids([f["id"] for f in filas]))
        return True

    if PLAZO_RE.match(t) and PLAZO_PALABRAS_RE.search(t) and len(nombres) == 1:
        vence = _parse_plazo(texto, ahora_n)
        if vence:
            filas = _tareas_de(nombres[0]["id"], t)
            if not filas:
                _decir(salida, dueno, f"{nombres[0]['nombre']} no tiene tareas pendientes.")
            else:
                proponer({"accion": "plazo", "vence": vence.strftime("%Y-%m-%d %H:%M")},
                                  db.tareas_por_ids([f["id"] for f in filas]))
            return True

    # "recuérdale a Mafer": solo sus pendientes. Con día u hora ("mañana a las 10") es un recordatorio nuevo -> la IA.
    if (RECORDAR_RE.match(t) or t.startswith("recuerda a ")) and len(nombres) == 1 \
            and not re.search(r"\d|manana|hoy|luego|despues", t):
        filas = _tareas_de(nombres[0]["id"], t)
        if not filas:
            _decir(salida, dueno, f"{nombres[0]['nombre']} no tiene tareas pendientes que recordarle.")
        else:
            proponer({"accion": "recordar"}, db.tareas_por_ids([f["id"] for f in filas]))
        return True
    return False


RECORDAR_EQUIPO_RE = re.compile(r"^(recuerdales|recordales|recuerda a todos|recuerdale a todos|recuerdale al equipo|"
                                r"mandales|enviales|avisales|pasales)\b")
PENDIENTES_PALABRAS_RE = re.compile(r"\b(tareas?|pendientes?|estado)\b")
RESUELTA_RE = re.compile(r"\b(incidencias?|problemas?)\b.*\b(resuelt[oa]s?|solucionad[oa]s?|arreglad[oa]s?)\b"
                         r"|\b(resolvi|solucione|arregle|limpia|limpiar|borra|borrar|quita|quitar|elimina|eliminar)\b"
                         r".*\b(incidencias?|problemas?)\b")
RECORDAR_EN_TEXTO_RE = re.compile(r"\b(recuerdales|recordales|avisales|mandales|enviales|pasales)\b")
TODAS_TAREAS_RE = re.compile(r"\btodas?\b.*\b(tareas|pendientes)\b|\btareas de todos\b")


def _control_equipo(t, nombres, decir, proponer):
    """"recuérdales sus tareas pendientes", "las incidencias ya están resueltas", o ambas. True si era de eso.

    Sin nombres es para todo el equipo; con nombres, solo para esas personas. Nunca incluye al dueño.
    """
    recordar = bool((RECORDAR_EQUIPO_RE.match(t) or RECORDAR_EN_TEXTO_RE.search(t)) and PENDIENTES_PALABRAS_RE.search(t))
    resuelta = bool(RESUELTA_RE.search(t))
    if not recordar and not resuelta:
        return False
    ids_personas = {n["id"] for n in nombres}
    abiertas = [f for f in db.tareas_abiertas_de_todos()
                if f["resp_rol"] != "maestro" and (not ids_personas or f["responsable_id"] in ids_personas)]
    incidencias = [f for f in abiertas if f["estado"] == "con_incidencia"] if resuelta else []
    if resuelta and not recordar and not incidencias:
        decir("No hay incidencias abiertas" + (" de esas personas." if ids_personas else ". Todo en orden 👍"))
        return True
    if recordar and not abiertas:
        decir("Nadie tiene tareas pendientes ahora mismo 🎉")
        return True
    proponer({"accion": "equipo", "recordar": recordar, "resolver": resuelta,
              "ids": [f["id"] for f in abiertas if recordar or f["id"] in {i["id"] for i in incidencias}],
              "ids_inc": [f["id"] for f in incidencias]}, abiertas)
    return True


def _por_persona(filas):
    """{nombre: [filas]} conservando el orden."""
    d = {}
    for f in filas:
        d.setdefault(f["resp_nombre"], []).append(f)
    return d


def _texto_equipo(ctl, filas):
    partes = ["Voy a:"]
    inc = [f for f in filas if f["id"] in set(ctl["ids_inc"])]
    if inc:
        partes.append(f"✅ Marcar como *resueltas* {len(inc)} incidencia(s):\n" + "\n".join(f"• {f['resp_nombre']}: {f['actividad']}" for f in inc))
    if ctl["recordar"]:
        resumen = " · ".join(f"{n} ({len(v)})" for n, v in _por_persona(filas).items())
        partes.append(f"🔔 Recordarle a cada uno sus pendientes: {resumen}")
    elif inc:
        partes.append("y avisarles a quienes las tenían.")
    partes.append("¿Confirmas? Responde *SÍ* o *NO*.")
    return "\n".join(partes)


def _ejecutar_equipo(salida, dueno, ctl, fallidas):
    filas = db.tareas_por_ids(ctl["ids"])
    resueltas = set()
    for i in ctl["ids_inc"]:
        if db.resolver_incidencia(i):
            resueltas.add(i)
    nombres_msg = 0
    if ctl["recordar"]:
        vigentes = {f["id"]: f for f in db.tareas_abiertas_de_todos()}
        por_persona = _por_persona([vigentes[f["id"]] for f in filas if f["id"] in vigentes])
        for nombre, lista in por_persona.items():
            lineas = [f"✅ Ya se resolvió tu problema con *{f['actividad']}*." for f in lista if f["id"] in resueltas]
            lineas.append(f"🔔 {nombre}, estos son tus pendientes:")
            lineas.append(lista_tareas(lista))
            lineas.append("Responde *listo* cuando termines una." if len(lista) > 1 else "")
            botones = botones_tarea(lista[0]["id"], con_mas_tiempo=bool(lista[0]["vence_en"])) if len(lista) == 1 else None
            _avisar(salida, lista[0]["resp_telefono"], "\n".join(x for x in lineas if x), fallidas, nombre, botones=botones)
            nombres_msg += 1
        return f"✅ Resolví {len(resueltas)} incidencia(s) y les recordé sus pendientes a {nombres_msg} persona(s)." if resueltas \
            else f"✅ Les recordé sus pendientes a {nombres_msg} persona(s)."
    for f in filas:
        if f["id"] in resueltas:
            _avisar(salida, f["resp_telefono"], f"✅ Ya se resolvió tu problema con *{f['actividad']}*. Cuando termines responde *listo*.",
                    fallidas, f["resp_nombre"], botones=botones_tarea(f["id"], con_mas_tiempo=bool(f["vence_en"])))
    return f"✅ Marqué {len(resueltas)} incidencia(s) como resueltas y avisé a quienes las tenían."


def _fecha_evento(fecha):
    d = datetime.strptime(fecha, "%Y-%m-%d %H:%M:%S")
    return f"{d:%H:%M}" if d.date() == _hoy_naive().date() else f"{d:%d/%m %H:%M}"


EVENTO_TEXTO = {"creada": "creada", "completada": "✅ completada", "incidencia": "⚠️ problema", "cancelada": "❌ cancelada",
                "reasignada": "↔️ reasignada", "plazo": "⏰ plazo", "recordada": "🔔 recordada", "envio_fallido": "no se pudo enviar"}


def texto_historial(persona, limite=3):
    """Línea de tiempo de las últimas tareas de una persona, con datos reales."""
    hist = db.historial_tareas(persona["id"], limite)
    if not hist:
        return f"{persona['nombre']} todavía no tiene tareas registradas."
    bloques = [f"📋 Historial de {persona['nombre']} (últimas {len(hist)}):"]
    for h in hist:
        t = h["tarea"]
        plazo = f" · {texto_plazo(t['vence_en'])}" if t["vence_en"] else ""
        bloques.append(f"\n*{t['actividad']}*{plazo}")
        for e in h["eventos"]:
            extra = f": {e['detalle']}" if e["detalle"] and e["tipo"] != "creada" else ""
            bloques.append(f"• {_fecha_evento(e['fecha'])} {EVENTO_TEXTO.get(e['tipo'], e['tipo'])}{extra}")
    return "\n".join(bloques)


# ── Encuestas al equipo ───────────────────────────────────────────────────────
#
# El dueño escribe "encuesta ¿Quién cubre el sábado? opciones: yo, no puedo" (o "pregúntale al equipo si ...", que
# usa Sí / No). Pati la muestra, y con el SÍ del dueño la envía como encuesta de WhatsApp a cada trabajador. Los votos
# se guardan (sobreviven a reinicios); al votar todos, o pasado el tiempo configurado, el dueño recibe los resultados,
# y a quien no votó se le recuerda UNA vez. El "check-in" (¿cómo va tu día?) es una encuesta con seguimiento: quien
# marca problema o más tiempo se lo cuenta a Pati y el dueño se entera.

EJEMPLO_ENCUESTA = "Por ejemplo: *encuesta ¿Quién cubre el sábado? opciones: yo, no puedo* o *pregúntale al equipo si pueden quedarse hasta las 8*."


def _cfg_encuestas():
    return negocio.config().get("encuestas") or {"recordar_min": 30}


def _duracion(minutos):
    return f"{minutos // 60} h" if minutos >= 60 and minutos % 60 == 0 else f"{minutos} min"


def _parsear_encuesta(texto):
    """"encuesta ¿Quién cubre el sábado? opciones: yo, no puedo" -> (pregunta, [opciones]). Sin opciones: Sí / No.

    Si no se entiende, devuelve (None, mensaje para el dueño).
    """
    s = _sin_tildes(texto)
    m = ENCUESTA_RE.match(s)
    preguntando = False
    if m is None:
        m = PREGUNTAR_EQUIPO_RE.match(s)
        preguntando = m is not None
    if m is None:
        return None, "No entendí la encuesta. " + EJEMPLO_ENCUESTA
    cuerpo = texto[m.end():].strip()
    pos = OPCIONES_RE.search(_sin_tildes(cuerpo))
    if pos:
        pregunta = cuerpo[:pos.start()].strip(" ,;:-")
        opciones, vistas = [], set()
        for o in SEPARADOR_OPCIONES_RE.split(cuerpo[pos.end():]):
            o = " ".join(o.split()).strip(" .")  # sin espacios dobles
            if o and norm(o) not in vistas:
                vistas.add(norm(o))
                opciones.append(o[:1].upper() + o[1:])
    else:
        pregunta, opciones = cuerpo.strip(" ,;:-"), list(OPCIONES_SI_NO)
    if preguntando and norm(pregunta).startswith("si "):  # "si pueden venir el sábado" -> "¿Pueden venir el sábado?"
        pregunta = "¿" + pregunta[3:].strip()[:1].upper() + pregunta[3:].strip()[1:].rstrip("?") + "?"
    pregunta = " ".join(pregunta.split())
    if len(pregunta) < 3:
        return None, "¿Qué quieres preguntarle al equipo? " + EJEMPLO_ENCUESTA
    if len(pregunta) > 200:
        return None, "La pregunta es muy larga (máximo 200 letras). Acórtala, por favor."
    if not 2 <= len(opciones) <= 8 or any(len(o) > 60 for o in opciones):
        return None, ("Necesito entre 2 y 8 opciones, cortas (hasta 60 letras), separadas por comas. "
                      "Por ejemplo: *encuesta ¿Quién cubre el sábado? opciones: yo, no puedo*.")
    return pregunta, opciones


def _nombres_encuestables():
    """A quiénes se les envía una encuesta: al equipo, no al dueño."""
    return [w for w in db.asignables() if w["rol"] != "maestro"]


def enviar_encuesta_equipo(salida, pregunta, opciones, tipo="equipo", creador_id=None, solo=None):
    """Registra la encuesta y la envía a cada trabajador. Devuelve (id de la encuesta, enviadas, fallidas)."""
    enc_id = db.crear_encuesta(pregunta, opciones, tipo, creador_id, _hoy_naive().strftime("%Y-%m-%d %H:%M:%S"))
    enviadas, fallidas = [], []
    for w in _nombres_encuestables():
        if solo is not None and w["nombre"] not in solo:
            continue  # el dueño la quitó para esa persona
        try:
            msg_id = salida.enviar_encuesta(w["telefono"], pregunta, opciones) or f"sin-id-{enc_id}-{w['id']}"
            db.registrar_envio_encuesta(msg_id, enc_id, w["id"])
            enviadas.append(w["nombre"])
        except Exception as e:
            print(f"[ERROR encuesta] {w['nombre']}: {e}")
            fallidas.append(w["nombre"])
    return enc_id, enviadas, fallidas


def texto_resultados(enc, destinatarios, votos):
    """Quién votó qué y quién falta, con datos reales."""
    por_opcion = {o: [] for o in enc["opciones"]}
    for v in votos:
        por_opcion.setdefault(v["opcion"], []).append(v["nombre"])
    lineas = [f"📊 *{enc['pregunta']}*"]
    for o in enc["opciones"]:
        n = por_opcion[o]
        lineas.append(f"{o}: *{len(n)}*" + (f" ({', '.join(n)})" if n else ""))
        for v in votos:  # lo que contaron quienes marcaron un problema
            if v["opcion"] == o and v["detalle"]:
                lineas.append(f"   ↳ {v['nombre']}: {v['detalle']}")
    votaron = {v["persona_id"] for v in votos}
    faltan = [d["nombre"] for d in destinatarios if d["id"] not in votaron]
    lineas.append(f"⏳ Sin responder ({len(faltan)}): {', '.join(faltan)}" if faltan else "✅ Respondieron todos.")
    return "\n".join(lineas)


def texto_resultados_ultima():
    enc = db.ultima_encuesta()
    if enc is None:
        return "Todavía no he enviado ninguna encuesta. " + EJEMPLO_ENCUESTA
    return texto_resultados(enc, db.destinatarios_encuesta(enc["id"]), db.votos_de(enc["id"]))


def _enviar_resultados(salida, enc, destinatarios, votos, parcial):
    texto = texto_resultados(enc, destinatarios, votos)
    _notificar_duenos(salida, ("⏱️ Resultados parciales\n" if parcial else "") + texto)
    db.marcar_encuesta(enc["id"], "resumen_enviado")


def _revisar_cierre(salida, encuesta_id):
    """Si ya votaron todos, el dueño recibe los resultados (una sola vez)."""
    enc = db.encuesta(encuesta_id)
    if enc is None or enc["resumen_enviado"]:
        return
    dest, votos = db.destinatarios_encuesta(encuesta_id), db.votos_de(encuesta_id)
    if dest and len({v["persona_id"] for v in votos}) >= len(dest):
        _enviar_resultados(salida, enc, dest, votos, parcial=False)


PROBLEMA_RE = re.compile(r"\b(problemas?|incidencias?|inconvenientes?|reportar)\b")
SIN_PROBLEMA_RE = re.compile(r"\b(sin|no|ningun[oa]?)\b.{0,14}\b(problemas?|incidencias?|inconvenientes?)\b")
DETALLE_VIGENTE_MIN = 20  # tiempo que Pati espera lo que la persona cuente de su problema


def _es_problema(opcion):
    """¿Esta opción de una encuesta es "tengo un problema" / "reportar problema"? ("sin problemas" no)."""
    t = norm(opcion)
    return bool(PROBLEMA_RE.search(t)) and not SIN_PROBLEMA_RE.search(t)


def _pedir_detalle_problema(salida, persona, enc, opcion, saludo):
    """Quien marcó un problema en una encuesta cuenta qué pasa; Pati se lo pasa al dueño (y queda en los resultados)."""
    db.set_estado(persona["id"], {"esperando": "detalle_checkin", "encuesta_id": enc["id"], "opcion": opcion,
                                  "desde": _hoy_naive().isoformat()})
    _decir(salida, persona, f"{saludo} Cuéntame qué pasa y se lo aviso al dueño. (Escribe *no* si prefieres no dar detalles.)")
    _notificar_duenos(salida, f"⚠️ *{persona['nombre']}* marcó *{opcion}* en la encuesta «{enc['pregunta']}». Le pedí detalles.")


def _seguimiento_checkin(salida, persona, opcion, enc):
    """Check-in "¿Cómo va tu día?": al que tiene un problema se le pregunta qué pasa y se avisa al dueño."""
    nombre = persona["nombre"]
    i = CHECKIN_OPCIONES.index(opcion) if opcion in CHECKIN_OPCIONES else -1
    if i == 0:
        _decir(salida, persona, f"¡Qué bueno, {nombre}! 🙌")
    elif i == 1:
        _pedir_detalle_problema(salida, persona, enc, opcion, "Lamento eso 🙁")
    elif i == 2:
        _decir(salida, persona, "Anotado, se lo aviso al dueño.")
        _notificar_duenos(salida, f"⏳ *{nombre}* dice que necesita más tiempo (check-in del día).")


def procesar_voto_encuesta(salida, persona, encuesta_id, opcion):
    """Un voto (o un cambio de voto) de alguien del equipo. Solo en la línea central y solo opciones de esa encuesta."""
    enc = db.encuesta(encuesta_id)
    if enc is None or opcion not in enc["opciones"] or _linea(salida) != "central":
        return
    previo = db.guardar_voto(encuesta_id, persona["id"], opcion)
    if previo == opcion:
        return  # el mismo voto otra vez
    if enc["tipo"] == "checkin":
        _seguimiento_checkin(salida, persona, opcion, enc)
    elif _es_problema(opcion):  # una encuesta libre con "Tengo un problema" / "Reportar problema"
        _pedir_detalle_problema(salida, persona, enc, opcion, f"👍 Anotado: {opcion}.")
    else:
        _decir(salida, persona, f"👍 Anotado: {opcion}" if previo is None else f"👍 Cambié tu voto a: {opcion}")
    _revisar_cierre(salida, encuesta_id)


def revisar_encuestas(salida):
    """Se llama cada minuto: recuerda UNA vez a quien no votó y manda al dueño el resumen cuando pasa el tiempo.

    Devuelve [(tipo, id de la encuesta)]. Solo en la ventana de avisos (como el seguimiento de tareas).
    """
    cfg = negocio.config()
    ahora_n = _hoy_naive()
    if not _en_horario_de_avisos(ahora_n, cfg["seguimiento"]):
        return []
    hechos = []
    for e in db.encuestas_sin_cerrar():
        edad = (ahora_n - datetime.strptime(e["creada_en"], "%Y-%m-%d %H:%M:%S")).total_seconds() / 60
        enc = db.encuesta(e["id"])
        dest, votos = db.destinatarios_encuesta(e["id"]), db.votos_de(e["id"])
        if edad > 24 * 60:  # una encuesta de ayer ya no se recuerda ni se resume
            db.marcar_encuesta(e["id"], "recordatorio_enviado")
            db.marcar_encuesta(e["id"], "resumen_enviado")
            continue
        try:
            recordar_min = _cfg_encuestas()["recordar_min"]
            if not e["recordatorio_enviado"] and recordar_min > 0 and edad >= recordar_min:
                votaron = {v["persona_id"] for v in votos}
                for d in dest:
                    if d["id"] not in votaron:
                        salida.enviar(d["telefono"], f"👆 Te falta responder: *{enc['pregunta']}*")
                db.marcar_encuesta(e["id"], "recordatorio_enviado")
                hechos.append(("recordatorio", e["id"]))
        except Exception as ex:  # se reintenta el siguiente minuto
            print(f"[ERROR encuestas] encuesta {e['id']}: {type(ex).__name__}: {ex}")
    return hechos


def enviar_checkin(salida):
    """El check-in diario al equipo (negocio.json: "checkin": {"hora": "17:30"}); una sola vez por día."""
    hoy = _hoy_naive().strftime("%Y-%m-%d")
    ultima = db.ultima_encuesta("checkin")
    if ultima is not None and ultima["creada_en"].startswith(hoy):
        return None
    if not _nombres_encuestables():
        return None
    return enviar_encuesta_equipo(salida, CHECKIN_PREGUNTA, CHECKIN_OPCIONES, "checkin")


CONTINUAR = "➡️ Continuar"
CANCELAR_SEL = "✖️ Cancelar"
SELECTORES_TRABAJADOR = {  # tipo -> (pregunta, ¿se pueden marcar varias?)
    "selector_completar": ("¿Cuáles terminaste? Marca las que ya hiciste y toca ➡️ Continuar", True),
    "selector_problema": ("¿En cuál tarea hay un problema? Toca una", False),
    "selector_mas": ("¿Para cuál tarea necesitas más tiempo? Toca una", False),
}


def _enviar_selector(salida, destino, pregunta, grupos, tipo, multiple):
    """Crea y envía una encuesta de ELEGIR (no de opinar) a `destino`. `grupos`: [{"label", "ids"}] con labels distintos.

    Siempre lleva "✖️ Cancelar" (volver atrás) y, si se pueden marcar varias, "➡️ Continuar". Devuelve el id.
    """
    etiquetas = [g["label"] for g in grupos]
    opciones = etiquetas + ([CONTINUAR] if multiple else []) + [CANCELAR_SEL]
    enc_id = db.crear_encuesta(pregunta, opciones, tipo, destino["id"], _hoy_naive().strftime("%Y-%m-%d %H:%M:%S"),
                               datos={"grupos": grupos})
    msg_id = salida.enviar_encuesta(destino["telefono"], pregunta, opciones, multiple=multiple) or f"sin-id-{enc_id}"
    db.registrar_envio_encuesta(msg_id, enc_id, destino["id"])
    return enc_id


def _etiquetas_distintas(grupos):
    vistas = []
    for g in grupos:  # si dos opciones se llamaran igual no se sabría cuál se marcó
        base, k = g["label"], 2
        while g["label"] in vistas:
            g["label"] = f"{base} ({k})"
            k += 1
        vistas.append(g["label"])
    return grupos


def enviar_selector_cancelar(salida, dueno, personas=None):
    """Al dueño le llega una encuesta de varias opciones con sus tareas abiertas (agrupadas por nombre): marca las que
    quiere cancelar y toca "Continuar". Es solo para ELEGIR: cancelar sigue pidiendo su SÍ. `personas`: solo las de ellas."""
    grupos = {}
    for f in db.tareas_abiertas_de_todos():
        if f["resp_rol"] != "maestro" and (not personas or f["resp_nombre"] in personas):
            g = grupos.setdefault(norm(f["actividad"]), {"actividad": f["actividad"], "ids": [], "quienes": []})
            g["ids"].append(f["id"])
            if f["resp_nombre"] not in g["quienes"]:
                g["quienes"].append(f["resp_nombre"])
    if not grupos:
        _decir(salida, dueno, "No hay tareas pendientes que cancelar" + (f" de {', '.join(personas)}." if personas else "."))
        return
    lista = list(grupos.values())[:10]  # WhatsApp admite 12 opciones; dos son "Continuar" y "Cancelar"
    for g in lista:
        if personas and len(personas) == 1:
            g["label"] = g["actividad"][:90]
        else:
            quienes = ", ".join(g["quienes"]) if len(g["quienes"]) <= 2 else f"{len(g['quienes'])} personas"
            g["label"] = f"{g['actividad'][:60]} · {quienes}"[:90]
    lista = _etiquetas_distintas(lista)
    de = f" de {', '.join(personas)}" if personas else ""
    _enviar_selector(salida, dueno, f"¿Qué tareas{de} cancelo? Marca las que quieras y toca ➡️ Continuar",
                     [{"label": g["label"], "ids": g["ids"]} for g in lista], "selector_cancelar", True)
    extra = f" (Hay {len(grupos) - 10} más: usa *.cancelar* con las palabras de la tarea.)" if len(grupos) > 10 else ""
    _decir(salida, dueno, "Te mandé la lista. Marca las tareas a cancelar y toca *➡️ Continuar* (o *✖️ Cancelar* para salir); "
           "después te pido tu SÍ." + extra)


def enviar_selector_trabajador(salida, persona, tipo, tareas):
    """Cuando alguien tiene varias tareas y dice "listo", "problema" o "más tiempo": en vez de pedirle números, recibe una
    encuesta con sus tareas. Devuelve False si no se pudo (entonces se usa la lista con números de siempre)."""
    if (not _botones_activos() or _linea(salida) != "central" or not hasattr(salida, "enviar_encuesta")
            or len(tareas) > (10 if SELECTORES_TRABAJADOR[tipo][1] else 11)):
        return False
    grupos = []
    for i, t in enumerate(tareas, 1):
        plazo = f" · {etiqueta_plazo(t['vence_en'])}" if t["vence_en"] else ""
        aviso = " ⚠️" if t["estado"] != "pendiente" else ""
        grupos.append({"label": f"{i}. {t['actividad'][:55]}{aviso}{plazo}"[:90], "ids": [t["id"]]})
    pregunta, multiple = SELECTORES_TRABAJADOR[tipo]
    try:
        _enviar_selector(salida, persona, pregunta, grupos, tipo, multiple)
    except Exception as e:
        print(f"[ERROR selector] {persona['nombre']}: {type(e).__name__}: {e}")
        return False
    return True


def procesar_seleccion(salida, persona, encuesta_id, elegidas):
    """Voto en un selector (cancelar del dueño, o completar / problema / más tiempo de un trabajador).

    En los de varias opciones se ignoran las marcas hasta que toque "Continuar" (WhatsApp avisa en cada marca); en los de
    una sola, tocar la opción ya vale. "✖️ Cancelar" siempre vuelve atrás sin cambiar nada. Cada selector se usa una vez.
    """
    enc = db.encuesta(encuesta_id)
    if enc is None or not enc["tipo"].startswith("selector") or enc["resumen_enviado"] or _linea(salida) != "central":
        return
    tipo = enc["tipo"]
    multiple = tipo in ("selector_cancelar", "selector_completar")
    pid = persona["id"]
    if CANCELAR_SEL in elegidas:
        db.marcar_encuesta(encuesta_id, "resumen_enviado")
        _limpiar_pasos(pid)
        _decir(salida, persona, "Ok, no cambié nada.")
        return
    if multiple and CONTINUAR not in elegidas:
        return
    if tipo == "selector_cancelar" and db.get_estado(pid).get("propuesta"):
        _decir(salida, persona, "Primero responde *SÍ* o *NO* a la propuesta que tengo pendiente y vuelve a tocar *Continuar*.")
        return
    marcadas = [g for g in enc["datos"]["grupos"] if g["label"] in elegidas]
    if not marcadas:
        if multiple:  # tocó Continuar sin marcar nada
            db.marcar_encuesta(encuesta_id, "resumen_enviado")
            _decir(salida, persona, "No marcaste ninguna tarea. Escribe de nuevo lo que necesitas y te mando la lista.")
        return
    db.marcar_encuesta(encuesta_id, "resumen_enviado")  # un selector se usa una sola vez
    ids = [i for g in marcadas for i in g["ids"]]
    if tipo == "selector_cancelar":
        filas = db.tareas_por_ids(ids)  # solo las que siguen abiertas
        if not filas:
            _decir(salida, persona, "No marcaste ninguna tarea (o ya no están pendientes). Usa *.cancelar* para volver a elegir.")
            return
        _proponer_control(salida, persona, {"accion": "cancelar", "elegidas": True}, filas)
        return
    _limpiar_pasos(pid)
    if tipo == "selector_completar":
        propias = [t for t in db.tareas_pendientes(pid) if t["id"] in ids]
        _marcar_listas(salida, persona, [{"id": t["id"], "actividad": t["actividad"]} for t in propias]
                       or [{"id": i, "actividad": ""} for i in ids])
    elif tipo == "selector_problema":
        procesar_boton(salida, persona, f"problema:{ids[0]}")
    elif tipo == "selector_mas":
        procesar_boton(salida, persona, f"mas:{ids[0]}")


def _limpiar_pasos(pid):
    """Quita los pasos pendientes de "¿cuál?" (si se eligió con la encuesta, ya no se espera un número escrito)."""
    estado = db.get_estado(pid)
    db.set_estado(pid, {k: v for k, v in estado.items() if k not in ("esperando", "tareas", "tarea_id", "actividad", "intentos")})


def _hay_encuesta_reciente():
    """¿Se envió una encuesta en las últimas 24 h? (para entender "avísame los resultados" sin decir "encuesta")."""
    e = db.ultima_encuesta()
    return e is not None and (_hoy_naive() - datetime.strptime(e["creada_en"], "%Y-%m-%d %H:%M:%S")).total_seconds() < 24 * 3600


def _encuestas_dueno(salida, dueno, texto, t, prop):
    """Órdenes del dueño sobre encuestas: crear una, ver resultados, check-in. True si el mensaje era de encuestas."""
    s = _sin_tildes(texto)
    crear = ENCUESTA_RE.match(s) or PREGUNTAR_EQUIPO_RE.match(s)
    if CHECKIN_RE.match(t) and len(t) <= 60:
        ctl = {"accion": "encuesta", "pregunta": CHECKIN_PREGUNTA, "opciones": list(CHECKIN_OPCIONES), "tipo": "checkin"}
    elif RESULTADOS_SOLOS_RE.match(t) and _hay_encuesta_reciente():
        _decir(salida, dueno, texto_resultados_ultima())
        return True
    elif re.search(r"\bencuestas?\b", t) and RESULTADOS_ENCUESTA_RE.search(t) \
            and not (crear and ("?" in texto or OPCIONES_RE.search(s))):
        _decir(salida, dueno, texto_resultados_ultima())
        return True
    elif crear:
        pregunta, opciones = _parsear_encuesta(texto)
        if pregunta is None:
            _decir(salida, dueno, opciones)
            return True
        ctl = {"accion": "encuesta", "pregunta": pregunta, "opciones": opciones, "tipo": "equipo"}
    else:
        return False
    destino = [w["nombre"] for w in _nombres_encuestables()]
    if not destino:
        _decir(salida, dueno, "No hay nadie más en el equipo a quien enviarle la encuesta.")
    elif prop:
        _decir(salida, dueno, "Primero responde *SÍ* o *NO* a la propuesta que tengo pendiente, y luego vemos eso.")
    else:
        _proponer_control(salida, dueno, {**ctl, "nombres": destino}, [])
    return True


# ── Plazos y seguimiento automático ───────────────────────────────────────────
#
# Cada tarea con plazo (vence_en) se vigila una vez por minuto:
#   1) unos minutos antes: recordatorio al trabajador
#   2) al vencer sin completarse: aviso al trabajador ("¿ya la hiciste?")
#   3) pasada la gracia sin respuesta: aviso al dueño (una sola vez)
# Solo en la ventana de avisos (por defecto 07:00 a 21:00) y sin repetir nunca el mismo aviso.

MAS_TIEMPO_RE = re.compile(r"\b(mas tiempo|me demoro|me demorare|me atraso|me atrase|un poco mas|necesito mas|dame mas)\b")


def etiqueta_plazo(vence_iso, hoy=None):
    """"2030-01-07 17:00" -> "hoy 17:00" / "mañana 10:00" / "vie 11/01 16:00"."""
    d = datetime.strptime(vence_iso, "%Y-%m-%d %H:%M")
    hoy = hoy or _hoy_naive().date()
    if d.date() == hoy:
        dia = "hoy"
    elif d.date() == hoy + timedelta(days=1):
        dia = "mañana"
    elif d.date() == hoy - timedelta(days=1):
        dia = "ayer"
    else:
        dia = f"{agenda.ABREV_DIA[d.weekday()]} {d.day:02d}/{d.month:02d}"
    return f"{dia} {d:%H:%M}"


def texto_plazo(vence_iso):
    """"vence hoy 17:00" o, si ya pasó, "venció hoy 17:00"."""
    verbo = "venció" if vence_iso < _hoy_naive().strftime("%Y-%m-%d %H:%M") else "vence"
    return f"{verbo} {etiqueta_plazo(vence_iso)}"


def _en_horario_de_avisos(ahora_n, cfg):
    minutos = ahora_n.hour * 60 + ahora_n.minute
    return agenda.a_minutos(cfg["avisos_desde"]) <= minutos < agenda.a_minutos(cfg["avisos_hasta"])


def ejecutar_seguimiento(salida):
    """Revisa las tareas con plazo y envía los avisos que correspondan. Se llama cada minuto.

    Devuelve [(tipo, tarea_id)] de lo que se envió (tipo: previo | vencida | escalada).
    """
    cfg = negocio.config()["seguimiento"]
    ahora_n = _hoy_naive()
    if not cfg["activo"] or not _en_horario_de_avisos(ahora_n, cfg):
        return []
    hechos = []
    for t in db.tareas_con_plazo():
        vence = datetime.strptime(t["vence_en"], "%Y-%m-%d %H:%M")
        faltan = (vence - ahora_n).total_seconds() / 60
        quien, tel, tarea = t["resp_nombre"], t["resp_telefono"], t["actividad"]
        try:
            if not t["rec_previo"] and 0 < faltan <= cfg["recordar_min_antes"]:
                _enviar_con_botones(
                    salida, tel, f"⏰ En {max(1, round(faltan))} min vence: *{tarea}*"
                    + ("" if _botones_activos() else "\n✅ *listo* · ⏳ *más tiempo*"),
                    botones_tarea(t["id"], con_problema=False))
                db.marcar_seguimiento(t["id"], "rec_previo")
                hechos.append(("previo", t["id"]))
            elif faltan <= 0 and not t["rec_vencida_en"]:
                _enviar_con_botones(
                    salida, tel, f"⏳ Ya pasó la hora de *{tarea}* ({etiqueta_plazo(t['vence_en'])}). ¿La hiciste?"
                    + ("" if _botones_activos() else "\n✅ *listo* · ⚠️ *problema* · ⏳ *más tiempo*"),
                    botones_tarea(t["id"]))
                db.marcar_seguimiento(t["id"], "rec_previo")
                db.marcar_seguimiento(t["id"], "rec_vencida_en", ahora_n.isoformat())
                estado = db.get_estado(t["resp_id"])
                estado["seguimiento_tarea_id"] = t["id"]  # para que "más tiempo" se refiera a esta tarea
                db.set_estado(t["resp_id"], estado)
                hechos.append(("vencida", t["id"]))
            elif (t["rec_vencida_en"] and not t["aviso_dueno"]
                  and (ahora_n - datetime.fromisoformat(t["rec_vencida_en"])).total_seconds() / 60 >= cfg["gracia_min"]):
                if t["resp_rol"] != "maestro":  # el dueño no se avisa a sí mismo
                    _notificar_duenos(salida, f"🔴 *{quien}* no completó a tiempo: {tarea}\n"
                                      f"Vencía {etiqueta_plazo(t['vence_en'])}. Ya le avisé.")
                db.marcar_seguimiento(t["id"], "aviso_dueno")
                hechos.append(("escalada", t["id"]))
        except Exception as e:  # un fallo de envío no debe frenar al resto; se reintenta el siguiente minuto
            print(f"[ERROR seguimiento] tarea {t['id']}: {type(e).__name__}: {e}")
    return hechos


def _parse_plazo(texto, ahora_n):
    """Nuevo plazo escrito por el trabajador: "en 30 minutos", "6pm", "a las 6:30", "mañana a las 10". None si no se entiende."""
    t = norm(texto)
    m = re.search(r"\ben (\d+|una|un|media)\s*(minutos?|mins?|horas?|hrs?)\b", t)
    if m:
        cantidad = {"una": 1, "un": 1, "media": 0.5}.get(m.group(1)) or int(m.group(1))
        minutos = cantidad * (60 if m.group(2).startswith(("h", "hr")) else 1)
        return (ahora_n + timedelta(minutes=minutos)).replace(second=0, microsecond=0) if 0 < minutos <= 24 * 60 else None
    m = re.search(r"(\d{1,2}(?::\d{2})?\s*(?:a\.?\s?m\.?|p\.?\s?m\.?)?)", texto.lower())
    hora = agenda.parse_hora(m.group(1).replace(" ", "")) if m else None
    if hora is None:
        return None
    manana = "manana" in t
    dt = datetime.combine(ahora_n.date() + timedelta(days=1 if manana else 0), datetime.min.time())
    dt += timedelta(minutes=agenda.a_minutos(hora))
    if dt <= ahora_n and not manana:
        dt += timedelta(days=1)  # esa hora ya pasó hoy: se entiende mañana
    return dt


def texto_reporte_estado(nombre=None):
    """Reporte del estado de las tareas (las de hoy y las que siguen abiertas) con los datos guardados.

    Si se da un nombre, solo las de esa persona. Devuelve None si no hay nada que reportar.
    """
    pid = None
    if nombre:
        fila = next((w for w in db.asignables() if w["nombre"].lower() == nombre.lower()), None)
        pid = fila["id"] if fila else None
    filas = db.estado_tareas(pid)
    if not filas:
        return None
    hechas = [f for f in filas if f["estado"] == "completada"]
    con_inc = [f for f in filas if f["estado"] == "con_incidencia"]
    ahora_iso = _hoy_naive().strftime("%Y-%m-%d %H:%M")
    abiertas = [f for f in filas if f["estado"] not in ("completada", "con_incidencia")]
    atrasadas = [f for f in abiertas if f["vence_en"] and f["vence_en"] < ahora_iso]
    ids_atrasadas = {f["id"] for f in atrasadas}
    abiertas = [f for f in abiertas if f["id"] not in ids_atrasadas]
    titulo = "📊 *Estado de las tareas" + (f" de {nombre}*" if pid else "*") + f" · {ahora().strftime('%d/%m %H:%M')}"
    lineas = [titulo, ""]
    if hechas:
        lineas.append(f"✅ *Completadas* ({len(hechas)})")
        for f in hechas:
            hora = f" ({f['completada_en'][11:16]})" if f["completada_en"] else ""
            lineas.append(f"• {f['nombre']}: {f['actividad']}{hora}")
        lineas.append("")
    if atrasadas:
        lineas.append(f"🔴 *Atrasadas* ({len(atrasadas)})")
        lineas += [f"• {f['nombre']}: {f['actividad']} ({texto_plazo(f['vence_en'])})" for f in atrasadas]
        lineas.append("")
    if abiertas:
        lineas.append(f"⏳ *Pendientes* ({len(abiertas)})")
        lineas += [f"• {f['nombre']}: {f['actividad']}" + (f" ({texto_plazo(f['vence_en'])})" if f["vence_en"] else "")
                   for f in abiertas]
        lineas.append("")
    if con_inc:
        lineas.append(f"⚠️ *Con incidencia* ({len(con_inc)})")
        for f in con_inc:
            detalle = f" — {f['incidencia'][:80]}" if f["incidencia"] else ""
            lineas.append(f"• {f['nombre']}: {f['actividad']}{detalle}")
        lineas.append("")
    lineas.append(f"Van {len(hechas)} de {len(filas)} completadas.")
    return "\n".join(lineas)


def _es_consulta_agenda(t):
    return len(t) <= 90 and not re.search(r"\d", t) and any(r.search(t) for r in CONSULTA_AGENDA_RES)


def _rango_agenda(t, hoy):
    """Qué días pregunta: hoy, mañana, pasado mañana, un día de la semana o los próximos 7 días."""
    if "pasado manana" in t:
        d = hoy + timedelta(days=2)
        return d, d, "de pasado mañana"
    if "manana" in t:
        d = hoy + timedelta(days=1)
        return d, d, "de mañana"
    if "hoy" in t and "semana" not in t:
        return hoy, hoy, "de hoy"
    for palabra in t.split():
        dia = agenda.parse_dia(palabra, hoy) if palabra in agenda._INDICE_DIA else None
        if dia:
            return dia, dia, f"del {agenda.NOMBRES_DIA[dia.weekday()]}"
    if "semana" in t:
        return hoy, hoy + timedelta(days=6), "de los próximos 7 días"
    return hoy, hoy + timedelta(days=1), "de hoy y mañana"


def texto_agenda(ini, fin, etiqueta):
    """Citas confirmadas entre dos días, agrupadas por día."""
    filas = db.citas_en_rango(ini.isoformat(), fin.isoformat())
    if not filas:
        return f"No hay citas agendadas {etiqueta}."
    hoy = _hoy_naive().date()
    lineas = [f"📅 *Citas {etiqueta}* ({len(filas)})", ""]
    dia_actual = None
    for f in filas:
        if f["fecha_hora"][:10] != dia_actual:
            dia_actual = f["fecha_hora"][:10]
            lineas.append(f"*{agenda.etiqueta_dia(date.fromisoformat(dia_actual), hoy)}*")
        quien = f["cliente_nombre"] if f["mascota"] == f["cliente_nombre"] else f"{f['mascota']} ({f['cliente_nombre']})"
        lineas.append(f"• {f['fecha_hora'][11:16]} — {quien} · {f['servicio']}")
    return "\n".join(lineas)


def _responder_agenda(salida, persona, t, prop=None):
    ini, fin, etiqueta = _rango_agenda(t, _hoy_naive().date())
    texto = texto_agenda(ini, fin, etiqueta)
    if prop:
        texto += "\n\n(Sigo esperando tu *SÍ* o *NO* para la propuesta anterior.)"
    _decir(salida, persona, texto)


def _nombre_en_texto(t):
    """Si el mensaje menciona a UNA persona del equipo, su nombre; si no, None (se reporta a todos)."""
    palabras = set(t.split())
    halladas = [w["nombre"] for w in db.asignables() if norm(w["nombre"]) in palabras]
    return halladas[0] if len(halladas) == 1 else None


def _es_consulta_estado(t):
    return len(t) <= 90 and any(r.search(t) for r in CONSULTA_ESTADO_RES)


def _responder_estado(salida, dueno, nombre, prop):
    """Responde con el estado real de las tareas; no toca la propuesta pendiente del dueño."""
    if nombre and not any(w["nombre"].lower() == nombre.lower() for w in db.asignables()):
        texto = f"No encuentro a {nombre} en el equipo."
    else:
        texto = texto_reporte_estado(nombre) or (
            f"{nombre} no tiene tareas de hoy ni pendientes." if nombre else "No hay tareas de hoy ni pendientes.")
    if prop:
        texto += "\n\n(Sigo esperando tu *SÍ* o *NO* para la propuesta anterior.)"
    _decir(salida, dueno, texto)


def _avisar_completada(salida, persona, actividad):
    """Avisa al dueño apenas alguien del equipo completa una tarea (si el negocio no lo apagó)."""
    if not negocio.config()["avisar_completadas"] or persona["rol"] == "maestro":
        return
    filas = db.estado_tareas()
    hechas = sum(1 for f in filas if f["estado"] == "completada")
    _notificar_duenos(
        salida, f"✅ *{persona['nombre']}* completó: {actividad}\nVan {hechas} de {len(filas)} tareas completadas.")


def _extras(persona):
    return [x for x in (persona["extras"] or "").split(",") if x]


def _es_asignable(persona):
    return persona["rol"] == "trabajador" or "trabajador" in _extras(persona)


def _responde_como_trabajador(persona, estado, t, texto):
    """¿Este mensaje del dueño es una respuesta de trabajador (sobre sus propias tareas)?"""
    pid = persona["id"]
    esperando = estado.get("esperando")
    if esperando in ("detalle_incidencia", "nuevo_plazo"):
        return True
    if esperando in ("cual_tarea", "cual_incidencia", "cual_plazo"):
        if texto.strip().isdigit() or _en(t, NO):
            return True
        # cambió de tema: se abandona el paso pendiente para no dejarlo atrapado
        db.set_estado(pid, {k: v for k, v in estado.items()
                            if k not in ("esperando", "tareas", "tarea_id", "actividad", "intentos")})
    if not db.tareas_pendientes(pid):
        return False
    if texto.strip() in ("✅", "⚠️", "⚠") or t in INTENCION_TRABAJADOR:
        return True
    return len(t) <= 30 and bool(PENDIENTE_RE.search(texto))


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
                _enviar_con_botones(salida, w["telefono"], mensaje_trabajador(w["nombre"], t, botones=_botones_activos()),
                                    botones_tarea(it["tarea_id"], con_mas_tiempo=bool(t.get("vence"))))
                enviadas.append(f"{w['nombre']}: {t['actividad']}")
            except Exception as e:
                print(f"[ERROR envío] tarea {it['tarea_id']} -> {w['nombre']}: {e}")
                db.registrar_envio_fallido(it["tarea_id"], str(e)[:200])
                fallidas.append(f"{w['nombre']}: {t['actividad']}")
        estado = db.get_estado(dueno["id"])  # para poder deshacer este envío un rato
        estado["ultimo_lote"] = {"ids": [it["tarea_id"] for it in res["items"]], "hora": _ahora_naive().isoformat()}
        db.set_estado(dueno["id"], estado)
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
        estado = "" if t["estado"] == "pendiente" else " ⚠️"  # con problema reportado
        plazo = f" · {texto_plazo(t['vence_en'])}" if t["vence_en"] else ""
        lineas.append(f"{i}. {t['actividad']}{estado}{plazo}")
    return "\n".join(lineas)


def _resumen_ids(tareas):
    return [{"id": t["id"], "actividad": t["actividad"]} for t in tareas]


def _pedir_plazo(salida, persona, tarea):
    db.set_estado(persona["id"], {"esperando": "nuevo_plazo", "tarea_id": tarea["id"], "actividad": tarea["actividad"],
                                  "intentos": 0})
    _decir(salida, persona, f"Claro. ¿Para qué hora terminas *{tarea['actividad']}*? "
           "Escríbela así: *6pm*, *18:30* o *en 30 minutos*.")


def _notificar_duenos(salida, mensaje):
    for d in db.duenos():
        try:
            salida.enviar(d["telefono"], mensaje)
        except Exception as e:
            print(f"[ERROR notif dueño] {e}")


def _marcar_listas(salida, persona, candidatas):
    """Completa esas tareas (solo las abiertas y suyas), le confirma a la persona y avisa al dueño de cada una."""
    nombre = persona["nombre"]
    hechas = [t for t in candidatas if db.completar_tarea(t["id"], persona["id"])]
    if not hechas:
        _decir(salida, persona, "Esas tareas ya no están abiertas." if len(candidatas) > 1 else "Esa tarea ya no está abierta.")
        return
    if len(hechas) == 1:
        _decir(salida, persona, f"✅ Listo, marqué *{hechas[0]['actividad']}*. ¡Buen trabajo, {nombre}!")
    else:
        _decir(salida, persona, "✅ Marqué como completadas:\n" + "\n".join(f"• {h['actividad']}" for h in hechas)
               + f"\n¡Buen trabajo, {nombre}!")
    for h in hechas:
        _avisar_completada(salida, persona, h["actividad"])


def _aplicar_plazo(salida, persona, tarea_id, actividad, iso):
    """Nuevo plazo para una tarea de esa persona; avisa al dueño (si quien pide más tiempo no es el dueño)."""
    if db.cambiar_vence(tarea_id, persona["id"], iso):
        db.registrar_evento(tarea_id, "plazo", etiqueta_plazo(iso))  # queda en el historial de la tarea
        _decir(salida, persona, f"Listo, nuevo plazo: *{etiqueta_plazo(iso)}*. Te recuerdo antes de que venza.")
        if persona["rol"] != "maestro":
            _notificar_duenos(salida, f"⏳ *{persona['nombre']}* pidió más tiempo: {actividad}\n"
                              f"Nuevo plazo: {etiqueta_plazo(iso)}.")
    else:
        _decir(salida, persona, "Esa tarea ya no está abierta.")


MINUTOS_BOTON = {"15": "15 min", "30": "30 min", "60": "1 hora"}


def procesar_boton(salida, persona, boton):
    """Un toque en un botón de WhatsApp ("listo:12", "problema:12", "mas:12", "plazo:12:30"). Solo sobre tareas DE ESA persona.

    Cada id lleva el número de la tarea, así no hay que preguntar "¿cuál?". Si la tarea ya no está abierta (se tocó dos
    veces, o la cancelaron) se dice y no se hace nada.
    """
    if _linea(salida) != "central" or not _es_asignable(persona):
        return
    partes = str(boton).split(":")
    try:
        accion, tarea_id = partes[0], int(partes[1])
    except (IndexError, ValueError):
        return  # botón que no es de Pati: se ignora
    tarea = db.tarea_abierta_de(tarea_id, persona["id"])
    if tarea is None:
        _decir(salida, persona, "Esa tarea ya no está abierta.")
        return
    pid, actividad = persona["id"], tarea["actividad"]
    if accion == "listo":
        db.set_estado(pid, {k: v for k, v in db.get_estado(pid).items() if k not in ("esperando", "tareas", "tarea_id", "actividad")})
        _marcar_listas(salida, persona, [{"id": tarea_id, "actividad": actividad}])
    elif accion == "problema":
        db.set_estado(pid, {"esperando": "detalle_incidencia", "tarea_id": tarea_id, "actividad": actividad})
        _decir(salida, persona, f"¿Qué pasó con *{actividad}*?")
    elif accion == "mas":
        db.set_estado(pid, {"esperando": "nuevo_plazo", "tarea_id": tarea_id, "actividad": actividad, "intentos": 0})
        _decir(salida, persona, f"⏳ ¿Cuánto más necesitas para *{actividad}*? Elige o escribe una hora (ej. *6pm*).",
               botones=[(txt, f"plazo:{tarea_id}:{m}") for m, txt in MINUTOS_BOTON.items()])
    elif accion == "plazo" and len(partes) == 3 and partes[2] in MINUTOS_BOTON:
        db.set_estado(pid, {k: v for k, v in db.get_estado(pid).items() if k not in ("esperando", "tareas", "tarea_id", "actividad")})
        nuevo = (_hoy_naive() + timedelta(minutes=int(partes[2]))).replace(second=0, microsecond=0)
        _aplicar_plazo(salida, persona, tarea_id, actividad, nuevo.strftime("%Y-%m-%d %H:%M"))


PLAZO_PREGUNTA_RE = re.compile(r"\b(cuanto tiempo|cuanto me queda|que plazo|cual es el plazo|cuando vence|hasta cuando|"
                               r"para cuando|hasta que hora|a que hora (vence|debo|tengo))\b")
PIDE_AL_DUENO_RE = re.compile(r"\b(me pueden|me puedes|me podrian|me podrias|pueden|puedes|podrian|podrias|necesito|quiero|"
                              r"no puedo|no alcanzo|no llego|reasign\w*|re asign\w*|cambi\w*|ayudenme|urgente|permiso|"
                              r"faltare|llegare tarde)\b")
TRIVIALES = {"ok", "okay", "oki", "okis", "gracias", "vale", "dale", "bueno", "si", "no", "ya", "entendido", "perfecto",
             "genial", "claro", "jaja", "jajaja", "de nada", "listo pe", "gracias pati"}
MAX_REENVIOS_HORA = 5  # tope de mensajes de un mismo trabajador que se le pasan al dueño por hora
SALUDOS = {"hola", "holi", "holaa", "hello", "hey", "buenas", "buen dia", "buenos dias", "buenas tardes", "buenas noches"}
SOBRE_TAREAS_RE = re.compile(r"\b(tareas?|pendientes?|asignad[oa]s?|encargos?)\b")  # texto sin tildes


def _numeros_elegidos(texto, n):
    """"1", "1 y 3", "1,3", "todas" -> [1, 3] (números válidos de una lista de n). None si no se entiende."""
    t = norm(texto)
    if t in {"todas", "todo", "todos", "las dos", "los dos", "ambas", "ambos"}:
        return list(range(1, n + 1))
    if not re.fullmatch(r"[\d\sy]+", t):
        return None
    nums = sorted({int(x) for x in re.findall(r"\d+", t)})
    return nums if nums and all(1 <= x <= n for x in nums) else None


def _pasar_al_dueno(salida, persona, texto, t, estado):
    """Lo que un trabajador escribe y Pati no resuelve (un pedido, una duda) se lo pasa al dueño y se lo dice al trabajador.

    No se pasan los "ok" / "gracias" / emojis, ni más de MAX_REENVIOS_HORA por hora (para no saturar al dueño).
    """
    if t in TRIVIALES or len(t) <= 2 or not any(c.isalpha() for c in t):
        return
    ahora_n = _hoy_naive()
    recientes = [x for x in estado.get("reenvios", []) if (ahora_n - datetime.fromisoformat(x)).total_seconds() < 3600]
    if len(recientes) >= MAX_REENVIOS_HORA:
        return
    duenos = db.duenos()
    if not duenos:
        _decir(salida, persona, "Por ahora no puedo pasarle tu mensaje a nadie 😕")
        return
    db.set_estado(persona["id"], {**estado, "reenvios": recientes + [ahora_n.isoformat()]})
    _notificar_duenos(salida, f"💬 *{persona['nombre']}* te escribió:\n«{texto.strip()[:500]}»")
    _decir(salida, persona, f"Se lo pasé a {duenos[0]['nombre']} 👍")


def flujo_trabajador(salida, persona, texto):
    pid = persona["id"]
    nombre = persona["nombre"]
    t = norm(texto)
    estado = db.get_estado(pid)
    esperando = estado.get("esperando")

    def limpiar():
        db.set_estado(pid, {})

    # El detalle de un problema se espera un rato; si pasó mucho, lo que escribe ahora es otra cosa
    if esperando == "detalle_checkin" and (
            not estado.get("desde")
            or (_hoy_naive() - datetime.fromisoformat(estado["desde"])).total_seconds() > DETALLE_VIGENTE_MIN * 60):
        limpiar()
        estado, esperando = {}, None

    # Quien atiende a los clientes (rol "atencion") puede consultar la agenda de citas
    if "atencion" in _extras(persona) and esperando != "detalle_incidencia" and _es_consulta_agenda(t):
        _responder_agenda(salida, persona, t)
        return

    # Pide ayuda: se responde sin perder el paso en curso (salvo si está escribiendo el detalle de un problema)
    if esperando != "detalle_incidencia" and _es_pedido_ayuda(t):
        _decir(salida, persona, texto_ayuda_trabajador(persona, t))
        return

    # Pasos de una conversación en curso
    if esperando in ("cual_tarea", "cual_incidencia"):
        if _en(t, NO):
            limpiar()
            _decir(salida, persona, "Ok, lo dejamos así.")
            return
        tareas = estado["tareas"]
        if esperando == "cual_tarea":  # se pueden completar varias: "1 y 3", "1,3", "todas"
            numeros = _numeros_elegidos(texto, len(tareas))
            if numeros:
                limpiar()
                _marcar_listas(salida, persona, [tareas[n - 1] for n in numeros])
                return
        elif texto.strip().isdigit() and 1 <= int(texto.strip()) <= len(tareas):  # "cual_incidencia": una sola
            elegida = tareas[int(texto.strip()) - 1]
            db.set_estado(pid, {"esperando": "detalle_incidencia", "tarea_id": elegida["id"],
                                "actividad": elegida["actividad"]})
            _decir(salida, persona, f"¿Qué pasó con *{elegida['actividad']}*?")
            return
        _decir(salida, persona, f"Responde con el número (1 al {len(tareas)})"
               + (", o *1 y 3* si fueron varias." if esperando == "cual_tarea" else "."))
        return

    if esperando == "cual_plazo":
        if _en(t, NO):
            limpiar()
            _decir(salida, persona, "Ok, lo dejamos así.")
        elif texto.strip().isdigit() and 1 <= int(texto.strip()) <= len(estado["tareas"]):
            _pedir_plazo(salida, persona, estado["tareas"][int(texto.strip()) - 1])
        else:
            _decir(salida, persona, f"Responde con el número (1 al {len(estado['tareas'])}).")
        return

    if esperando == "nuevo_plazo":
        if _en(t, NO):
            limpiar()
            _decir(salida, persona, "Ok, dejo el plazo como estaba.")
            return
        nuevo = _parse_plazo(texto, _hoy_naive())
        if nuevo is None:
            intentos = estado.get("intentos", 0) + 1
            if intentos >= 2:
                limpiar()
                _decir(salida, persona, "No logré entender la hora 😅 Escribe *más tiempo* para intentarlo de nuevo.")
            else:
                db.set_estado(pid, {**estado, "intentos": intentos})
                _decir(salida, persona, "No entendí la hora. Escríbela así: *6pm*, *18:30* o *en 30 minutos*.")
            return
        limpiar()
        _aplicar_plazo(salida, persona, estado["tarea_id"], estado["actividad"], nuevo.strftime("%Y-%m-%d %H:%M"))
        return

    if esperando == "detalle_checkin":  # detalle de un "tengo un problema" votado en la encuesta (no es de una tarea)
        limpiar()
        if _en(t, NO) or t in ("nada", "ninguno", "despues"):
            _decir(salida, persona, "Ok, sin detalles. Si luego quieres contarme, escríbeme.")
            return
        enc = db.encuesta(estado.get("encuesta_id") or 0)
        if enc is not None:
            db.guardar_detalle_voto(enc["id"], pid, texto)
        _notificar_duenos(salida, f"⚠️ *{nombre}* cuenta qué pasa" + (f" (encuesta «{enc['pregunta']}»)" if enc else "")
                          + f":\n{texto}")
        _decir(salida, persona, "Anotado, se lo aviso al dueño.")
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

    # "Necesito más tiempo": pide un nuevo plazo para una tarea (la que Pati le recordó, o la única que tiene)
    if MAS_TIEMPO_RE.search(t):
        abiertas = [x for x in db.tareas_pendientes(pid) if x["estado"] == "pendiente"]
        if not abiertas:
            _decir(salida, persona, f"No tienes tareas pendientes ahora mismo, {nombre}.")
            return
        elegida = next((x for x in abiertas if x["id"] == estado.get("seguimiento_tarea_id")), None)
        if elegida is None and len(abiertas) == 1:
            elegida = abiertas[0]
        if elegida is not None:
            _pedir_plazo(salida, persona, {"id": elegida["id"], "actividad": elegida["actividad"]})
        else:
            db.set_estado(pid, {"esperando": "cual_plazo", "tareas": _resumen_ids(abiertas)})
            if not enviar_selector_trabajador(salida, persona, "selector_mas", abiertas):
                _decir(salida, persona, f"¿Para cuál tarea necesitas más tiempo?\n\n{lista_tareas(abiertas)}\n\n"
                       "Responde con el número.")
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
            if not enviar_selector_trabajador(salida, persona, "selector_problema", tareas):
                _decir(salida, persona, f"¿El problema es con cuál tarea?\n\n{lista_tareas(tareas)}\n\n"
                       "Responde con el número.")
        return

    if COMPLETADO_RE.search(texto):
        tareas = db.tareas_pendientes(pid)
        if not tareas:
            _decir(salida, persona, f"No tienes tareas pendientes ahora mismo, {nombre}.")
        elif len(tareas) == 1:
            if db.completar_tarea(tareas[0]["id"], pid):
                _decir(salida, persona, f"✅ ¡Perfecto, {nombre}! Marqué *{tareas[0]['actividad']}* como completada.")
                _avisar_completada(salida, persona, tareas[0]["actividad"])
            else:
                _decir(salida, persona, "Esa tarea ya no está abierta.")
        else:
            db.set_estado(pid, {"esperando": "cual_tarea", "tareas": _resumen_ids(tareas)})
            if not enviar_selector_trabajador(salida, persona, "selector_completar", tareas):
                _decir(salida, persona, f"¿Cuál terminaste?\n{lista_tareas(tareas)}\n"
                       "Responde con el número (o *1 y 3* si fueron varias).")
        return

    # Saludo
    if t in SALUDOS:
        tareas = db.tareas_pendientes(pid)
        _decir(salida, persona, f"¡Hola, {nombre}! 🐾 " + (f"📋 Tus pendientes:\n{lista_tareas(tareas)}" if tareas
                                                          else "No tienes tareas pendientes 🎉"))
        return

    pide_al_dueno = bool(PIDE_AL_DUENO_RE.search(t))

    # "¿cuánto tiempo tengo?": sus plazos, con datos reales
    if PLAZO_PREGUNTA_RE.search(t) and len(t) <= 90 and not pide_al_dueno:
        tareas = db.tareas_pendientes(pid)
        _decir(salida, persona, "No tienes tareas pendientes 🎉" if not tareas else "⏰ Tus plazos:\n" + "\n".join(
            f"{i}. {x['actividad']} · {texto_plazo(x['vence_en']) if x['vence_en'] else 'sin plazo'}"
            for i, x in enumerate(tareas, 1)))
        return

    # Una pregunta simple sobre sus tareas: sus pendientes
    if SOBRE_TAREAS_RE.search(t) and not pide_al_dueno:
        tareas = db.tareas_pendientes(pid)
        _decir(salida, persona, f"📋 Tus pendientes:\n{lista_tareas(tareas)}" if tareas else "No tienes tareas pendientes 🎉")
        return

    # Todo lo demás (pedidos, dudas, "me pueden reasignar...") Pati no lo resuelve: se lo pasa al dueño
    _pasar_al_dueno(salida, persona, texto, t, estado)


# ── Flujo del cliente ─────────────────────────────────────────────────────────

def _menu_cliente(neg):
    mis_citas = "\nEscribe *mis citas* para ver o cancelar tus citas." if neg.get("agenda") else ""
    return (
        f"Hola 👋 Soy Pati, la asistente virtual de {neg['nombre']}.\n\n"
        "¿En qué te ayudo?\n"
        "1️⃣ Ver servicios\n"
        "2️⃣ Agendar una cita\n"
        "3️⃣ Hablar con una persona\n\n"
        "Responde con el número o escribe lo que necesitas." + mis_citas
    )


def _notificar_atencion(salida, mensaje):
    """Avisa a quien atiende a los clientes (rol adicional "atencion", puede ser el propio dueño).

    Va por la línea central a su número personal. Si nadie tiene ese rol, se avisa al dueño.
    """
    for d in db.personas_con_extra("atencion") or db.duenos():
        try:
            salida.enviar(d["telefono"], mensaje)
        except Exception as e:
            print(f"[ERROR aviso a atención] {e}")


def _derivar(salida, persona, motivo, texto):
    """Pasa al cliente con una persona: se avisa a atención (no al dueño, salvo que él atienda)."""
    _notificar_atencion(
        salida,
        f"📞 {persona['nombre']} ({persona['telefono']}) quiere {motivo}.\nSu mensaje: {texto}",
    )


# ── Ficha del cliente y citas por WhatsApp (reglas fijas, sin IA) ────────────
#
# Conversaciones guiadas por pasos que viven en el estado de la persona (sobreviven a reinicios):
#   nombre   -> pide el nombre al primer contacto
#   cita     -> servicio, día, hora, mascota (si aplica) y confirmación
#   cancelar -> elegir la cita y confirmar (con opción de reprogramar)
# Solo agenda si el negocio configuró su agenda (negocio.json); si no, deriva a una persona.

SALIR_SIEMPRE = {"salir", "menu", "volver", "inicio"}
SALIR_EN_CITA = {"cancelar", "nada", "olvidalo", "dejalo", "no gracias"}
OMITIR = {"omitir", "no", "paso", "saltar", "no quiero", "prefiero no decirlo", "prefiero no decir"}
NO_SON_NOMBRES = {"hola", "buenas", "buenos dias", "buenas tardes", "buenas noches", "si", "ok", "gracias",
                  "quiero una cita"}
INTENCION_PALABRAS = {"servicio", "servicios", "cita", "citas", "precio", "precios", "horario", "horarios", "info",
                      "informacion", "persona", "ayuda", "menu", "cancelar", "agendar", "reservar", "turno"}
CANCELAR_CITA_RE = re.compile(r"\b(cancelar|anular)\b.*\bcita\b|\bcita\b.*\b(cancelar|anular)\b")
REPROGRAMAR_RE = re.compile(r"\b(reprogramar|reagendar|cambiar (mi |la )?cita|mover (mi |la )?cita)\b")
MIS_CITAS_RE = re.compile(r"\bmis citas\b|\bque citas tengo\b|\bmi cita\b|\bmi proxima cita\b")
OTRO_DIA_RE = re.compile(r"\b(otro|otra|cambiar)\b.*\b(dia|fecha)\b")
FLUJO_CLAVES = ("flujo", "paso", "servicio", "duracion", "fecha", "hora", "mascota", "opciones", "libres", "intentos",
                "texto_inicial", "reprogramar", "cita_id", "flujo_ts")
MAX_INTENTOS = 3
FLUJO_CADUCA_HORAS = 3
MAX_HORAS_MOSTRADAS = 12


def _hoy_naive():
    return ahora().replace(tzinfo=None)


def _guardar_estado(pid, **cambios):
    est = db.get_estado(pid)
    for k, v in cambios.items():
        if v is None:
            est.pop(k, None)
        else:
            est[k] = v
    if "flujo" in cambios or "paso" in cambios:
        est["flujo_ts"] = _hoy_naive().isoformat()
    db.set_estado(pid, est)
    return est


def _terminar_flujo(pid):
    est = db.get_estado(pid)
    for k in FLUJO_CLAVES:
        est.pop(k, None)
    db.set_estado(pid, est)


def _flujo_vencido(est):
    ts = est.get("flujo_ts")
    return bool(ts) and (_hoy_naive() - datetime.fromisoformat(ts)).total_seconds() > FLUJO_CADUCA_HORAS * 3600


def _decir_flujo(salida, persona, texto):
    """Respuesta dentro de una conversación guiada: no cuenta para el tope de respuestas por hora."""
    db.log_mensaje(persona["id"], "pati", texto, _linea(salida) + "-flujo")
    salida.responder(texto)


PARTICULAS = {"de", "del", "la", "las", "los", "y", "e", "da", "van", "von"}


def _capitalizar(texto):
    """"maría josé de la cruz" -> "María José de la Cruz" (las partículas quedan en minúscula)."""
    palabras = texto.split()
    return " ".join(p if (i and p.lower() in PARTICULAS) else p[:1].upper() + p[1:] for i, p in enumerate(palabras))


def _parece_nombre(texto, max_palabras=5, digitos=False):
    limpio = " ".join(texto.split())
    if not (2 <= len(limpio) <= 40) or len(limpio.split()) > max_palabras:
        return False
    if not all(ch.isalpha() or ch in " '’.-" or (digitos and ch.isdigit()) for ch in limpio):
        return False
    t = norm(limpio)
    return t not in NO_SON_NOMBRES and not (set(t.split()) & INTENCION_PALABRAS)


def _es_intencion_cliente(t):
    """¿Ya está pidiendo algo concreto? Entonces no se le interrumpe con la pregunta del nombre."""
    return (t in ("1", "2", "3") or bool(SERVICIOS_RE.search(t)) or bool(CITA_CLIENTE_RE.search(t))
            or bool(PERSONA_RE.search(t)) or bool(CANCELAR_CITA_RE.search(t)) or bool(MIS_CITAS_RE.search(t))
            or bool(REPROGRAMAR_RE.search(t)))


def _elegir(t, opciones):
    """Índice de la opción elegida por número o por nombre (si coincide con una sola). None si no se entendió."""
    if t.isdigit():
        i = int(t) - 1
        return i if 0 <= i < len(opciones) else None
    if len(t) >= 3:
        coinciden = [i for i, o in enumerate(opciones) if t in norm(str(o)) or norm(str(o)) in t]
        if len(coinciden) == 1:
            return coinciden[0]
    return None


def _pide_sujeto(neg):
    """¿Hay que preguntar por la mascota? Solo en rubros donde el cliente y quien recibe el servicio son distintos."""
    return neg["sujeto"] != "cliente" and not neg.get("sujeto_es_cliente")


def _cuando(fecha_iso, hora, hoy):
    return f"{agenda.etiqueta_dia(date.fromisoformat(fecha_iso), hoy)} a las {hora}"


def _reintentar(salida, persona, neg, mensaje):
    pid = persona["id"]
    intentos = db.get_estado(pid).get("intentos", 0) + 1
    if intentos >= MAX_INTENTOS:
        _terminar_flujo(pid)
        _decir_flujo(salida, persona, "No logré entender 😅 Si prefieres, escribe *3* para hablar con una persona.\n\n"
                     + _menu_cliente(neg))
        return
    _guardar_estado(pid, intentos=intentos)
    _decir_flujo(salida, persona, mensaje)


def _avisar_cita(salida, titulo, datos):
    """Avisa a quien atiende cuando un cliente agenda o cancela con Pati (se apaga con avisar_citas)."""
    if negocio.config().get("avisar_citas", True):
        _notificar_atencion(
            salida,
            f"{titulo}\n{datos['cliente_nombre']} ({datos['telefono']})\n{datos['servicio']} · {datos['mascota']}\n"
            f"{datos['fecha_hora']}",
        )


# ── Nombre del cliente ────────────────────────────────────────────────────────

def _preguntar_nombre_primer_contacto(salida, persona, neg, texto):
    _guardar_estado(persona["id"], flujo="nombre", paso="nombre", intentos=0, texto_inicial=texto)
    _decir_flujo(salida, persona, f"Hola 👋 Soy Pati, la asistente virtual de {neg['nombre']}.\n"
                 "Para atenderte mejor, ¿cómo te llamas? (si prefieres no decirlo, escribe *omitir*)")


def _guardar_nombre(pid, texto):
    nombre = _capitalizar(" ".join(texto.split()))
    db.actualizar_nombre(pid, nombre)
    _guardar_estado(pid, nombre_ok=True, nombre_dado=True)
    return nombre


def _paso_nombre(salida, persona, texto, t, est, neg):
    pid = persona["id"]
    if t in OMITIR:
        _guardar_estado(pid, nombre_ok=True)
        _terminar_flujo(pid)
        _decir_flujo(salida, persona, "Sin problema 😊")
    elif _parece_nombre(texto):
        nombre = _guardar_nombre(pid, texto)
        _terminar_flujo(pid)
        _decir_flujo(salida, persona, f"Mucho gusto, {nombre} 🐾")
    elif est.get("intentos", 0) + 1 >= 2:
        _guardar_estado(pid, nombre_ok=True)
        _terminar_flujo(pid)
    else:
        _guardar_estado(pid, intentos=est.get("intentos", 0) + 1)
        _decir_flujo(salida, persona, "No te entendí bien. ¿Cómo te llamas? (o escribe *omitir*)")
        return
    # Se atiende lo que escribió al principio (o se muestra el menú)
    flujo_cliente(salida, db.persona_por_id(persona["telefono"]), est.get("texto_inicial") or "hola")


# ── Agendar una cita ──────────────────────────────────────────────────────────

def _iniciar_cita(salida, persona, neg, servicio=None):
    pid = persona["id"]
    if persona["rol"] == "cliente" and not db.get_estado(pid).get("nombre_dado"):
        _guardar_estado(pid, flujo="cita", paso="nombre", intentos=0, servicio=servicio)
        _decir_flujo(salida, persona, "¡Claro! Para agendar necesito tu nombre. ¿Cómo te llamas?")
        return
    _preguntar_servicio(salida, persona, neg, servicio)


def _preguntar_servicio(salida, persona, neg, servicio=None):
    pid = persona["id"]
    servicios = neg["servicios_detalle"]
    elegido = next((s for s in servicios if servicio and s["nombre"].lower() == servicio.lower()), None)
    if elegido is None and len(servicios) == 1:
        elegido = servicios[0]
    if elegido:
        _guardar_estado(pid, flujo="cita", servicio=elegido["nombre"], duracion=elegido["duracion_min"], intentos=0)
        _preguntar_dia(salida, persona, neg)
        return
    _guardar_estado(pid, flujo="cita", paso="servicio", opciones=[s["nombre"] for s in servicios], intentos=0)
    _decir_flujo(salida, persona, "\n".join(
        ["¿Qué servicio necesitas?", ""] + [f"{i}. {s['nombre']}" for i, s in enumerate(servicios, 1)]
        + ["", "Responde con el número."]))


def _preguntar_dia(salida, persona, neg, mensaje=None):
    pid = persona["id"]
    est = db.get_estado(pid)
    ahora_n = _hoy_naive()
    dias = agenda.dias_con_huecos(neg["agenda"], est["duracion"], lambda d: db.citas_ocupadas(d.isoformat()), ahora_n)
    if not dias:
        _terminar_flujo(pid)
        _decir_flujo(salida, persona, "Ahora mismo no tengo horarios libres. Aviso al equipo para que una persona te ayude.")
        _derivar(salida, persona, "agendar una cita (no hay horarios libres)", "(sin mensaje)")
        return
    _guardar_estado(pid, paso="dia", opciones=[d.isoformat() for d in dias], intentos=0)
    lineas = [mensaje or f"Perfecto, *{est['servicio']}*. ¿Qué día te acomoda?", ""]
    lineas += [f"{i}. {agenda.etiqueta_dia(d, ahora_n.date())}" for i, d in enumerate(dias, 1)]
    lineas += ["", "Responde con el número, o escribe otra fecha (por ejemplo 25/10)."]
    _decir_flujo(salida, persona, "\n".join(lineas))


def _preguntar_hora(salida, persona, neg, dia):
    pid = persona["id"]
    est = db.get_estado(pid)
    libres = agenda.huecos(neg["agenda"], dia, est["duracion"], db.citas_ocupadas(dia.isoformat()), _hoy_naive())
    if not libres:
        _preguntar_dia(salida, persona, neg, "Ese día ya no tiene horarios libres. Elige otro:")
        return
    mostradas = libres[:MAX_HORAS_MOSTRADAS]
    _guardar_estado(pid, paso="hora", fecha=dia.isoformat(), opciones=mostradas, libres=libres, intentos=0)
    lineas = [f"{agenda.etiqueta_dia(dia, _hoy_naive().date())}: estos horarios están libres:", ""]
    lineas += [f"{i}. {h}" for i, h in enumerate(mostradas, 1)]
    lineas += ["", "Responde con el número" + (" o escribe otra hora (por ejemplo 3pm)." if len(libres) > len(mostradas)
                                                else ".") + " Si prefieres otro día, escribe *otro día*."]
    _decir_flujo(salida, persona, "\n".join(lineas))


def _preguntar_mascota(salida, persona, neg):
    pid = persona["id"]
    if not _pide_sujeto(neg):
        _guardar_estado(pid, mascota=persona["nombre"])
        _pedir_confirmacion(salida, persona, neg)
        return
    nombres = [m["nombre"] for m in db.mascotas_de(pid)]
    _guardar_estado(pid, paso="mascota", opciones=nombres, intentos=0)
    if len(nombres) == 1:
        texto = f"¿La cita es para *{nombres[0]}*? Responde *SÍ* o escribe el nombre de otra {neg['sujeto']}."
    elif nombres:
        texto = (f"¿Para cuál {neg['sujeto']} es la cita?\n\n" + "\n".join(f"{i}. {n}" for i, n in enumerate(nombres, 1))
                 + "\n\nResponde con el número, o escribe el nombre de otra.")
    else:
        texto = f"¿Cómo se llama {neg['el_sujeto']}?"
    _decir_flujo(salida, persona, texto)


def _pedir_confirmacion(salida, persona, neg):
    pid = persona["id"]
    est = db.get_estado(pid)
    _guardar_estado(pid, paso="confirmar", intentos=0)
    lineas = ["Esto es lo que voy a agendar:", "", f"• Servicio: {est['servicio']}",
              f"• Día: {_cuando(est['fecha'], est['hora'], _hoy_naive().date())}"]
    if _pide_sujeto(neg):
        lineas.append(f"• {neg['Sujeto']}: {est['mascota']}")
    lineas += [f"• A nombre de: {persona['nombre']}", "", "¿Confirmas? Responde *SÍ* o *NO*."]
    _decir_flujo(salida, persona, "\n".join(lineas))


def _confirmar_cita(salida, persona, neg, est):
    pid = persona["id"]
    fecha_hora = f"{est['fecha']} {est['hora']}"
    cita_id = db.crear_cita_si_libre(persona["nombre"], persona["telefono"], est["mascota"], est["servicio"],
                                     fecha_hora, est["duracion"], neg["agenda"]["simultaneas"], cliente_id=pid)
    if cita_id is None:  # alguien más ocupó ese horario mientras tanto
        _preguntar_hora(salida, persona, neg, date.fromisoformat(est["fecha"]))
        _decir_flujo(salida, persona, "Ese horario acaba de ocuparse 😅 Elige otro, por favor.")
        return
    if _pide_sujeto(neg):
        db.agregar_mascota(pid, est["mascota"])
    _terminar_flujo(pid)
    _decir_flujo(salida, persona, f"✅ ¡Listo! Tu cita de *{est['servicio']}* quedó agendada para el "
                 f"{_cuando(est['fecha'], est['hora'], _hoy_naive().date())}.\n"
                 "Te enviaré un recordatorio antes. Si necesitas cancelarla, escribe *cancelar cita*.")
    _avisar_cita(salida, "📅 *Nueva cita agendada por Pati*",
                 {"cliente_nombre": persona["nombre"], "telefono": persona["telefono"], "servicio": est["servicio"],
                  "mascota": est["mascota"], "fecha_hora": fecha_hora})


def _paso_cita(salida, persona, texto, t, est, neg):
    pid = persona["id"]
    paso = est.get("paso")
    if paso == "nombre":
        if _parece_nombre(texto):
            _guardar_nombre(pid, texto)
            _preguntar_servicio(salida, db.persona_por_id(persona["telefono"]), neg, est.get("servicio"))
        elif est.get("intentos", 0) + 1 >= 2:
            _guardar_estado(pid, nombre_ok=True)  # sin nombre se usa el de WhatsApp
            _preguntar_servicio(salida, persona, neg, est.get("servicio"))
        else:
            _guardar_estado(pid, intentos=est.get("intentos", 0) + 1)
            _decir_flujo(salida, persona, "No te entendí bien. ¿Cómo te llamas?")
    elif paso == "servicio":
        i = _elegir(t, est["opciones"])
        if i is None:
            _reintentar(salida, persona, neg, "Elige el servicio con su número, por favor.")
            return
        s = next(x for x in neg["servicios_detalle"] if x["nombre"] == est["opciones"][i])
        _guardar_estado(pid, servicio=s["nombre"], duracion=s["duracion_min"])
        _preguntar_dia(salida, persona, neg)
    elif paso == "dia":
        hoy = _hoy_naive().date()
        dia = date.fromisoformat(est["opciones"][int(t) - 1]) if t.isdigit() and 1 <= int(t) <= len(est["opciones"]) \
            else agenda.parse_dia(texto.strip(), hoy)  # texto original: norm() borra la "/" de 25/10
        if dia is None:
            _reintentar(salida, persona, neg, "No entendí el día. Responde con el número de la lista o escribe una fecha "
                        "como 25/10.")
            return
        _preguntar_hora(salida, persona, neg, dia)
    elif paso == "hora":
        if OTRO_DIA_RE.search(t):
            _preguntar_dia(salida, persona, neg)
            return
        i = int(t) - 1 if t.isdigit() and 1 <= int(t) <= len(est["opciones"]) else None
        hora = est["opciones"][i] if i is not None else agenda.parse_hora(texto.strip())
        if hora is None or hora not in est["libres"]:
            _reintentar(salida, persona, neg, "Elige una de las horas libres (con su número) o escribe otra hora de la "
                        "lista.")
            return
        _guardar_estado(pid, hora=hora)
        _preguntar_mascota(salida, persona, neg)
    elif paso == "mascota":
        opciones = est.get("opciones") or []
        if len(opciones) == 1 and _es_si(t):
            nombre = opciones[0]
        elif t.isdigit() and 1 <= int(t) <= len(opciones):
            nombre = opciones[int(t) - 1]
        elif len(opciones) == 1 and _en(t, NO):
            _guardar_estado(pid, opciones=[], intentos=0)
            _decir_flujo(salida, persona, f"¿Cómo se llama {neg['el_sujeto']}?")
            return
        elif _parece_nombre(texto, 3, digitos=True):
            nombre = _capitalizar(" ".join(texto.split()))
        else:
            _reintentar(salida, persona, neg, f"No entendí el nombre {neg['del_sujeto']}. ¿Cómo se llama?")
            return
        _guardar_estado(pid, mascota=nombre)
        _pedir_confirmacion(salida, persona, neg)
    elif paso == "confirmar":
        if _es_si(t):
            _confirmar_cita(salida, persona, neg, est)
        elif _en(t, NO):
            _terminar_flujo(pid)
            _decir_flujo(salida, persona, "Listo, no agendé nada. Si quieres intentar con otro horario, escribe *2*.")
        else:
            _reintentar(salida, persona, neg, "Responde *SÍ* para confirmar o *NO* para cancelar.")


# ── Ver y cancelar citas ──────────────────────────────────────────────────────

def _cita_texto(c):
    return f"{c['servicio']} el {_cuando(c['fecha_hora'][:10], c['fecha_hora'][11:16], _hoy_naive().date())}"


def _mostrar_mis_citas(salida, persona):
    citas = db.citas_futuras_de(persona["telefono"], _hoy_naive().strftime("%Y-%m-%d %H:%M"))
    if not citas:
        _decir(salida, persona, "No tienes citas próximas. Si quieres agendar una, escribe *2*.")
        return
    _decir(salida, persona, "📅 *Tus próximas citas:*\n\n" + "\n".join(f"• {_cita_texto(c)}" for c in citas)
           + "\n\nPara cancelar una, escribe *cancelar cita*.")


def _iniciar_cancelar(salida, persona, neg, reprogramar=False):
    pid = persona["id"]
    citas = db.citas_futuras_de(persona["telefono"], _hoy_naive().strftime("%Y-%m-%d %H:%M"))
    if not citas:
        _decir(salida, persona, "No tienes citas próximas para " + ("cambiar." if reprogramar else "cancelar."))
        return
    if len(citas) == 1:
        _guardar_estado(pid, flujo="cancelar", paso="confirmar", cita_id=citas[0]["id"], reprogramar=reprogramar,
                        intentos=0)
        _decir_flujo(salida, persona, f"¿Cancelo tu cita de {_cita_texto(citas[0])}"
                     + (" para buscar otro horario" if reprogramar else "") + "? Responde *SÍ* o *NO*.")
        return
    _guardar_estado(pid, flujo="cancelar", paso="elegir", opciones=[c["id"] for c in citas], reprogramar=reprogramar,
                    intentos=0)
    _decir_flujo(salida, persona, "¿Cuál cita " + ("quieres cambiar" if reprogramar else "quieres cancelar") + "?\n\n"
                 + "\n".join(f"{i}. {_cita_texto(c)}" for i, c in enumerate(citas, 1)) + "\n\nResponde con el número.")


def _paso_cancelar(salida, persona, texto, t, est, neg):
    pid = persona["id"]
    if est.get("paso") == "elegir":
        if t.isdigit() and 1 <= int(t) <= len(est["opciones"]):
            cita_id = est["opciones"][int(t) - 1]
            _guardar_estado(pid, paso="confirmar", cita_id=cita_id, intentos=0)
            fila = next((c for c in db.citas_futuras_de(persona["telefono"], "0000") if c["id"] == cita_id), None)
            _decir_flujo(salida, persona, f"¿Cancelo tu cita de {_cita_texto(fila)}? Responde *SÍ* o *NO*." if fila
                         else "Esa cita ya no está disponible.")
        else:
            _reintentar(salida, persona, neg, "Responde con el número de la cita, por favor.")
        return
    if _es_si(t):
        fila = next((c for c in db.citas_futuras_de(persona["telefono"], "0000") if c["id"] == est["cita_id"]), None)
        reprogramar = est.get("reprogramar")
        _terminar_flujo(pid)
        if fila is None or not db.cancelar_cita(est["cita_id"], persona["telefono"]):
            _decir_flujo(salida, persona, "Esa cita ya no está disponible.")
            return
        _decir_flujo(salida, persona, "✅ Cancelé tu cita de " + _cita_texto(fila) + ".")
        _avisar_cita(salida, "❌ *Cita cancelada por el cliente*",
                     {"cliente_nombre": fila["cliente_nombre"], "telefono": fila["telefono"],
                      "servicio": fila["servicio"], "mascota": fila["mascota"], "fecha_hora": fila["fecha_hora"]})
        if reprogramar:
            _iniciar_cita(salida, persona, neg, fila["servicio"])
    elif _en(t, NO):
        _terminar_flujo(pid)
        _decir_flujo(salida, persona, "Perfecto, tu cita sigue en pie 👍")
    else:
        _reintentar(salida, persona, neg, "Responde *SÍ* para cancelar o *NO* para dejarla como está.")


# ── Entrada del cliente ───────────────────────────────────────────────────────

def flujo_cliente(salida, persona, texto):
    neg = negocio.config()
    t = norm(texto)
    pid = persona["id"]
    est = db.get_estado(pid)

    if est.get("flujo"):
        if _flujo_vencido(est):
            _terminar_flujo(pid)
        elif t in SALIR_SIEMPRE or (est["flujo"] == "cita" and _en(t, SALIR_EN_CITA)):
            _terminar_flujo(pid)
            _decir(salida, persona, "Listo, lo dejamos así.\n\n" + _menu_cliente(neg))
            return
        elif PERSONA_RE.search(t):
            _terminar_flujo(pid)
            _decir(salida, persona, "Claro, aviso al equipo para que una persona te atienda. Un momento, por favor.")
            _derivar(salida, persona, "hablar con una persona", texto)
            return
        else:
            {"nombre": _paso_nombre, "cita": _paso_cita, "cancelar": _paso_cancelar}[est["flujo"]](
                salida, persona, texto, t, est, neg)
            return

    # Primer contacto: se pregunta el nombre (salvo que ya pida algo concreto; entonces se pide al agendar)
    if persona["rol"] == "cliente" and not est.get("nombre_ok") and not _es_intencion_cliente(t):
        _preguntar_nombre_primer_contacto(salida, persona, neg, texto)
        return

    puede_agendar = bool(neg.get("agenda")) and bool(neg["servicios_detalle"])
    if REPROGRAMAR_RE.search(t) or CANCELAR_CITA_RE.search(t):
        if puede_agendar:
            _iniciar_cancelar(salida, persona, neg, reprogramar=bool(REPROGRAMAR_RE.search(t)))
        else:
            _decir(salida, persona, "Claro, aviso al equipo para que una persona te ayude con tu cita.")
            _derivar(salida, persona, "cancelar o cambiar una cita", texto)
        return
    if MIS_CITAS_RE.search(t) and puede_agendar:
        _mostrar_mis_citas(salida, persona)
        return
    if t in ("1", "servicios", "ver servicios") or SERVICIOS_RE.search(t):
        if neg["servicios"]:
            lineas = ["🐾 *Servicios:*", ""] + [f"• {x}" for x in neg["servicios"]]
            if neg["horario"]:
                lineas += ["", f"🕒 Horario: {neg['horario']}"]
            lineas += ["", "¿Quieres agendar una cita? Responde *2*, o *3* para hablar con una persona."]
            _decir(salida, persona, "\n".join(lineas))
        else:  # no hay servicios cargados: no se inventan
            _decir(salida, persona, "Con gusto te cuento sobre nuestros servicios: "
                   "una persona del equipo te escribirá en breve.")
            _derivar(salida, persona, "información de servicios", texto)
        return
    if t in ("2", "cita", "agendar") or CITA_CLIENTE_RE.search(t):
        if puede_agendar:
            _iniciar_cita(salida, persona, neg)
        else:  # sin agenda configurada Pati no inventa horarios
            _decir(salida, persona, "📅 *Agendar una cita*\n\nPronto te derivaremos con un asesor "
                   "que te ayudará con los detalles.")
            _derivar(salida, persona, "agendar una cita", texto)
        return
    if t == "3" or PERSONA_RE.search(t):
        _decir(salida, persona, "Claro, aviso al equipo para que una persona te atienda. Un momento, por favor.")
        _derivar(salida, persona, "hablar con una persona", texto)
        return
    _decir(salida, persona, _menu_cliente(neg))


def registrar_desconocido(telefono, lid, nombre):
    """Alguien nuevo escribe al número del negocio: se registra como cliente salvo que esté apagado/ignorado."""
    neg = negocio.config()
    if not neg["atender_desconocidos"]:
        return None
    if telefono in neg["ignorar"] or (lid and lid in neg["ignorar"]):
        return None
    return db.crear_cliente(telefono, lid, nombre)


# ── Pausa cuando escribe una persona del negocio ─────────────────────────────

def _ahora_iso():
    return ahora().replace(tzinfo=None).isoformat()


def pausar_por_humano(chat):
    """Una persona escribió en este chat: Pati se calla hasta N minutos después de ESE mensaje."""
    db.pausar_chat(chat, (ahora() + timedelta(minutes=PAUSA_HUMANO_MIN)).replace(tzinfo=None).isoformat())


def chat_pausado(chat):
    return db.chat_pausado(chat, _ahora_iso())


def mensaje_recordatorio_cita(cita):
    """Texto que recibe el cliente antes de su cita (solo datos guardados)."""
    neg = negocio.config()
    return (
        "🐾 *Recordatorio de cita*\n\n"
        f"{neg['Sujeto']}: {cita['mascota']}\n"
        f"Servicio: {cita['servicio']}\n"
        f"Fecha: {cita['fecha_hora']}\n\n"
        f"¡Te esperamos en {neg['nombre']}!"
    )


# ── Tope de respuestas por cliente ───────────────────────────────────────────

def _limite_alcanzado(salida, persona, texto):
    """True si el cliente ya recibió el máximo de respuestas en la última hora (ventana móvil).

    Al llegar al tope se avisa UNA sola vez y se deriva a una persona; después Pati calla
    hasta que las respuestas más viejas cumplan la hora y el contador baje.
    """
    pid = persona["id"]
    estado = db.get_estado(pid)
    if db.respuestas_recientes(pid, 60, _linea(salida)) < MAX_RESPUESTAS_CLIENTE_HORA:
        if estado.pop("limite_avisado", None):
            db.set_estado(pid, estado)  # ya puede volver a responderle
        return False
    print(f"[LÍMITE] {persona['nombre']} alcanzó {MAX_RESPUESTAS_CLIENTE_HORA} respuestas en 1 hora")
    if not estado.get("limite_avisado"):
        estado["limite_avisado"] = True
        db.set_estado(pid, estado)
        _decir(salida, persona, "Para atenderte mejor, una persona del equipo te escribirá en breve. 🙏")
        _derivar(salida, persona, "ser atendido (muchos mensajes seguidos)", texto)
    return True


# ── Entrada única ─────────────────────────────────────────────────────────────

TEXTO_PROSPECTO = (
    "Hola 👋 Soy Pati, la asistente de WhatsApp de Paticare para negocios pequeños. "
    "Este número es de uso interno de los negocios que ya usan Pati. "
    "Si quieres conocerla, cuéntanos y lo revisaremos."
)
_prospectos = {}  # número -> última vez que se le respondió (solo en memoria)


def atender_prospecto(salida, ident):
    """Un número no registrado escribe a la línea central: una sola respuesta informativa por hora."""
    ahora_s = time.time()
    if ahora_s - _prospectos.get(ident, 0) < 3600:
        return
    _prospectos[ident] = ahora_s
    salida.responder(TEXTO_PROSPECTO)


def _texto_numero_interno(neg):
    destino = neg["lineas"].get("corporativa")
    donde = f"al número del negocio (+{destino})" if destino else "al número del negocio"
    return (f"Hola 👋 Este es el número interno del equipo de {neg['nombre']}. "
            f"Para consultas o citas escríbenos {donde}.")


def procesar(salida, persona, texto):
    """Atiende un mensaje de una persona registrada. Lo que hace depende de la LÍNEA por la que llegó.

    - Línea corporativa: todos son clientes (el equipo puede hacerse pasar por cliente para probar).
    - Línea central: según el rol (dueño, trabajador). Un cliente recibe un aviso de número interno.
    """
    if _linea(salida) == "corporativa":
        # No se guarda lo que escriben los clientes (solo se cuenta lo que Pati responde)
        if _limite_alcanzado(salida, persona, texto):
            return
        flujo_cliente(salida, persona, texto)
        return
    if persona["rol"] == "cliente":
        if db.respuestas_recientes(persona["id"], 60, "central") < 3:  # sin alertas: solo evita insistir
            _decir(salida, persona, _texto_numero_interno(negocio.config()))
        return
    db.log_mensaje(persona["id"], "usuario", texto, "central")
    if persona["rol"] == "maestro":
        flujo_dueno(salida, persona, texto)
    elif persona["rol"] == "trabajador":
        flujo_trabajador(salida, persona, texto)


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
    ahora_iso = _hoy_naive().strftime("%Y-%m-%d %H:%M")
    atrasadas = [f for f in db.estado_tareas() if f["estado"] == "pendiente" and f["vence_en"] and f["vence_en"] < ahora_iso]
    if atrasadas:
        lineas.append("🔴 *Atrasadas:*")
        lineas += [f"  • {f['nombre']}: {f['actividad']} ({texto_plazo(f['vence_en'])})" for f in atrasadas]
        lineas.append("")
    if atencion:
        lineas.append("🔴 *Requiere tu atención:*")
        lineas += [f"  • {n} reportó una incidencia" for n in atencion]
    elif not atrasadas:
        lineas.append("✅ Todo en orden, sin incidencias.")
    return "\n".join(lineas)
