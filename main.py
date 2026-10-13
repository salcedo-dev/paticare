"""PatiCare - Pati, el asistente de WhatsApp (conexión con neonize).

La lógica de conversación vive en logica.py; aquí solo se conecta con WhatsApp,
se envían los mensajes con una pausa "humana" y corre el scheduler:
resumen diario 19:00 Lima + recordatorios (cada minuto) + citas (cada 30 min).
"""
import os
import random
import time
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from neonize.client import NewClient
from neonize.events import ConnectedEv, MessageEv, PairStatusEv
from neonize.utils import build_jid
from neonize.utils.enum import ChatPresence, ChatPresenceMedia
from zoneinfo import ZoneInfo

import db
import logica

db.init()
db.cargar_personas()  # lee personas.json; no borra clientes agregados a mano
client = NewClient(os.path.join(db.AQUI, "pati_sesion.sqlite3"))  # sesión de WhatsApp (no subir a git)

LIMA = ZoneInfo("America/Lima")
DEBUG_MENSAJES = os.getenv("PATI_DEBUG") == "1"  # 1 = imprimir también el texto de los mensajes


# ── Envío "humano" ────────────────────────────────────────────────────────────
# Pausa aleatoria + "escribiendo..." para no responder al instante como un bot.

def _pausa_humana(jid, texto):
    try:
        client.send_chat_presence(
            jid, ChatPresence.CHAT_PRESENCE_COMPOSING, ChatPresenceMedia.CHAT_PRESENCE_MEDIA_TEXT
        )
    except Exception as e:
        print(f"[AVISO presencia] {type(e).__name__}: {e}")
    time.sleep(min(1.0 + len(texto) * 0.015 + random.uniform(0, 1.5), 4))


def responder(texto, msg):
    """Responde citando el mensaje recibido."""
    _pausa_humana(msg.Info.MessageSource.Chat, texto)
    return client.reply_message(texto, msg)


def enviar(jid, texto):
    """Envía un mensaje nuevo a un JID ya construido."""
    _pausa_humana(jid, texto)
    return client.send_message(jid, texto)


class SalidaWA:
    """Conecta logica.py con WhatsApp para un mensaje entrante."""

    def __init__(self, msg):
        self.msg = msg

    def responder(self, texto):
        responder(texto, self.msg)

    def enviar(self, telefono, texto):
        enviar(build_jid(telefono), texto)


# ── Scheduler: resumen diario, recordatorios y citas ─────────────────────────

def resumen_diario_job():
    """Envía el resumen diario al dueño (19:00)."""
    if db.resumen_enviado_hoy():
        return  # ya se envió hoy
    texto = logica.texto_resumen_diario()
    enviados = 0
    for dueno in db.duenos():
        try:
            enviar(build_jid(dueno["telefono"]), texto)
            enviados += 1
            print(f"[RESUMEN] Enviado a {dueno['nombre']}")
        except Exception as e:
            print(f"[ERROR resumen] {e}")
    if enviados:
        db.registrar_resumen_hoy()  # si todo falló, se reintenta en la próxima conexión


def enviar_recordatorios_job():
    """Revisa cada minuto si hay recordatorios que enviar."""
    for rec in db.recordatorios_pendientes():
        try:
            enviar(build_jid(rec["telefono"]), rec["mensaje"])
            db.marcar_recordatorio_enviado(rec["id"])
            print(f"[RECORDATORIO] Enviado a {rec['cliente']} ({rec['telefono']})")
        except Exception as e:
            print(f"[ERROR recordatorio id={rec['id']}] {e}")


def enviar_citas_job():
    """Envía el recordatorio de cada cita cuando falten 24 horas o menos."""
    ahora = datetime.now(LIMA)
    for cita in db.citas_proximas():
        fecha_cita = datetime.fromisoformat(cita["fecha_hora"]).replace(tzinfo=LIMA)
        horas_antes = (fecha_cita - ahora).total_seconds() / 3600
        if 0 < horas_antes <= 24:
            mensaje = (
                f"🐾 *Recordatorio de cita*\n\n"
                f"Mascota: {cita['mascota']}\n"
                f"Servicio: {cita['servicio']}\n"
                f"Fecha: {cita['fecha_hora']}\n\n"
                f"¡Te esperamos!"
            )
            try:
                enviar(build_jid(cita["telefono"]), mensaje)
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
    scheduler.start()

    if datetime.now(LIMA).hour >= 19 and not db.resumen_enviado_hoy():
        print("[ARRANQUE] Resumen del día no enviado, enviando ahora...")
        resumen_diario_job()


# ── Eventos de neonize ────────────────────────────────────────────────────────

@client.event(PairStatusEv)
def al_vincular(_: NewClient, ev: PairStatusEv):
    print(f"🔗 Vinculado como {ev.ID.User}")


@client.event(ConnectedEv)
def al_conectar(_: NewClient, __: ConnectedEv):
    print("✅ Pati conectada a WhatsApp")
    arrancar_scheduler()


@client.event(MessageEv)
def al_recibir(c: NewClient, msg: MessageEv):
    src = msg.Info.MessageSource
    if src.IsFromMe or src.IsGroup:
        return
    ident = src.Sender.User
    texto = (msg.Message.conversation or msg.Message.extendedTextMessage.text or "").strip()
    if not texto:
        return
    print(f"[DEBUG] ID: {ident}" + (f" | texto: {texto}" if DEBUG_MENSAJES else ""))

    persona = db.persona_por_id(ident)
    if persona is None:
        return  # número desconocido: se ignora
    if not db.registrar_mensaje_id(msg.Info.ID):
        print(f"[AVISO] Mensaje repetido ignorado ({msg.Info.ID})")
        return  # reentrega del mismo mensaje
    try:
        logica.procesar(SalidaWA(msg), persona, texto)
    except Exception as e:
        print(f"[ERROR inesperado] {type(e).__name__}: {e}")


client.connect()
