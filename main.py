"""PatiCare - Pati, el asistente de WhatsApp (conexión con neonize).

Un solo programa maneja dos líneas de WhatsApp:
  - línea CENTRAL: el número de Pati; aquí hablan el dueño y el equipo.
  - línea CORPORATIVA (opcional): el número del negocio; aquí escriben los clientes.
La lógica de conversación vive en logica.py. Aquí solo se conecta con WhatsApp, se envían los mensajes con
una pausa "humana" y corre el scheduler: resumen diario 19:00 Lima + recordatorios (c/min) + citas (c/30 min).

Reglas fijas: las líneas de Pati nunca se responden entre sí, y se ignoran los mensajes viejos.
"""
import hashlib
import json
import os
import random
import sqlite3
import threading
import time
from collections import OrderedDict, deque
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from neonize.client import NewClient
from neonize.events import ConnectedEv, MessageEv, PairStatusEv
from neonize.ext.interactive_message import ButtonMessage
from neonize.utils import build_jid
from neonize.utils.enum import VoteType
from neonize.utils.enum import ChatPresence, ChatPresenceMedia
from zoneinfo import ZoneInfo

import db
import logica
import negocio

db.init()
db.cargar_personas()  # lee personas.json; no borra clientes agregados a mano

LIMA = ZoneInfo("America/Lima")
DEBUG_MENSAJES = os.getenv("PATI_DEBUG") == "1"  # 1 = imprimir también el texto de los mensajes

# Sesiones de WhatsApp (no se suben a git). La central es la de siempre; la corporativa solo si el negocio la configura.
central = NewClient(os.path.join(db.DATA_DIR, "pati_sesion.sqlite3"))
corporativa = (NewClient(os.path.join(db.DATA_DIR, "sesion_corporativa.sqlite3"))
               if negocio.config()["lineas"].get("corporativa") else None)
LINEAS = {"central": central}
if corporativa is not None:
    LINEAS["corporativa"] = corporativa

# Archivo de sesión de cada línea: WhatsApp guarda ahí qué teléfono hay detrás de cada ID interno (LID)
_SESIONES = {"central": os.path.join(db.DATA_DIR, "pati_sesion.sqlite3"),
             "corporativa": os.path.join(db.DATA_DIR, "sesion_corporativa.sqlite3")}


def _telefono_de_lid(linea, lid):
    """El teléfono real detrás de un ID interno (LID), según la sesión de WhatsApp de esa línea. None si no lo sabe.

    Solo lee (modo lectura) la tabla que WhatsApp mantiene; así una persona registrada se reconoce aunque WhatsApp
    no mande su teléfono en el mensaje (sin tener que copiar su ID a mano).
    """
    ruta = _SESIONES.get(linea)
    if not ruta or not os.path.exists(ruta):
        return None
    try:
        con = sqlite3.connect(f"file:{ruta}?mode=ro", uri=True, timeout=2)
        try:
            fila = con.execute("SELECT pn FROM whatsmeow_lid_map WHERE lid=?", (lid,)).fetchone()
        finally:
            con.close()
        return fila[0] if fila else None
    except Exception as e:  # sin la tabla o con el archivo ocupado: simplemente no se sabe
        print(f"[LID] No pude consultar el mapa de IDs ({type(e).__name__})")
        return None


# ── Envío "humano" ────────────────────────────────────────────────────────────
# Pausa aleatoria + "escribiendo..." para no responder al instante como un bot.

_ids_pati = deque(maxlen=1000)  # IDs de los mensajes que envió Pati (para no confundirlos con los de una persona)


def _pausa_humana(cliente, jid, texto):
    try:
        cliente.send_chat_presence(
            jid, ChatPresence.CHAT_PRESENCE_COMPOSING, ChatPresenceMedia.CHAT_PRESENCE_MEDIA_TEXT
        )
    except Exception as e:
        print(f"[AVISO presencia] {type(e).__name__}: {e}")
    time.sleep(min(1.0 + len(texto) * 0.015 + random.uniform(0, 1.5), 4))


