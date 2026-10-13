"""PatiCare - llamada a Gemini (REST directo, sin SDK)."""
import json
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("GEMINI_API_KEY")
MODELO = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
TIMEOUT = float(os.getenv("GEMINI_TIMEOUT", "30"))
URL = f"https://generativelanguage.googleapis.com/v1/models/{MODELO}:generateContent"


# ── Errores de Gemini ─────────────────────────────────────────────────────────

MENSAJES_USUARIO = {
    "cuota": "Hoy ya usé todo mi cupo de inteligencia artificial 😅. Inténtalo más tarde o avisa al equipo técnico.",
    "transitorio": "El servicio de IA está saturado en este momento. Inténtalo de nuevo en un minuto.",
    "config": "Tengo un problema de configuración con la IA. Avisa al equipo técnico.",
    "respuesta": "No logré entender bien ese mensaje. ¿Puedes escribirlo de otra forma?",
}


class GeminiError(Exception):
    """kind: cuota | transitorio | config | respuesta. Nunca contiene la API key."""

    def __init__(self, kind, detalle):
        super().__init__(f"[{kind}] {detalle}")
        self.kind = kind
        self.mensaje_usuario = MENSAJES_USUARIO[kind]


def _log(texto):
    print(f"[GEMINI] {texto.replace(API_KEY, '***') if API_KEY else texto}")


def _llamar(sistema, contexto, temperatura):
    """Hace la llamada HTTP, maneja errores/reintentos y devuelve el dict JSON del modelo."""
    if not API_KEY:
        raise GeminiError("config", "falta GEMINI_API_KEY en el archivo .env")
    cuerpo = {
        "systemInstruction": {"parts": [{"text": sistema}]},
        "contents": [{"role": "user", "parts": [{"text": contexto}]}],
        "generationConfig": {"temperature": temperatura, "responseMimeType": "application/json"},
    }
    r = None
    for intento in (1, 2):  # un solo reintento
        try:
            r = httpx.post(URL, headers={"x-goog-api-key": API_KEY}, json=cuerpo, timeout=TIMEOUT)
        except httpx.HTTPError as e:
            _log(f"{MODELO} intento {intento}: error de red {type(e).__name__}")
            if intento == 2:
                raise GeminiError("transitorio", f"red/timeout: {type(e).__name__}")
            time.sleep(2)
            continue

        if r.status_code == 200:
            break
        _log(f"{MODELO} intento {intento}: HTTP {r.status_code} {r.text[:200]!r}")

        if r.status_code == 429:
            # Cuota: solo se reintenta si Google pide una espera corta
            espera = r.headers.get("Retry-After", "")
            if intento == 1 and espera.isdigit() and int(espera) <= 10:
                time.sleep(int(espera))
                continue
            raise GeminiError("cuota", "HTTP 429")
        if r.status_code >= 500:
            if intento == 2:
                raise GeminiError("transitorio", f"HTTP {r.status_code}")
            time.sleep(2)
            continue
        # 400 / 401 / 403 / 404: configuración, no se reintenta
        raise GeminiError("config", f"HTTP {r.status_code} (modelo {MODELO})")

    try:
        data = r.json()
        partes = data["candidates"][0]["content"]["parts"]
        texto = "".join(p.get("text", "") for p in partes)
        resultado = json.loads(texto)
    except (ValueError, KeyError, IndexError, TypeError) as e:
        _log(f"{MODELO}: respuesta inválida ({type(e).__name__})")
        raise GeminiError("respuesta", f"respuesta vacía o JSON inválido: {type(e).__name__}")
    if not isinstance(resultado, dict):
        raise GeminiError("respuesta", "el JSON no es un objeto")
    return resultado


DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def _ahora():
    n = datetime.now(ZoneInfo("America/Lima"))
    return f"{DIAS[n.weekday()]} {n.strftime('%d/%m/%Y %H:%M')}"


