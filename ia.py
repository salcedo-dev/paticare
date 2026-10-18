"""PatiCare - llamada a Gemini (REST directo, sin SDK)."""
import json
import os
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("GEMINI_API_KEY")
TIMEOUT = float(os.getenv("GEMINI_TIMEOUT", "30"))


def _lista_modelos():
    """Modelos en orden de uso. Si uno se queda sin cupo o falla, se pasa al siguiente.

    GEMINI_MODELS="a,b,c" manda; si no existe se usan los dos Flash-Lite (cupo diario separado por modelo).
    El antiguo GEMINI_MODEL se agrega al final como último recurso.
    """
    base = os.getenv("GEMINI_MODELS") or "gemini-3.1-flash-lite,gemini-3.5-flash-lite"
    lista = [m.strip() for m in base.split(",") if m.strip()]
    viejo = (os.getenv("GEMINI_MODEL") or "").strip()
    if viejo and viejo not in lista:
        lista.append(viejo)
    return lista


MODELOS = _lista_modelos()


def _url(modelo):
    return f"https://generativelanguage.googleapis.com/v1/models/{modelo}:generateContent"


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


# ── Contador de uso diario (el cupo es por modelo y por clave, compartido por todos los negocios) ──

USO_ARCHIVO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uso_gemini.json")  # no se sube a git
LIMITE_RPD = {"gemini-3.1-flash-lite": 500, "gemini-3.5-flash-lite": 500}  # plan gratis visto en AI Studio
LIMITE_RPD_OTROS = 20
_uso_lock = threading.Lock()


def _dia_de_cupo():
    """El cupo diario se reinicia a medianoche de California (por verificar con AI Studio)."""
    return datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d")


def _leer_uso():
    try:
        with open(USO_ARCHIVO, encoding="utf-8") as f:
            datos = json.load(f)
        if datos.get("dia") == _dia_de_cupo() and isinstance(datos.get("modelos"), dict):
            return datos
    except (OSError, ValueError, AttributeError):
        pass
    return {"dia": _dia_de_cupo(), "modelos": {}}


def _registrar_uso(modelo):
    """Cuenta cada petición enviada a Gemini (aunque falle: también gasta cupo). Nunca rompe el bot."""
    try:
        with _uso_lock:
            datos = _leer_uso()
            n = datos["modelos"].get(modelo, 0) + 1
            datos["modelos"][modelo] = n
            with open(USO_ARCHIVO, "w", encoding="utf-8") as f:
                json.dump(datos, f)
        limite = LIMITE_RPD.get(modelo, LIMITE_RPD_OTROS)
        if n >= limite * 0.8 and (n == int(limite * 0.8) or n % 10 == 0 or n >= limite):
            _log(f"AVISO: {modelo} lleva {n}/{limite} peticiones hoy")
    except Exception as e:  # el contador es solo informativo
        print(f"[AVISO uso] no pude registrar el uso: {type(e).__name__}")


def resumen_uso():
    """Texto con las peticiones de hoy por modelo frente a su límite diario."""
    datos = _leer_uso()
    lineas = [f"Uso de Gemini hoy (día de cupo {datos['dia']}, hora de California):"]
    for modelo in dict.fromkeys(list(MODELOS) + list(datos["modelos"])):
        n = datos["modelos"].get(modelo, 0)
        limite = LIMITE_RPD.get(modelo, LIMITE_RPD_OTROS)
        lineas.append(f"  {modelo}: {n}/{limite} ({100 * n // limite}%)")
    return "\n".join(lineas)


def _llamar(sistema, contexto, temperatura):
    """Prueba cada modelo de MODELOS hasta que uno responda; si todos fallan, lanza el último error."""
    ultimo = None
    for i, modelo in enumerate(MODELOS):
        try:
            return _llamar_modelo(modelo, sistema, contexto, temperatura)
        except GeminiError as e:
            ultimo = e
            if e.kind == "config" and "falta GEMINI_API_KEY" in str(e):
                raise  # sin clave ningún modelo va a funcionar
            if i + 1 < len(MODELOS):
                _log(f"{modelo} falló ({e.kind}); pruebo {MODELOS[i + 1]}")
    raise ultimo


