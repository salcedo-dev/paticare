"""Comandos del dueño: mensajes que EMPIEZAN con un punto (".encuesta ...", ".estado", ".recordar"...).

Reglas fijas, sin IA: un comando siempre hace lo mismo, así no hay malentendidos. Solo es comando si el punto va al
principio del mensaje; cualquier otro texto se sigue entendiendo como siempre (con IA). Todo lo que avisa a otras
personas sigue pidiendo el SÍ del dueño.

Los comandos reutilizan la lógica que ya existe en logica.py (control, encuestas, reportes); aquí solo se lee la forma
del comando y se la pasa a esa lógica.
"""
import difflib
import re
from datetime import timedelta

import agenda
import db
import logica
from logica import _decir, norm

COMANDO_RE = re.compile(r"^\s*\.\s*([^\W\d_][\w-]*)\s*(.*)$", re.S)  # el punto debe ir al inicio y pegado a una palabra

# nombre canónico -> variantes que también se aceptan (sin tildes ni guiones)
ALIAS = {
    "ayuda": ("ayuda", "comandos", "help", "menu"),
    "equipo": ("equipo", "personas", "roles", "miequipo"),
    "tarea": ("tarea", "tareas", "asignar", "asigna", "encargo"),
    "estado": ("estado", "reporte", "avance"),
    "encuesta": ("encuesta", "pregunta", "preguntar"),
    "resultados": ("resultados", "resultado", "votos"),
    "checkin": ("checkin",),
    "recordar": ("recordar", "recuerda", "recordatorio"),
    "resolver": ("resolver", "resuelto", "resueltas", "incidencias"),
    "cancelar": ("cancelar", "cancela", "anular"),
    "reasignar": ("reasignar", "pasar"),
    "plazo": ("plazo", "hora"),
    "aviso": ("aviso", "avisar", "avisa", "anuncio"),
    "historial": ("historial",),
    "agenda": ("agenda", "citas"),
    "deshacer": ("deshacer", "deshaz"),
}
NOMBRE_DE = {v: k for k, vs in ALIAS.items() for v in vs}
SOLO_LECTURA = {"ayuda", "equipo", "estado", "resultados", "historial", "agenda"}  # se pueden usar con una propuesta pendiente

AYUDA = (
    "🧭 *Comandos* (empiezan con punto):\n"
    "*.tarea* mafer y ashly: bañar a firulais para las 5\n"
    "*.equipo* · quiénes son y qué rol tienen\n"
    "*.estado* [persona] · cómo van las tareas\n"
    "*.encuesta* ¿Quién cubre el sábado? opciones: yo, no puedo\n"
    "*.resultados* · cómo va la última encuesta\n"
    "*.checkin* · \"¿Cómo va tu día?\" al equipo\n"
    "*.recordar* [personas] · les recuerdo sus pendientes\n"
    "*.resolver* [personas] · incidencias resueltas\n"
    "*.cancelar* · te mando una lista para marcar qué cancelar\n"
    "*.cancelar* .mafer · la lista, solo con las tareas de ella\n"
    "*.cancelar* probar los botones · esa tarea, para todos\n"
    "*.cancelar* todas · todas las pendientes\n"
    "*.reasignar* mafer a ashly\n"
    "*.plazo* mafer 6pm\n"
    "*.aviso* mañana abrimos tarde\n"
    "*.historial* mafer\n"
    "*.agenda* [hoy | mañana | semana]\n"
    "*.deshacer* · cancela lo último que envié\n\n"
    "Lo que avisa a otros siempre pide tu *SÍ*. También puedes escribirme normal 🙂"
)
FORMATO_TAREA = "Escríbelo así: *.tarea mafer y ashly: bañar a firulais para las 5* (las personas, dos puntos y lo que deben hacer)."

# ── plazo dentro de la tarea ("para las 5", "en 30 minutos", "mañana a las 10") ──
_HORA = r"\d{1,2}(?::\d{2})?\s*(?:a\.?\s?m\.?|p\.?\s?m\.?)?"
PLAZOS_RE = [
    re.compile(rf"\bmanana(?:\s+(?:para|a|antes de)\s+las?\s+{_HORA})?", re.I),
    re.compile(r"\ben\s+(?:\d+|una|un|media)\s*(?:minutos?|mins?|horas?|hrs?|h)\b", re.I),
    re.compile(rf"\b(?:para|antes de|hasta|a)\s+las?\s+{_HORA}", re.I),
]