def _recordar(resp):
    _ids_pati.append(getattr(resp, "ID", None))
    return resp


_botones_enviados = OrderedDict()  # id del mensaje de Pati -> {etiqueta normalizada: id del botón} (respaldo por texto)


def _recordar_botones(resp, botones):
    _botones_enviados[getattr(resp, "ID", None)] = {logica.norm(etiqueta): bid for etiqueta, bid in botones}
    while len(_botones_enviados) > 500:
        _botones_enviados.popitem(last=False)


def _mensaje_botones(texto, botones):
    """Mensaje con botones de respuesta rápida (máximo 3). Cada id lleva el prefijo "pati|" para reconocerlo al volver."""
    m = ButtonMessage().set_body(texto)
    for etiqueta, bid in botones[:3]:
        m.add_reply(etiqueta, "pati|" + bid)
    return m


def responder(cliente, texto, msg, botones=None):
    """Responde citando el mensaje recibido, por la misma línea por la que llegó (con botones va como mensaje nuevo)."""
    if botones:
        return enviar(cliente, msg.Info.MessageSource.Chat, texto, botones)
    _pausa_humana(cliente, msg.Info.MessageSource.Chat, texto)
    return _recordar(cliente.reply_message(texto, msg))


def enviar(cliente, jid, texto, botones=None):
    """Envía un mensaje nuevo a un JID ya construido por la línea indicada. Si los botones fallan, va como texto."""
    _pausa_humana(cliente, jid, texto)
    if botones:
        try:
            resp = cliente.send_interactive_message(jid, _mensaje_botones(texto, botones))
            _recordar_botones(resp, botones)
            return _recordar(resp)  # imprescindible: así Pati no confunde su propio mensaje con una persona escribiendo
        except Exception as e:
            print(f"[BOTONES] No se pudieron enviar botones ({type(e).__name__}: {e}); va como texto")
            texto += "\nResponde: " + " · ".join(etiqueta for etiqueta, _ in botones[:3])
    return _recordar(cliente.send_message(jid, texto))


def _linea_clientes():
    """Los mensajes para clientes salen por la línea corporativa; si no existe, por la central."""
    return corporativa if corporativa is not None else central


class SalidaWA:
    """Conecta logica.py con WhatsApp para un mensaje entrante."""

    def __init__(self, linea, cliente, msg):
        self.linea = linea
        self.cliente = cliente
        self.msg = msg

    def responder(self, texto, botones=None):
        responder(self.cliente, texto, self.msg, botones)

    def enviar(self, telefono, texto, botones=None):
        enviar(central, build_jid(telefono), texto, botones)  # los avisos internos siempre van por la línea central

    def enviar_encuesta(self, telefono, pregunta, opciones, multiple=False):
        return enviar_encuesta(central, build_jid(telefono), pregunta, opciones, multiple)


# ── Scheduler: resumen diario, recordatorios y citas ─────────────────────────

def resumen_diario_job():
    """Envía el resumen diario al dueño (19:00) por la línea central."""
    if db.resumen_enviado_hoy():
        return  # ya se envió hoy
    texto = logica.texto_resumen_diario()
    enviados = 0
    for dueno in db.duenos():
        try:
            enviar(central, build_jid(dueno["telefono"]), texto)
            enviados += 1
            print(f"[RESUMEN] Enviado a {dueno['nombre']}")
        except Exception as e:
            print(f"[ERROR resumen] {e}")
    if enviados:
        db.registrar_resumen_hoy()  # si todo falló, se reintenta en la próxima conexión


def reporte_estado_job():
    """Reporte periódico del estado de las tareas al dueño (horas de negocio.json). No envía nada si no hay tareas."""
    texto = logica.texto_reporte_estado()
    if not texto:
        return
    for dueno in db.duenos():
        try:
            enviar(central, build_jid(dueno["telefono"]), texto)
            print(f"[REPORTE] Enviado a {dueno['nombre']}")
        except Exception as e:
            print(f"[ERROR reporte] {e}")