def _llamar_modelo(MODELO, sistema, contexto, temperatura):
    """Una llamada HTTP a UN modelo, con un reintento ante fallos transitorios."""
    if not API_KEY:
        raise GeminiError("config", "falta GEMINI_API_KEY en el archivo .env")
    cuerpo = {
        "systemInstruction": {"parts": [{"text": sistema}]},
        "contents": [{"role": "user", "parts": [{"text": contexto}]}],
        "generationConfig": {"temperature": temperatura, "responseMimeType": "application/json"},
    }
    r = None
    for intento in (1, 2):  # un solo reintento
        _registrar_uso(MODELO)
        try:
            r = httpx.post(_url(MODELO), headers={"x-goog-api-key": API_KEY}, json=cuerpo, timeout=TIMEOUT)
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

SISTEMA_DUENO = """Eres Pati, la asistente de {rubro_texto} en Perú. El dueño te escribe por WhatsApp
de forma desordenada: jerga peruana, palabras pegadas, faltas de ortografía, frases a medias.
Tu trabajo es entender qué quiere y proponer una acción. Tú NO ejecutas nada: el sistema pide la confirmación.

Responde SOLO con este JSON:
{"accion":"nueva|corregir|consulta|ninguna",
 "tipo":"tareas|recordatorio|cita|ninguno",
 "tareas":[{"responsable":"nombre o null","actividad":"texto corto","detalles":"texto o null","horario":"texto o null","vence":"YYYY-MM-DD HH:MM o null"}],
 "recordatorio":{"cliente":"nombre o null","telefono":"solo dígitos con código de país o null","mensaje_a_enviar":"texto o null","fecha_hora":"YYYY-MM-DD HH:MM o null"},
 "cita":{"cliente":"nombre o null","telefono":"solo dígitos con código de país o null","mascota":"nombre {del_sujeto} o null","servicio":"texto o null","fecha_hora":"YYYY-MM-DD HH:MM o null"},
 "consulta":{"persona":"nombre exacto de la lista de trabajadores o null"},
 "pregunta":"una sola pregunta corta o null",
 "respuesta":"texto corto o null"}
Llena solo la clave que corresponde al "tipo"; las otras van vacías o null.

Reglas:
1. "accion":
   - "corregir": hay una PROPUESTA PENDIENTE y el mensaje la corrige, la completa o responde a su pregunta (aunque sea muy corto: "todos ahora", "a las 5", "no, que lo haga Mafer"). Devuelve la propuesta COMPLETA y actualizada del mismo tipo (en tareas, TODAS las tareas, no solo la cambiada).
   - "nueva": pide algo distinto a la propuesta pendiente, o no hay propuesta pendiente y pide tareas, un recordatorio o una cita.
   - "consulta": pregunta por el ESTADO o avance de las tareas del equipo ("¿quiénes ya terminaron?", "¿cómo va Mafer?", "haz el reporte", "dame un resumen"). Pon tipo "ninguno" y, en "consulta.persona", el nombre exacto si pregunta por alguien en particular, o null si es por todos. NUNCA inventes el estado, ni ofrezcas "consultar" a los trabajadores, ni crees una tarea para esto: el sistema responde con los datos guardados.
   - "ninguna": saludo, pregunta general, agradecimiento o algo que no pide acción. Pon en "respuesta" una frase amable y corta. No inventes datos del negocio (precios, horarios, servicios).
2. Usa el historial y la propuesta pendiente para entender referencias como "eso", "ella", "el resto", "todos", "lo mismo". Si no se puede resolver, deja el dato en null y haz UNA pregunta corta. No adivines.
3. NUNCA inventes horas, nombres de {sujeto}, direcciones, teléfonos ni instrucciones que el dueño no dio. Lo que no se dijo va en null.
4. Tareas: una por actividad y por responsable (si dice "todos que lo hagan ahora", crea una tarea por cada trabajador de la lista). "responsable" debe ser EXACTAMENTE un nombre de la lista de trabajadores; si no está claro, null. "el resto" = los trabajadores de la lista que no se mencionaron en esa instrucción.
5. Horarios en tareas: SOLO si el dueño dijo cuándo. Si no mencionó ningún momento, "horario" es null (nunca pongas "Ahora" por defecto). Si dijo "ahora", "ya" o "de una" -> "Ahora". Tiempos relativos ("en 2 minutos") -> calcula la hora de reloj con la fecha y hora actual, formato "HH:MM".
6. En "detalles" solo lo que el dueño dijo (dirección, indicaciones). No agregues pasos.
7. Elegir entre recordatorio y cita: "avísale / recuérdale / dile a un cliente que ..." (algo ya acordado) = "recordatorio". "agenda / reserva / nueva cita" = "cita". "servicio" y "mascota" ({el_sujeto}) solo si el dueño los dijo; nunca pongas "cita" como servicio. Recordatorio/cita: la fecha_hora siempre en formato YYYY-MM-DD HH:MM resolviendo "mañana", "el viernes", etc. con la fecha actual. "mensaje_a_enviar" es un texto amable, corto, en español, listo para el cliente, usando solo datos dados. Si el cliente está en la lista de clientes registrados, usa ese nombre.
8. "pregunta": solo por lo indispensable (quién hace la tarea, o la fecha/hora, el cliente o su teléfono en recordatorios y citas; {sujeto} y servicio en citas). No preguntes por detalles menores. Junta varias dudas en una sola pregunta.
11. "vence" es el PLAZO de la tarea en formato YYYY-MM-DD HH:MM (hora de Lima), calculado con la fecha y hora actual: "para las 5" o "a las 5" = hoy a las 17:00 (si ya pasó hoy, mañana), "mañana antes de las 10" = mañana 10:00, "en 2 minutos" = ahora + 2 minutos. Pon "vence" SOLO si el dueño dio una hora o un plazo. Si dijo un rango ("de 8 a 12") pon la hora final. Si dijo "ahora" o no dio ninguna hora, null. NUNCA inventes una hora. "horario" sigue siendo el texto tal como lo dijo.
10. TAREA o CITA: si el mensaje nombra a alguien de la lista de trabajadores como quien hace algo ("mafer baña a firulais", "ashly recoge al perro") es una TAREA para esa persona, aunque mencione una mascota o un paciente. Solo es "cita" cuando se pide agendar o reservar un horario PARA UN CLIENTE ("agenda cita para pedro", "reserva a luna el viernes"), y el responsable no es de la lista de trabajadores.
9. Si el dueño dice "yo", "a mí" o "me lo asigno", el responsable es el nombre que aparece en "Dueño que escribe", pero SOLO si ese nombre está en la lista de trabajadores; si no está, deja el responsable en null y pregunta."""


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


