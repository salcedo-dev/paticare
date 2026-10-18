"""Prueba de botones en WhatsApp (experimento; no usa la lógica de Pati ni la IA).

Qué hace: conecta la línea CENTRAL y le envía, UNA sola vez, a TU celular personal dos mensajes de prueba:
  1. Un mensaje con botones de respuesta rápida (✅ Listo / ⚠️ Problema / ⏳ Más tiempo).
  2. Una encuesta de WhatsApp con las mismas tres opciones.
Después solo ESCUCHA e imprime qué recibió cuando tocas un botón o votas. No responde nada (no hay bucles).

Antes de correrla, apaga Pati (dos programas no pueden usar la misma sesión de WhatsApp).
  $env:PYTHONUTF8=1; venv\\Scripts\\python.exe prueba_botones.py
Opcional: otro destino con  prueba_botones.py 51999999999   (siempre un celular PERSONAL, nunca la otra línea de Pati).
Se apaga con Ctrl+C.
"""
import json
import os
import sys
import threading

import negocio
from neonize.client import NewClient
from neonize.events import ConnectedEv, MessageEv
from neonize.ext.interactive_message import ButtonMessage
from neonize.utils import build_jid
from neonize.utils.enum import VoteType

AQUI = os.path.dirname(os.path.abspath(__file__))
NUMEROS_PROPIOS = set(negocio.config()["lineas"].values())  # las líneas de Pati (negocio.json): nunca se les escribe


def _destino():
    if len(sys.argv) > 1:
        return "".join(c for c in sys.argv[1] if c.isdigit())
    ruta = os.path.join(AQUI, "personas.json")
    with open(ruta, encoding="utf-8") as f:
        for p in json.load(f):
            if p.get("rol") == "maestro":
                return "".join(c for c in p["telefono"] if c.isdigit())
    raise SystemExit("No encontré al dueño en personas.json; pasa el número como argumento.")


DESTINO = _destino()
if DESTINO in NUMEROS_PROPIOS:
    raise SystemExit("Ese número es una línea de Pati. Usa un celular personal.")

cliente = NewClient(os.path.join(AQUI, "pati_sesion.sqlite3"))
_enviado = threading.Event()


def _enviar_pruebas():
    jid = build_jid(DESTINO)
    try:
        botones = (ButtonMessage()
                   .set_body("🧪 Prueba 1/2: botones. Toca uno.")
                   .set_footer("Pati · prueba")
                   .add_reply("✅ Listo", "tarea_listo")
                   .add_reply("⚠️ Problema", "tarea_problema")
                   .add_reply("⏳ Más tiempo", "tarea_mas_tiempo"))
        cliente.send_interactive_message(jid, botones)
        print("[BOTONES] Enviado el mensaje con botones.")
    except Exception as e:
        print(f"[BOTONES] ERROR al enviar botones: {type(e).__name__}: {e}")
    try:
        encuesta = cliente.build_poll_vote_creation(
            "🧪 Prueba 2/2: encuesta. ¿Cómo va la tarea?",
            ["✅ Listo", "⚠️ Problema", "⏳ Más tiempo"],
            VoteType.SINGLE,
        )
        cliente.send_message(jid, encuesta)
        print("[BOTONES] Enviada la encuesta.")
    except Exception as e:
        print(f"[BOTONES] ERROR al enviar la encuesta: {type(e).__name__}: {e}")
    print("[BOTONES] Mira tu celular: ¿se ven los botones? ¿y la encuesta? Toca/vota y fíjate en lo que sale aquí.")


@cliente.event(ConnectedEv)
def al_conectar(c, ev):
    print("[BOTONES] Conectada la línea central.")
    if not _enviado.is_set():
        _enviado.set()
        _enviar_pruebas()


@cliente.event(MessageEv)
def al_recibir(c, msg):
    # Solo se muestra lo que llega; NUNCA se responde.
    info = msg.Info
    try:
        origen = info.MessageSource
        if origen.IsFromMe:
            return
        m = msg.Message
        if m.HasField("interactiveResponseMessage"):
            r = m.interactiveResponseMessage
            print(f"[BOTONES] ✔ Tocaste un botón: {r.nativeFlowResponseMessage.paramsJSON}")
        elif m.HasField("buttonsResponseMessage"):
            print(f"[BOTONES] ✔ Botón (formato antiguo): {m.buttonsResponseMessage.selectedButtonID}")
        elif m.HasField("pollUpdateMessage"):
            try:
                voto = c.decrypt_poll_vote(msg)
                print(f"[BOTONES] ✔ Votaste en la encuesta: {list(voto.selectedOptions)}")
            except Exception as e:
                print(f"[BOTONES] Llegó un voto pero no pude leerlo: {type(e).__name__}: {e}")
        else:
            texto = m.conversation or m.extendedTextMessage.text
            print(f"[BOTONES] Llegó otro mensaje (tipo texto/otro): {texto[:60]!r}")
    except Exception as e:
        print(f"[BOTONES] Error leyendo el mensaje: {type(e).__name__}: {e}")


if __name__ == "__main__":
    print(f"[BOTONES] Destino: {DESTINO}. Conectando... (Ctrl+C para salir)")
    try:
        cliente.connect()
    except KeyboardInterrupt:
        print("\n[BOTONES] Apagada.")