class SalidaJob:
    """Salida para los trabajos automáticos: los avisos internos siempre salen por la línea central."""
    linea = "central"

    def enviar(self, telefono, texto, botones=None):
        enviar(central, build_jid(telefono), texto, botones)

    def enviar_encuesta(self, telefono, pregunta, opciones, multiple=False):
        return enviar_encuesta(central, build_jid(telefono), pregunta, opciones, multiple)


def encuestas_job():
    """Cada minuto: recuerda una vez a quien no votó y manda al dueño el resumen cuando pasa el tiempo."""
    try:
        for tipo, encuesta_id in logica.revisar_encuestas(SalidaJob()):
            print(f"[ENCUESTAS] {tipo} (encuesta {encuesta_id})")
    except Exception as e:
        print(f"[ERROR encuestas] {type(e).__name__}: {e}")


def checkin_job():
    """Check-in diario del equipo ("¿cómo va tu día?"), a la hora de negocio.json (lunes a sábado)."""
    try:
        r = logica.enviar_checkin(SalidaJob())
        if r:
            print(f"[CHECK-IN] Enviado a {len(r[1])} persona(s)" + (f"; fallaron: {', '.join(r[2])}" if r[2] else ""))
    except Exception as e:
        print(f"[ERROR check-in] {type(e).__name__}: {e}")


def seguimiento_job():
    """Cada minuto: recordatorios de tareas con plazo y aviso al dueño si vencen sin completarse."""
    try:
        for tipo, tarea_id in logica.ejecutar_seguimiento(SalidaJob()):
            print(f"[SEGUIMIENTO] {tipo} (tarea {tarea_id})")
    except Exception as e:
        print(f"[ERROR seguimiento] {type(e).__name__}: {e}")


def enviar_recordatorios_job():
    """Revisa cada minuto si hay recordatorios que enviar a clientes (por la línea del negocio)."""
    for rec in db.recordatorios_pendientes():
        try:
            enviar(_linea_clientes(), build_jid(rec["telefono"]), rec["mensaje"])
            db.marcar_recordatorio_enviado(rec["id"])
            print(f"[RECORDATORIO] Enviado a {rec['cliente']} ({rec['telefono']})")
        except Exception as e:
            print(f"[ERROR recordatorio id={rec['id']}] {e}")


def enviar_citas_job():
    """Envía el recordatorio de cada cita cuando falten 24 horas o menos (por la línea del negocio)."""
    ahora = datetime.now(LIMA)
    for cita in db.citas_proximas():
        fecha_cita = datetime.fromisoformat(cita["fecha_hora"]).replace(tzinfo=LIMA)
        horas_antes = (fecha_cita - ahora).total_seconds() / 3600
        if 0 < horas_antes <= 24:
            try:
                enviar(_linea_clientes(), build_jid(cita["telefono"]), logica.mensaje_recordatorio_cita(cita))
                db.marcar_cita_recordatorio_enviado(cita["id"])
                print(f"[CITA RECORDATORIO] Enviado a {cita['cliente_nombre']} ({cita['telefono']})")
            except Exception as e:
                print(f"[ERROR cita recordatorio id={cita['id']}] {e}")


_scheduler = None