def extraer_plazo(texto, ahora_n):
    """"bañar a firulais para las 5" -> ("bañar a firulais", datetime de las 5). Sin plazo: (texto, None)."""
    sin = logica._sin_tildes(texto)  # mismo largo que el original: sirve para cortar por posición
    for patron in PLAZOS_RE:
        m = patron.search(sin)
        if m:
            dt = logica._parse_plazo(texto[m.start():m.end()], ahora_n)
            if dt is not None:
                actividad = re.sub(r"\s+", " ", (texto[:m.start()] + " " + texto[m.end():])).strip(" ,.;:-")
                return actividad, dt
    return texto.strip(), None


def _personas(izquierda):
    """Las personas del equipo nombradas ("mafer y ashly", "todos"). Devuelve (nombres reales, palabras que no entendí)."""
    equipo = {norm(w["nombre"]): w["nombre"] for w in logica._nombres_encuestables()}
    elegidas, raras = [], []
    for palabra in norm(izquierda).split():
        if palabra in ("y", "e", "a", "para", "de", "con"):
            continue
        if palabra in ("todos", "todas", "equipo"):
            elegidas += [n for n in equipo.values() if n not in elegidas]
        elif palabra in equipo:
            if equipo[palabra] not in elegidas:
                elegidas.append(equipo[palabra])
        else:
            raras.append(palabra)
    return elegidas, raras


# ── comandos ─────────────────────────────────────────────────────────────────

def _cmd_ayuda(salida, dueno, args, prop):
    _decir(salida, dueno, AYUDA)


def _cmd_equipo(salida, dueno, args, prop):
    """Las personas registradas y su rol (datos reales de la base)."""
    with db.conn() as c:
        filas = c.execute("SELECT nombre, rol, extras FROM personas WHERE rol IN ('maestro','trabajador') ORDER BY id").fetchall()
    lineas = ["👥 *Tu equipo*"]
    for f in filas:
        extras = [x for x in (f["extras"] or "").split(",") if x]
        if f["rol"] == "maestro":
            rol = "dueño" + (" · también recibe tareas" if "trabajador" in extras else "")
        else:
            rol = "trabajador" + (" · atiende a los clientes" if "atencion" in extras else "")
        lineas.append(f"• {f['nombre']} — {rol}")
    _decir(salida, dueno, "\n".join(lineas) if len(lineas) > 1 else "Todavía no hay personas registradas.")


def _cmd_tarea(salida, dueno, args, prop):
    if ":" not in args:
        _decir(salida, dueno, "Me falta separar las personas de la tarea. " + FORMATO_TAREA)
        return
    izquierda, derecha = args.split(":", 1)
    nombres, raras = _personas(izquierda)
    if raras or not nombres:
        quienes = ", ".join(raras) if raras else "nadie"
        _decir(salida, dueno, f"No encuentro a *{quienes}* en el equipo. " + FORMATO_TAREA)
        return
    actividad, plazo = extraer_plazo(derecha.strip(), logica._hoy_naive())
    if len(actividad) < 3:
        _decir(salida, dueno, "¿Qué deben hacer? " + FORMATO_TAREA)
        return
    vence = plazo.strftime("%Y-%m-%d %H:%M") if plazo else None
    datos = logica._normalizar("tareas", {"tareas": [
        {"responsable": n, "actividad": actividad[:1].upper() + actividad[1:], "detalles": None, "horario": None, "vence": vence}
        for n in nombres]})
    estado = db.get_estado(dueno["id"])
    estado["propuesta"] = logica._nueva_propuesta("tareas", datos)
    estado.pop("control", None)
    db.set_estado(dueno["id"], estado)
    _decir(salida, dueno, logica.texto_propuesta(estado["propuesta"]))


TODOS = ("todos", "todas", "todo", "equipo", "todo el equipo", "general", "el equipo")


def _cmd_estado(salida, dueno, args, prop):
    if norm(args) in TODOS:  # ".estado todos" / ".estado equipo": el reporte de todo el equipo
        args = ""
    nombre = logica._nombre_en_texto(norm(args)) if args else None
    if args and nombre is None:
        _decir(salida, dueno, f"No encuentro a *{args}* en el equipo. Ejemplo: *.estado mafer* o solo *.estado*.")
        return
    logica._responder_estado(salida, dueno, nombre, prop)