def _fecha_valida(valor):
    """Devuelve 'YYYY-MM-DD HH:MM' si el formato es correcto; si no, None (para preguntar)."""
    try:
        datetime.strptime(str(valor), "%Y-%m-%d %H:%M")
        return str(valor)
    except ValueError:
        return None


# ── Interpretación de los mensajes del dueño ─────────────────────────────────

SISTEMA_DUENO = """Eres Pati, la asistente de una veterinaria pequeña en Perú. El dueño te escribe por WhatsApp
de forma desordenada: jerga peruana, palabras pegadas, faltas de ortografía, frases a medias.
Tu trabajo es entender qué quiere y proponer una acción. Tú NO ejecutas nada: el sistema pide la confirmación.

Responde SOLO con este JSON:
{"accion":"nueva|corregir|ninguna",
 "tipo":"tareas|recordatorio|cita|ninguno",
 "tareas":[{"responsable":"nombre o null","actividad":"texto corto","detalles":"texto o null","horario":"texto o null"}],
 "recordatorio":{"cliente":"nombre o null","telefono":"solo dígitos con código de país o null","mensaje_a_enviar":"texto o null","fecha_hora":"YYYY-MM-DD HH:MM o null"},
 "cita":{"cliente":"nombre o null","telefono":"solo dígitos con código de país o null","mascota":"nombre o null","servicio":"texto o null","fecha_hora":"YYYY-MM-DD HH:MM o null"},
 "pregunta":"una sola pregunta corta o null",
 "respuesta":"texto corto o null"}
Llena solo la clave que corresponde al "tipo"; las otras van vacías o null.

Reglas:
1. "accion":
   - "corregir": hay una PROPUESTA PENDIENTE y el mensaje la corrige, la completa o responde a su pregunta (aunque sea muy corto: "todos ahora", "a las 5", "no, que lo haga Mafer"). Devuelve la propuesta COMPLETA y actualizada del mismo tipo (en tareas, TODAS las tareas, no solo la cambiada).
   - "nueva": pide algo distinto a la propuesta pendiente, o no hay propuesta pendiente y pide tareas, un recordatorio o una cita.
   - "ninguna": saludo, pregunta general, agradecimiento o algo que no pide acción. Pon en "respuesta" una frase amable y corta. No inventes datos del negocio (precios, horarios, servicios).
2. Usa el historial y la propuesta pendiente para entender referencias como "eso", "ella", "el resto", "todos", "lo mismo". Si no se puede resolver, deja el dato en null y haz UNA pregunta corta. No adivines.
3. NUNCA inventes horas, nombres de mascota, direcciones, teléfonos ni instrucciones que el dueño no dio. Lo que no se dijo va en null.
4. Tareas: una por actividad y por responsable (si dice "todos que lo hagan ahora", crea una tarea por cada trabajador de la lista). "responsable" debe ser EXACTAMENTE un nombre de la lista de trabajadores; si no está claro, null. "el resto" = los trabajadores de la lista que no se mencionaron en esa instrucción.
5. Horarios en tareas: SOLO si el dueño dijo cuándo. Si no mencionó ningún momento, "horario" es null (nunca pongas "Ahora" por defecto). Si dijo "ahora", "ya" o "de una" -> "Ahora". Tiempos relativos ("en 2 minutos") -> calcula la hora de reloj con la fecha y hora actual, formato "HH:MM".
6. En "detalles" solo lo que el dueño dijo (dirección, indicaciones). No agregues pasos.
7. Elegir entre recordatorio y cita: "avísale / recuérdale / dile a un cliente que ..." (algo ya acordado) = "recordatorio". "agenda / reserva / nueva cita" = "cita". "servicio" y "mascota" solo si el dueño los dijo; nunca pongas "cita" como servicio. Recordatorio/cita: la fecha_hora siempre en formato YYYY-MM-DD HH:MM resolviendo "mañana", "el viernes", etc. con la fecha actual. "mensaje_a_enviar" es un texto amable, corto, en español, listo para el cliente, usando solo datos dados. Si el cliente está en la lista de clientes registrados, usa ese nombre.
8. "pregunta": solo por lo indispensable (quién hace la tarea, o la fecha/hora, el cliente o su teléfono en recordatorios y citas; mascota y servicio en citas). No preguntes por detalles menores. Junta varias dudas en una sola pregunta."""