def arrancar_scheduler():
    """Inicia el scheduler (una sola vez) y, si se perdió el resumen de hoy, lo envía ahora."""
    global _scheduler
    if _scheduler is not None:
        return  # ya estaba corriendo (reconexión de WhatsApp)
    scheduler = _scheduler = BackgroundScheduler(timezone=LIMA)
    scheduler.add_job(resumen_diario_job, "cron", hour=19, minute=0, id="resumen_diario")
    scheduler.add_job(enviar_recordatorios_job, "interval", minutes=1, id="recordatorios")
    scheduler.add_job(enviar_citas_job, "interval", minutes=30, id="citas")
    scheduler.add_job(seguimiento_job, "interval", minutes=1, id="seguimiento", max_instances=1)
    scheduler.add_job(encuestas_job, "interval", minutes=1, id="encuestas", max_instances=1)
    if negocio.config().get("checkin_hora"):
        hh, mm = (int(x) for x in negocio.config()["checkin_hora"].split(":"))
        scheduler.add_job(checkin_job, "cron", day_of_week="mon-sat", hour=hh, minute=mm, id="checkin")
    for hora in negocio.config()["horas_reporte"]:  # reportes periódicos del estado de las tareas
        hh, mm = (int(x) for x in hora.split(":"))
        scheduler.add_job(reporte_estado_job, "cron", hour=hh, minute=mm, id=f"reporte_{hh:02d}{mm:02d}")
    scheduler.start()

    if datetime.now(LIMA).hour >= 19 and not db.resumen_enviado_hoy():
        print("[ARRANQUE] Resumen del día no enviado, enviando ahora...")
        resumen_diario_job()


# ── Mensajes entrantes ────────────────────────────────────────────────────────

RUTA_BITACORA = os.path.join(db.DATA_DIR, "pruebas_equipo.log")  # no se sube a git


def _bitacora(linea, quien, tipo, detalle=""):
    """Anota QUÉ llegó y CÓMO (no el contenido de los mensajes): sirve para depurar botones y encuestas con el equipo."""
    try:
        with open(RUTA_BITACORA, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now(LIMA):%Y-%m-%d %H:%M:%S} | {linea} | {quien} | {tipo} | {detalle}\n")
    except Exception as e:
        print(f"[BITÁCORA] No pude escribir ({type(e).__name__})")


def enviar_encuesta(cliente, jid, pregunta, opciones, multiple=False):
    """Envía una encuesta de WhatsApp (una sola opción, o varias si `multiple`) y recuerda cómo leer sus votos."""
    _pausa_humana(cliente, jid, pregunta)
    resp = cliente.send_message(
        jid, cliente.build_poll_vote_creation(pregunta, opciones, VoteType.MULTIPLE if multiple else VoteType.SINGLE))
    _bitacora("central", "-", "ENCUESTA ENVIADA", f"{getattr(resp, 'ID', '?')} a {jid.User if hasattr(jid, 'User') else jid}")
    return getattr(_recordar(resp), "ID", None)  # el id del mensaje: con él se reconocen los votos (también tras reiniciar)


def _id_encuesta_votada(msg):
    """Id de la encuesta que se votó, o None si el mensaje no es un voto."""
    try:
        if msg.Message.HasField("pollUpdateMessage"):
            return msg.Message.pollUpdateMessage.pollCreationMessageKey.ID or "?"
    except Exception:
        pass
    return None


def _descifrar_voto(c, msg):
    """Lee un voto de encuesta.

    neonize 0.5.2 falla en decrypt_poll_vote ("expected bytes, LP_Bytes found"): no convierte el resultado de Go a
    bytes como hace con las demás funciones. Si pasa, se hace aquí (leer los bytes y liberar la memoria de Go).
    """
    try:
        return c.decrypt_poll_vote(msg)
    except TypeError:
        from neonize._binder import free_bytes
        from neonize.proto import Neonize_pb2

        buf = msg.SerializeToString()
        crudo = c._NewClient__client.DecryptPollVote(c.uuid, buf, len(buf))
        try:
            datos = bytes(crudo) if isinstance(crudo, (bytes, bytearray)) else crudo.contents.get_bytes()
        finally:
            free_bytes(crudo)
        modelo = Neonize_pb2.ReturnFunctionWithError.FromString(datos)
        if modelo.Error:
            raise RuntimeError(modelo.Error)
        return modelo.PollVoteMessage