def _cmd_resultados(salida, dueno, args, prop):
    _decir(salida, dueno, logica.texto_resultados_ultima())


def _cmd_encuesta(salida, dueno, args, prop):
    frase = "encuesta " + args
    if not logica._encuestas_dueno(salida, dueno, frase, norm(frase), prop):
        _decir(salida, dueno, "No entendí la encuesta. " + logica.EJEMPLO_ENCUESTA)


def _cmd_checkin(salida, dueno, args, prop):
    logica._encuestas_dueno(salida, dueno, "haz el check-in", "haz el check in", prop)


def _via_texto(salida, dueno, frase, prop, ejemplo):
    """Pasa la frase (armada aquí, con palabras que la lógica ya entiende) a las órdenes de control del dueño."""
    if not logica._control_nuevo(salida, dueno, frase, norm(frase), bloqueado=bool(prop)):
        _decir(salida, dueno, f"No pude con ese comando. Ejemplo: *{ejemplo}*")


def _con_nombres(args):
    nombres, raras = _personas(args)
    return nombres, raras


def _cmd_recordar(salida, dueno, args, prop):
    nombres, raras = _con_nombres(args)
    if raras:
        _decir(salida, dueno, f"No encuentro a *{', '.join(raras)}* en el equipo. Ejemplo: *.recordar mafer ashly* o solo *.recordar*.")
        return
    frase = ("recuerdales a " + " y ".join(nombres) + " sus tareas pendientes") if nombres else "recuerdales sus tareas pendientes"
    _via_texto(salida, dueno, frase, prop, ".recordar mafer")


def _cmd_resolver(salida, dueno, args, prop):
    nombres, raras = _con_nombres(args)
    if raras:
        _decir(salida, dueno, f"No encuentro a *{', '.join(raras)}* en el equipo. Ejemplo: *.resolver mafer* o solo *.resolver*.")
        return
    frase = ("las incidencias de " + " y ".join(nombres) + " ya estan resueltas") if nombres else "las incidencias ya estan resueltas"
    _via_texto(salida, dueno, frase, prop, ".resolver mafer")


# Cómo se puede decir "a todos" antes del nombre de la tarea (de más largo a más corto)
GRUPOS = ("todo el equipo", "a todo el equipo", "el equipo", "a todos", "para todos", "de todos", "todos", "todas", "equipo")


def _cancelar_por_actividad(salida, dueno, palabras):
    """Cancela, para TODAS las personas, las tareas abiertas cuyo nombre incluye esas palabras (ej. "probar los botones")."""
    claves = [p[:5] for p in norm(palabras).split() if len(p) >= 4]
    abiertas = [f for f in db.tareas_abiertas_de_todos() if f["resp_rol"] != "maestro"]
    coinciden = [f for f in abiertas if claves and all(k in norm(f["actividad"]) for k in claves)]
    if not coinciden:
        _decir(salida, dueno, f"No encuentro tareas pendientes con *{palabras}*. Mira *.estado* para ver los nombres exactos.")
        return
    logica._proponer_control(salida, dueno, {"accion": "cancelar", "elegidas": True},
                             db.tareas_por_ids([f["id"] for f in coinciden]))


def _personas_con_punto(args):
    """".mafer .ashly" -> ["Mafer", "Ashly"]; ".equipo" / ".todos" -> [] (todo el equipo); no es de ese formato -> None;
    alguien que no existe -> "error". Acepta ". equipo" (con espacio)."""
    toks = re.sub(r"\.\s+", ".", args.strip()).split()
    if not toks or not all(t.startswith(".") and len(t) > 1 for t in toks):
        return None
    nombres, raras = _personas(" ".join(t[1:] for t in toks))
    if raras:
        return "error"
    return [] if any(norm(t[1:]) in ("equipo", "todos", "todas", "todo") for t in toks) else nombres