def _prompt_dueno(neg):
    """Adapta el prompt al rubro (veterinaria, dentista...). Sin perfil usa veterinaria."""
    if neg is None:
        import negocio  # import tardío: ia.py no depende del negocio salvo aquí
        neg = negocio.PERFILES["veterinaria"]
    return (SISTEMA_DUENO.replace("{rubro_texto}", neg["rubro_texto"])
            .replace("{del_sujeto}", neg["del_sujeto"]).replace("{el_sujeto}", neg["el_sujeto"]).replace("{sujeto}", neg["sujeto"]))


def interpretar_dueno(mensaje, trabajadores, clientes, historial, propuesta, negocio=None, dueno=None):
    """Interpreta el mensaje del dueño con su contexto.

    Devuelve {"accion", "tipo", "datos", "pregunta", "respuesta"}; "datos" es el contenido ya
    limpio de la propuesta (dict con "tareas", o los campos del recordatorio/cita), o None.
    """
    partes = [
        f"Fecha y hora actual en Lima: {_ahora()}",
        f"Trabajadores: {', '.join(trabajadores) or '(ninguno)'}",
        f"Clientes registrados: {', '.join(clientes) or '(ninguno)'}",
    ]
    if dueno:
        partes.append(f"Dueño que escribe: {dueno}")
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

    r = _llamar(_prompt_dueno(negocio), "\n".join(partes), 0.2)

    accion, tipo = r.get("accion"), r.get("tipo")
    if accion not in ("nueva", "corregir", "consulta", "ninguna") or tipo not in ("tareas", "recordatorio", "cita", "ninguno"):
        raise GeminiError("respuesta", f"accion/tipo inválidos: {accion!r}/{tipo!r}")

    if accion == "consulta":  # pregunta por el estado de las tareas: no hay propuesta que guardar
        c = r.get("consulta")
        persona = _limpiar(c.get("persona")) if isinstance(c, dict) else None
        return {"accion": "consulta", "tipo": "ninguno", "datos": {"persona": persona},
                "pregunta": None, "respuesta": None}

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
                "vence": _fecha_valida(_limpiar(t.get("vence"))),  # solo si tiene el formato correcto
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


if __name__ == "__main__":
    print(resumen_uso())