def _procesar_voto(c, msg, persona, salida, poll_id, linea):
    """Lee el voto, lo anota en la bitácora y se lo pasa a la lógica (que lo guarda, confirma y avisa al dueño)."""
    try:
        voto = _descifrar_voto(c, msg)
    except Exception as e:
        _bitacora(linea, persona["nombre"], "ENCUESTA", f"no pude leer el voto: {type(e).__name__}: {e}")
        return
    envio = db.envio_por_msg(poll_id)  # en la base de datos: los votos se reconocen aunque Pati se haya reiniciado
    enc = db.encuesta(envio["encuesta_id"]) if envio else None
    mapa = {hashlib.sha256(o.encode()).digest(): o for o in enc["opciones"]} if enc else {}
    hashes = [bytes(h) for h in voto.selectedOptions]
    nombres = [mapa.get(h, f"?{h.hex()[:8]}") for h in hashes]
    _bitacora(linea, persona["nombre"], "ENCUESTA",
              f"votó {nombres}" + ("" if enc else " (esa encuesta no es de Pati o no está registrada)"))
    if enc and envio["persona_id"] == persona["id"] and enc["tipo"].startswith("selector"):
        logica.procesar_seleccion(salida, persona, enc["id"], [n for n in nombres if n in enc["opciones"]])
    elif enc and envio["persona_id"] == persona["id"] and nombres and nombres[0] in enc["opciones"]:
        logica.procesar_voto_encuesta(salida, persona, enc["id"], nombres[0])  # guarda el voto, confirma y avisa al dueño


def _boton_de(msg):
    """Id del botón que tocó la persona ("listo:12"), o None si el mensaje no es un toque de botón de Pati."""
    m = msg.Message
    try:
        if m.HasField("interactiveResponseMessage"):
            bid = json.loads(m.interactiveResponseMessage.nativeFlowResponseMessage.paramsJSON or "{}").get("id", "")
        elif m.HasField("buttonsResponseMessage"):
            bid = m.buttonsResponseMessage.selectedButtonID
        elif m.HasField("templateButtonReplyMessage"):  # así llegan hoy los toques (comprobado con WhatsApp real)
            r = m.templateButtonReplyMessage
            bid = r.selectedID
            if not str(bid).startswith("pati|"):  # respaldo: se deduce por el mensaje citado y el texto del botón
                bid = "pati|" + ((_botones_enviados.get(r.contextInfo.stanzaID) or {}).get(logica.norm(r.selectedDisplayText), ""))
        else:
            return None
    except Exception:
        return None  # mensaje de prueba o formato inesperado
    bid = str(bid)
    return bid[5:] if bid.startswith("pati|") and len(bid) > 5 else None


def _boton_por_cita(msg, texto):
    """Respaldo: si el toque llegó como TEXTO ("✅ Listo") citando un mensaje con botones de Pati, se sabe cuál botón fue."""
    try:
        citado = msg.Message.extendedTextMessage.contextInfo.stanzaID
    except Exception:
        return None
    return (_botones_enviados.get(citado) or {}).get(logica.norm(texto))