def _cmd_cancelar(salida, dueno, args, prop):
    resto = norm(args)
    personas = _personas_con_punto(args)  # ".cancelar .mafer" / ".cancelar .equipo": el selector, solo de esas personas
    if personas is not None:
        if personas == "error":
            _decir(salida, dueno, "No encuentro a esa persona. Ejemplo: *.cancelar .mafer* o *.cancelar .equipo*.")
            return
        logica.enviar_selector_cancelar(salida, dueno, personas or None)
        return
    if resto in ("todas", "todo", "todos", "todas las tareas"):
        _via_texto(salida, dueno, "cancela todas las tareas", prop, ".cancelar todas")
        return
    for grupo in GRUPOS:  # ".cancelar todo el equipo probar los botones": se quita el "todo el equipo"
        if resto.startswith(grupo + " ") and len(resto) > len(grupo) + 1:
            args = " ".join(args.split()[len(grupo.split()):])
            _cancelar_por_actividad(salida, dueno, args)
            return
    if args and not logica._nombres_en_orden(resto):
        _cancelar_por_actividad(salida, dueno, args)  # ".cancelar probar los botones" (sin decir de quién)
    elif args:
        _via_texto(salida, dueno, "cancela lo de " + args, prop, ".cancelar mafer")
    else:  # sin decir cuál: se manda la lista para que marque
        logica.enviar_selector_cancelar(salida, dueno)


def _cmd_reasignar(salida, dueno, args, prop):
    _via_texto(salida, dueno, "pasa la tarea de " + args, prop, ".reasignar mafer a ashly")


def _cmd_plazo(salida, dueno, args, prop):
    _via_texto(salida, dueno, "cambia el plazo de " + args, prop, ".plazo mafer 6pm")


def _cmd_aviso(salida, dueno, args, prop):
    if not args:
        _decir(salida, dueno, "¿Qué aviso les mando? Ejemplo: *.aviso mañana abrimos a las 8*.")
        return
    _via_texto(salida, dueno, "avisa al equipo que " + args, prop, ".aviso mañana abrimos a las 8")


def _cmd_historial(salida, dueno, args, prop):
    _via_texto(salida, dueno, "historial de " + args, prop, ".historial mafer")


def _cmd_agenda(salida, dueno, args, prop):
    logica._responder_agenda(salida, dueno, norm("citas " + (args or "hoy")), prop)


def _cmd_deshacer(salida, dueno, args, prop):
    _via_texto(salida, dueno, "deshacer", prop, ".deshacer")


HANDLERS = {
    "ayuda": _cmd_ayuda, "equipo": _cmd_equipo, "tarea": _cmd_tarea, "estado": _cmd_estado, "encuesta": _cmd_encuesta,
    "resultados": _cmd_resultados, "checkin": _cmd_checkin, "recordar": _cmd_recordar, "resolver": _cmd_resolver,
    "cancelar": _cmd_cancelar, "reasignar": _cmd_reasignar, "plazo": _cmd_plazo, "aviso": _cmd_aviso,
    "historial": _cmd_historial, "agenda": _cmd_agenda, "deshacer": _cmd_deshacer,
}


def ejecutar_comando(salida, dueno, texto):
    """Si el mensaje es un comando (empieza con punto) lo ejecuta y devuelve True; si no, False y sigue el flujo normal."""
    m = COMANDO_RE.match(texto)
    if not m:
        return False
    palabra = norm(m.group(1).replace("-", ""))
    args = m.group(2).strip()
    comando = NOMBRE_DE.get(palabra)
    if comando is None:
        parecido = difflib.get_close_matches(palabra, NOMBRE_DE.keys(), n=1, cutoff=0.6)
        sugerencia = f" ¿Quisiste decir *.{parecido[0]}*?" if parecido else ""
        _decir(salida, dueno, f"No conozco el comando *.{m.group(1)}*.{sugerencia} Escribe *.ayuda* para ver la lista.")
        return True
    estado = db.get_estado(dueno["id"])
    prop = estado.get("propuesta")
    if (prop or estado.get("alterna")) and comando not in SOLO_LECTURA:
        _decir(salida, dueno, f"Primero responde *SÍ* o *NO* a la propuesta que tengo pendiente, y luego usa *.{palabra}*.")
        return True
    try:
        HANDLERS[comando](salida, dueno, args, prop)
    except Exception as e:  # un comando mal escrito nunca debe tumbar a Pati
        print(f"[ERROR comando .{comando}] {type(e).__name__}: {e}")
        _decir(salida, dueno, f"No pude con ese comando. Escribe *.ayuda* para ver cómo se usa.")
    return True