_CAMPOS = {
    "recordatorio": ("cliente", "telefono", "mensaje_a_enviar", "fecha_hora"),
    "cita": ("cliente", "telefono", "mascota", "servicio", "fecha_hora"),
}


def _limpiar(valor):
    """Texto no vacío o None (el modelo a veces devuelve '' o 'null')."""
    if isinstance(valor, (int, float)) and not isinstance(valor, bool):
        valor = str(valor)
    if not isinstance(valor, str):
        return None
    valor = valor.strip()
    return None if valor.lower() in ("", "null", "none") else valor


def interpretar_dueno(mensaje, trabajadores, clientes, historial, propuesta):
    """Interpreta el mensaje del dueño con su contexto.

    Devuelve {"accion", "tipo", "datos", "pregunta", "respuesta"}; "datos" es el contenido ya
    limpio de la propuesta (dict con "tareas", o los campos del recordatorio/cita), o None.
    """
    partes = [
        f"Fecha y hora actual en Lima: {_ahora()}",
        f"Trabajadores: {', '.join(trabajadores) or '(ninguno)'}",
        f"Clientes registrados: {', '.join(clientes) or '(ninguno)'}",
    ]
    if historial:
        lineas = [f"{'Dueño' if h['rol'] == 'usuario' else 'Pati'}: {h['texto']}" for h in historial]
        partes.append("Conversación reciente:\n" + "\n".join(lineas))
    if propuesta:
        partes.append(
            "PROPUESTA PENDIENTE: "
            + json.dumps({"tipo": propuesta["tipo"], **propuesta["datos"]}, ensure_ascii=False)
        )
    else:
        partes.append("PROPUESTA PENDIENTE: ninguna")
    partes.append(f"Mensaje actual del dueño: {mensaje}")

    r = _llamar(SISTEMA_DUENO, "\n".join(partes), 0.2)

    accion, tipo = r.get("accion"), r.get("tipo")
    if accion not in ("nueva", "corregir", "ninguna") or tipo not in ("tareas", "recordatorio", "cita", "ninguno"):
        raise GeminiError("respuesta", f"accion/tipo inválidos: {accion!r}/{tipo!r}")

    datos = None
    if tipo == "tareas":
        tareas = r.get("tareas")
        if not isinstance(tareas, list):
            raise GeminiError("respuesta", "falta la lista 'tareas'")
        limpias = []
        for t in tareas:
            if not isinstance(t, dict) or not _limpiar(t.get("actividad")):
                continue  # sin actividad no es una tarea
            limpias.append({
                "responsable": _limpiar(t.get("responsable")),
                "actividad": _limpiar(t["actividad"]),
                "detalles": _limpiar(t.get("detalles")),
                "horario": _limpiar(t.get("horario")),
            })
        datos = {"tareas": limpias} if limpias else None
    elif tipo in _CAMPOS:
        d = r.get(tipo)
        if not isinstance(d, dict):
            raise GeminiError("respuesta", f"falta el objeto '{tipo}'")
        datos = {k: _limpiar(d.get(k)) for k in _CAMPOS[tipo]}
        datos["fecha_hora"] = _fecha_valida(datos["fecha_hora"])

    if datos is None:  # nada accionable
        accion, tipo = "ninguna", "ninguno"
    return {
        "accion": accion,
        "tipo": tipo,
        "datos": datos,
        "pregunta": _limpiar(r.get("pregunta")),
        "respuesta": _limpiar(r.get("respuesta")),
    }