def al_recibir(linea, c, msg):
    src = msg.Info.MessageSource
    if src.IsGroup:
        return
    chat = f"{linea}:{src.Chat.User}"  # la pausa es por línea y por chat
    edad = logica.edad_segundos(msg.Info.Timestamp)

    if src.IsFromMe:
        # Mensaje enviado desde esta cuenta: si no lo envió Pati, lo escribió una persona (celular o WhatsApp Web)
        if edad > logica.MAX_EDAD_PROPIO_SEG:
            return  # historial que llega al vincular la línea, no una persona escribiendo ahora
        time.sleep(1.5)  # da tiempo a que se registre el ID si lo envió Pati
        if msg.Info.ID not in _ids_pati:
            logica.pausar_por_humano(chat)
            print(f"[PAUSA] Escribió una persona en {chat}: Pati se calla {logica.PAUSA_HUMANO_MIN} min")
        return

    ident = src.Sender.User
    alterno = src.SenderAlt.User if src.HasField("SenderAlt") else ""  # el teléfono real si WhatsApp usa un ID interno
    if not alterno:
        alterno = _telefono_de_lid(linea, ident) or ""  # WhatsApp no lo mandó: se busca en su mapa de IDs
    texto = (msg.Message.conversation or msg.Message.extendedTextMessage.text or "").strip()
    boton = _boton_de(msg)
    via_boton = "botón"
    if not boton and texto:
        boton = _boton_por_cita(msg, texto)  # el toque llegó como texto citando un mensaje con botones
        via_boton = "texto citado"
    voto = _id_encuesta_votada(msg)
    if not texto and not boton and not voto:
        try:
            _bitacora(linea, "?", "SIN TEXTO", f"campos: {[f.name for f, _ in msg.Message.ListFields()]}")
        except Exception:
            pass
        return
    print(f"[DEBUG] {linea} | ID: {ident}" + (f" | texto: {texto}" if DEBUG_MENSAJES else ""))

    if logica.es_linea_propia(ident, alterno):
        print(f"[LÍNEAS] Ignorado: el mensaje viene de otra línea de Pati ({alterno or ident})")
        return  # las líneas de Pati nunca se responden entre sí (evita bucles)
    if edad > logica.MAX_EDAD_MENSAJE_SEG:
        print(f"[VIEJO] Ignorado un mensaje de hace {int(edad)} s ({linea})")
        return
    if logica.chat_pausado(chat):
        print(f"[PAUSA] {chat} en pausa: Pati no responde")
        return

    persona = db.persona_por_id(ident) or (db.persona_por_id(alterno) if alterno else None)
    if persona is not None and alterno and ident != alterno:
        db.guardar_lid(persona["id"], ident)  # aprende el ID interno sin copiarlo a mano
    salida = SalidaWA(linea, c, msg)
    if persona is None and voto:
        return  # un voto de alguien no registrado no merece respuesta
    if persona is None:
        if linea == "central":
            logica.atender_prospecto(salida, alterno or ident)  # número no registrado en la línea interna
            return
        persona = logica.registrar_desconocido(alterno or ident, ident if alterno else None, msg.Info.Pushname)
        if persona is None:
            return  # el negocio no atiende desconocidos, o este número está en su lista "ignorar"
    if not db.registrar_mensaje_id(f"{linea}:{msg.Info.ID}"):
        print(f"[AVISO] Mensaje repetido ignorado ({msg.Info.ID})")
        return  # reentrega del mismo mensaje
    try:
        if voto:
            _procesar_voto(c, msg, persona, salida, voto, linea)
        elif boton:
            try:
                campos = [f.name for f, _ in msg.Message.ListFields()]
            except Exception:
                campos = []
            _bitacora(linea, persona["nombre"], "BOTÓN", f"{boton} (llegó como {via_boton}; campos: {campos})")
            logica.procesar_boton(salida, persona, boton)
        else:
            if linea == "central":
                _bitacora(linea, persona["nombre"], "TEXTO", f"{len(texto)} caracteres")
            logica.procesar(salida, persona, texto)
    except Exception as e:
        print(f"[ERROR inesperado] {type(e).__name__}: {e}")


def registrar_eventos(linea, cliente):
    @cliente.event(ConnectedEv)
    def al_conectar(_, __):
        print(f"✅ Línea {linea.upper()} conectada")
        if linea == "central":
            arrancar_scheduler()

    @cliente.event(PairStatusEv)
    def al_vincular(_, ev):
        print(f"🔗 Línea {linea.upper()} vinculada como {ev.ID.User}")

    @cliente.event(MessageEv)
    def al_mensaje(c, msg):
        al_recibir(linea, c, msg)


for _linea, _cliente in LINEAS.items():
    registrar_eventos(_linea, _cliente)

if __name__ == "__main__":
    print("Líneas:", ", ".join(LINEAS), "| Ctrl+C para salir.")
    for _linea, _cliente in LINEAS.items():
        threading.Thread(target=_cliente.connect, daemon=True, name=_linea).start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Cerrando...")
        for _cliente in LINEAS.values():
            try:
                _cliente.stop()
            except Exception as e:
                print(f"(aviso al cerrar: {type(e).__name__})")
