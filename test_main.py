"""Pruebas del enrutamiento de main.py (qué hace Pati con cada mensaje y cada línea) SIN conectar WhatsApp.

Se usan mensajes falsos con la misma forma que los de neonize. No se abre ninguna conexión.
"""
import importlib
import json
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from neonize.utils import build_jid

import db
import logica
import negocio

main = None
_tmp = None
CENTRAL, CORP = "51900000100", "51900000200"
NEG = {**negocio.PERFILES["veterinaria"], "rubro": "veterinaria", "nombre": "Clínica Demo", "servicios": ["Vacunación"],
       "horario": None, "atender_desconocidos": True, "ignorar": [], "lineas": {"central": CENTRAL, "corporativa": CORP},
       "avisar_completadas": True, "horas_reporte": ["13:00", "17:30"], "seguimiento": {"activo": True, "recordar_min_antes": 15, "gracia_min": 10, "avisos_desde": "07:00", "avisos_hasta": "21:00"}}


def setUpModule():
    global main, _tmp
    _tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    db.DB = f"{_tmp.name}/main.db"
    db.PERSONAS_JSON = f"{_tmp.name}/no_existe.json"
    main = importlib.import_module("main")  # crea los clientes pero NO conecta


def tearDownModule():
    _tmp.cleanup()


def fake_msg(ident="51900000001", alterno="", texto="hola", edad_s=0, propio=False, grupo=False, id="M1", chat=None,
             pushname="Rosa", boton=None, cita=None):
    src = SimpleNamespace(IsGroup=grupo, IsFromMe=propio, Chat=SimpleNamespace(User=chat or ident),
                          Sender=SimpleNamespace(User=ident), SenderAlt=SimpleNamespace(User=alterno),
                          HasField=lambda campo: bool(alterno))
    info = SimpleNamespace(MessageSource=src, Timestamp=int((time.time() - edad_s) * 1000), ID=id, Pushname=pushname)
    mensaje = SimpleNamespace(conversation=texto, extendedTextMessage=SimpleNamespace(text=""))
    if cita:  # el texto llega como respuesta citando un mensaje de Pati
        mensaje = SimpleNamespace(conversation="", extendedTextMessage=SimpleNamespace(
            text=texto, contextInfo=SimpleNamespace(stanzaID=cita)))
    if boton:  # toque de un botón de WhatsApp (mensaje interactivo de respuesta)
        mensaje = SimpleNamespace(
            conversation="", extendedTextMessage=SimpleNamespace(text=""),
            HasField=lambda campo: campo == "interactiveResponseMessage",
            interactiveResponseMessage=SimpleNamespace(nativeFlowResponseMessage=SimpleNamespace(
                paramsJSON=json.dumps({"display_text": "x", "id": boton}))))
    return SimpleNamespace(Info=info, Message=mensaje)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        db.DB = f"{self.tmp.name}/t.db"
        db.init()
        with db.conn() as c:
            c.executemany("INSERT INTO personas(telefono, lid, nombre, rol) VALUES(?,?,?,?)",
                          [("51900000001", None, "Andrés", "maestro"), ("51900000003", None, "Mafer", "trabajador")])
        self.procesar = mock.patch.object(logica, "procesar")
        self.prospecto = mock.patch.object(logica, "atender_prospecto")
        self.p_procesar, self.p_prospecto = self.procesar.start(), self.prospecto.start()
        for p in (mock.patch.object(main, "RUTA_BITACORA", f"{self.tmp.name}/pruebas.log"),
                  mock.patch.object(main.time, "sleep"), mock.patch.object(negocio, "config", lambda: NEG),
                  mock.patch.object(main, "_ids_pati", main.deque(maxlen=50))):
            p.start()
        self.addCleanup(mock.patch.stopall)
        db.set_estado  # noqa (solo para asegurar que db está cargado)
        self.cliente = object()

    def recibir(self, linea="central", **kw):
        main.al_recibir(linea, self.cliente, fake_msg(**kw))

    def filas(self, tabla):
        with db.conn() as c:
            return [dict(f) for f in c.execute(f"SELECT * FROM {tabla}")]


class TestFiltros(Base):
    def test_los_grupos_se_ignoran(self):
        self.recibir(grupo=True)
        self.p_procesar.assert_not_called()

    def test_un_mensaje_de_otra_linea_de_pati_se_ignora(self):
        self.recibir(ident="112233", alterno=CORP)  # llega con ID interno, pero el teléfono real es la corporativa
        self.recibir("corporativa", ident=CENTRAL)
        self.p_procesar.assert_not_called()
        self.p_prospecto.assert_not_called()

    def test_un_mensaje_viejo_se_ignora(self):
        self.recibir(edad_s=logica.MAX_EDAD_MENSAJE_SEG + 5)
        self.p_procesar.assert_not_called()

    def test_un_mensaje_reciente_se_procesa(self):
        self.recibir(edad_s=30)
        self.p_procesar.assert_called_once()

    def test_mensaje_repetido_se_procesa_una_sola_vez(self):
        self.recibir(id="X1")
        self.recibir(id="X1")
        self.assertEqual(self.p_procesar.call_count, 1)

    def test_mensaje_vacio_se_ignora(self):
        self.recibir(texto="   ")
        self.p_procesar.assert_not_called()

    def test_un_error_al_procesar_no_tumba_a_pati(self):
        self.p_procesar.side_effect = RuntimeError("boom")
        self.recibir()  # no debe lanzar
        self.recibir(id="M2")
        self.assertEqual(self.p_procesar.call_count, 2)


class TestIdInterno(Base):
    """WhatsApp a veces manda un ID interno (LID) sin el teléfono: se busca en el mapa de la propia sesión."""

    def mapa(self, filas):
        ruta = f"{self.tmp.name}/sesion.sqlite3"
        import sqlite3
        con = sqlite3.connect(ruta)
        con.execute("CREATE TABLE whatsmeow_lid_map (lid TEXT PRIMARY KEY, pn TEXT UNIQUE NOT NULL)")
        con.executemany("INSERT INTO whatsmeow_lid_map VALUES(?,?)", filas)
        con.commit()
        con.close()
        mock.patch.dict(main._SESIONES, {"central": ruta}).start()

    def test_una_persona_registrada_se_reconoce_por_el_mapa_de_ids(self):
        self.mapa([("999111", "51900000003")])  # Mafer
        self.recibir(ident="999111", texto="listo")
        self.p_prospecto.assert_not_called()
        self.assertEqual(self.p_procesar.call_args[0][1]["nombre"], "Mafer")
        self.assertEqual(db.persona_por_id("51900000003")["lid"], "999111")  # y aprende su ID

    def test_un_id_que_no_esta_en_el_mapa_sigue_siendo_desconocido(self):
        self.mapa([("999111", "51900000003")])
        self.recibir(ident="777222", texto="hola")
        self.p_procesar.assert_not_called()
        self.p_prospecto.assert_called_once()

    def test_un_numero_desconocido_en_el_mapa_sigue_siendo_prospecto(self):
        self.mapa([("999111", "51988887777")])  # el teléfono existe pero no está registrado
        self.recibir(ident="999111", texto="hola")
        self.p_procesar.assert_not_called()
        self.p_prospecto.assert_called_once()

    def test_el_id_de_otra_linea_de_pati_se_ignora_aunque_no_venga_el_telefono(self):
        self.mapa([("555444", CORP)])
        self.recibir(ident="555444", texto="hola")
        self.p_procesar.assert_not_called()
        self.p_prospecto.assert_not_called()

    def test_sin_archivo_de_sesion_no_se_cae(self):
        mock.patch.dict(main._SESIONES, {"central": f"{self.tmp.name}/no_existe.sqlite3"}).start()
        self.recibir(ident="777222", texto="hola")
        self.p_prospecto.assert_called_once()

    def test_si_whatsapp_manda_el_telefono_no_se_consulta_el_mapa(self):
        with mock.patch.object(main, "_telefono_de_lid") as consulta:
            self.recibir(ident="999111", alterno="51900000003", texto="listo")
        consulta.assert_not_called()
        self.p_procesar.assert_called_once()


class TestBotones(Base):
    """Botones de WhatsApp: se reconocen al volver, se envían con respaldo en texto y nunca crean bucles."""

    def test_un_toque_de_boton_se_enruta_a_procesar_boton(self):
        with mock.patch.object(logica, "procesar_boton") as pb:
            self.recibir("central", ident="51900000003", texto="", boton="pati|listo:7", id="B1")
        pb.assert_called_once()
        self.assertEqual(pb.call_args[0][2], "listo:7")
        self.p_procesar.assert_not_called()

    def toque_plantilla(self, selected_id, display="⏳ Más tiempo", citado="PATI9", id="T1"):
        m = fake_msg(ident="51900000003", texto="", id=id)
        m.Message = SimpleNamespace(
            conversation="", extendedTextMessage=SimpleNamespace(text=""),
            HasField=lambda campo: campo == "templateButtonReplyMessage",
            ListFields=lambda: [(SimpleNamespace(name="templateButtonReplyMessage"), None)],
            templateButtonReplyMessage=SimpleNamespace(
                selectedID=selected_id, selectedDisplayText=display, contextInfo=SimpleNamespace(stanzaID=citado)))
        return m

    def test_el_toque_llega_como_template_button_reply_con_el_id(self):
        with mock.patch.object(logica, "procesar_boton") as pb:
            main.al_recibir("central", object(), self.toque_plantilla("pati|mas:7", id="T1"))
        self.assertEqual(pb.call_args[0][2], "mas:7")
        with open(main.RUTA_BITACORA, encoding="utf-8") as f:
            self.assertIn("templateButtonReplyMessage", f.read())  # queda anotado cómo llegó

    def test_template_button_reply_sin_id_nuestro_se_deduce_por_el_mensaje_citado(self):
        main._recordar_botones(SimpleNamespace(ID="PATI9"), [("⏳ Más tiempo", "mas:7")])
        with mock.patch.object(logica, "procesar_boton") as pb:
            main.al_recibir("central", object(), self.toque_plantilla("0", id="T2"))  # solo trae el índice
        self.assertEqual(pb.call_args[0][2], "mas:7")

    def test_template_button_reply_que_no_se_reconoce_no_hace_nada(self):
        with mock.patch.object(logica, "procesar_boton") as pb:
            main.al_recibir("central", object(), self.toque_plantilla("otra-cosa", display="Quién sabe", citado="NADA", id="T3"))
        pb.assert_not_called()
        self.p_procesar.assert_not_called()

    def test_un_boton_que_no_es_de_pati_se_ignora(self):
        with mock.patch.object(logica, "procesar_boton") as pb:
            self.recibir("central", ident="51900000003", texto="", boton="otro|listo:7", id="B2")
        pb.assert_not_called()
        self.p_procesar.assert_not_called()

    def test_el_mismo_toque_no_se_procesa_dos_veces(self):
        with mock.patch.object(logica, "procesar_boton") as pb:
            self.recibir("central", ident="51900000003", texto="", boton="pati|listo:7", id="B3")
            self.recibir("central", ident="51900000003", texto="", boton="pati|listo:7", id="B3")
        self.assertEqual(pb.call_count, 1)

    def test_un_toque_de_alguien_no_registrado_no_hace_nada(self):
        with mock.patch.object(logica, "procesar_boton") as pb:
            self.recibir("central", ident="51999999999", texto="", boton="pati|listo:7", id="B4")
        pb.assert_not_called()

    def test_respaldo_el_toque_llega_como_texto_citando_el_mensaje_con_botones(self):
        main._recordar_botones(SimpleNamespace(ID="PATI1"), [("✅ Listo", "listo:7"), ("⏳ Más tiempo", "mas:7")])
        with mock.patch.object(logica, "procesar_boton") as pb:
            self.recibir("central", ident="51900000003", texto="✅ Listo", cita="PATI1", id="B5")
            self.recibir("central", ident="51900000003", texto="Más tiempo", cita="PATI1", id="B6")
        self.assertEqual([c[0][2] for c in pb.call_args_list], ["listo:7", "mas:7"])

    def test_texto_normal_que_cita_un_mensaje_sin_botones_va_por_el_flujo_normal(self):
        self.recibir("central", ident="51900000003", texto="listo", cita="OTRO", id="B7")
        self.p_procesar.assert_called_once()

    def test_enviar_con_botones_registra_el_id_para_no_pausarse_a_si_misma(self):
        cliente = mock.Mock()
        cliente.send_interactive_message.return_value = SimpleNamespace(ID="ENVIADO1")
        main.enviar(cliente, "jid", "hola", [("✅ Listo", "listo:1")])
        cliente.send_interactive_message.assert_called_once()
        self.assertIn("ENVIADO1", main._ids_pati)  # si no, su propio mensaje pausaría el chat como si escribiera una persona
        main.al_recibir("central", cliente, fake_msg(ident="51900000001", propio=True, id="ENVIADO1", chat="51900000003"))
        self.assertFalse(logica.chat_pausado("central:51900000003"))

    def test_si_los_botones_fallan_va_como_texto_con_las_opciones(self):
        cliente = mock.Mock()
        cliente.send_interactive_message.side_effect = RuntimeError("no soportado")
        cliente.send_message.return_value = SimpleNamespace(ID="T1")
        main.enviar(cliente, "jid", "⏰ vence", [("✅ Listo", "listo:1"), ("⏳ Más tiempo", "mas:1")])
        texto = cliente.send_message.call_args[0][1]
        self.assertIn("⏰ vence", texto)
        self.assertIn("✅ Listo · ⏳ Más tiempo", texto)

    def test_maximo_tres_botones(self):
        m = main._mensaje_botones("x", [("a", "1"), ("b", "2"), ("c", "3"), ("d", "4")])
        self.assertEqual(len(m._buttons), 3)

    def test_responder_con_botones_va_como_mensaje_nuevo_al_chat(self):
        cliente = mock.Mock()
        cliente.send_interactive_message.return_value = SimpleNamespace(ID="R1")
        msg = fake_msg()
        main.responder(cliente, "¿cuánto?", msg, [("15 min", "plazo:1:15")])
        cliente.send_interactive_message.assert_called_once()
        cliente.reply_message.assert_not_called()


class TestEncuestasYBitacora(Base):
    def bitacora(self):
        try:
            with open(main.RUTA_BITACORA, encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            return ""

    def voto_msg(self, ident="51900000003", poll="POLL1", id="V1"):
        m = fake_msg(ident=ident, texto="", id=id)
        m.Message = SimpleNamespace(
            conversation="", extendedTextMessage=SimpleNamespace(text=""),
            HasField=lambda campo: campo == "pollUpdateMessage",
            pollUpdateMessage=SimpleNamespace(pollCreationMessageKey=SimpleNamespace(ID=poll)))
        return m

    def cliente_con_voto(self, opciones):
        cliente = mock.Mock()
        cliente.decrypt_poll_vote.return_value = SimpleNamespace(
            selectedOptions=[main.hashlib.sha256(o.encode()).digest() for o in opciones])
        return cliente

    def test_enviar_encuesta_registra_ids_y_opciones(self):
        cliente = mock.Mock()
        cliente.send_message.return_value = SimpleNamespace(ID="POLL1")
        self.assertEqual(main.enviar_encuesta(cliente, build_jid("51900000003"), "¿Cómo va?", ["✅ Bien", "⚠️ Mal"]), "POLL1")
        self.assertIn("POLL1", main._ids_pati)  # no se confunde con una persona escribiendo
        self.assertIn("ENCUESTA ENVIADA", self.bitacora())

    def test_un_voto_se_lee_se_anota_y_se_confirma(self):
        op = logica.CHECKIN_OPCIONES[1]
        cliente = self.cliente_con_voto([op])
        enc = db.crear_encuesta("¿Cómo va?", logica.CHECKIN_OPCIONES, "checkin")
        db.registrar_envio_encuesta("POLL1", enc, db.persona_por_id("51900000003")["id"])  # guardada en la base, no en memoria
        with mock.patch.object(logica, "procesar_voto_encuesta") as pv:
            main.al_recibir("central", cliente, self.voto_msg())
        self.assertIn(f"Mafer | ENCUESTA | votó ['{op}']", self.bitacora())
        self.assertEqual(pv.call_args[0][1]["nombre"], "Mafer")
        self.assertEqual(pv.call_args[0][2:], (enc, op))

    def test_un_selector_de_varias_opciones_pasa_todas_las_marcadas(self):
        opciones = ["Caja · Ashly", "Limpiar · Mafer", logica.CONTINUAR]
        enc = db.crear_encuesta("¿Qué cancelo?", opciones, "selector_cancelar")
        db.registrar_envio_encuesta("SEL1", enc, db.persona_por_id("51900000001")["id"])  # el dueño
        cliente = self.cliente_con_voto([opciones[0], opciones[2]])
        with mock.patch.object(logica, "procesar_seleccion") as ps, mock.patch.object(logica, "procesar_voto_encuesta") as pv:
            main.al_recibir("central", cliente, self.voto_msg(ident="51900000001", poll="SEL1", id="S1"))
        self.assertEqual(ps.call_args[0][2:], (enc, [opciones[0], opciones[2]]))
        pv.assert_not_called()

    def test_la_encuesta_de_varias_opciones_se_crea_como_multiple(self):
        cliente = mock.Mock()
        cliente.send_message.return_value = SimpleNamespace(ID="M1")
        main.enviar_encuesta(cliente, build_jid("51900000001"), "¿Cuáles?", ["a", "b"], multiple=True)
        self.assertEqual(cliente.build_poll_vote_creation.call_args[0][2], main.VoteType.MULTIPLE)
        main.enviar_encuesta(cliente, build_jid("51900000001"), "¿Cuál?", ["a", "b"])
        self.assertEqual(cliente.build_poll_vote_creation.call_args[0][2], main.VoteType.SINGLE)

    def test_un_voto_en_la_encuesta_de_otra_persona_se_ignora(self):
        enc = db.crear_encuesta("¿Cómo va?", ["✅ Bien", "⚠️ Mal"])
        db.registrar_envio_encuesta("POLL1", enc, db.persona_por_id("51900000001")["id"])  # era de Andrés
        with mock.patch.object(logica, "procesar_voto_encuesta") as pv:
            main.al_recibir("central", self.cliente_con_voto(["✅ Bien"]), self.voto_msg())  # vota Mafer
        pv.assert_not_called()
        self.p_procesar.assert_not_called()  # un voto no es un texto para la lógica
        self.p_prospecto.assert_not_called()

    def test_el_fallo_de_neonize_con_los_bytes_se_resuelve_leyendolos_a_mano(self):
        from neonize.proto import Neonize_pb2
        hash_ = main.hashlib.sha256("⚠️ Mal".encode()).digest()
        rf = Neonize_pb2.ReturnFunctionWithError()
        rf.PollVoteMessage.selectedOptions.append(hash_)
        serializado = rf.SerializeToString()
        crudo = SimpleNamespace(contents=SimpleNamespace(get_bytes=lambda: serializado))
        cliente = mock.Mock()
        cliente.decrypt_poll_vote.side_effect = TypeError("expected bytes, LP_Bytes found")  # el fallo real de neonize 0.5.2
        cliente._NewClient__client.DecryptPollVote.return_value = crudo
        msg = SimpleNamespace(SerializeToString=lambda: b"x")
        with mock.patch("neonize._binder.free_bytes") as libera:
            voto = main._descifrar_voto(cliente, msg)
        self.assertEqual([bytes(h) for h in voto.selectedOptions], [hash_])
        libera.assert_called_once_with(crudo)  # no deja memoria de Go sin liberar

    def test_el_error_que_devuelve_go_se_informa(self):
        from neonize.proto import Neonize_pb2
        serializado = Neonize_pb2.ReturnFunctionWithError(Error="sin clave").SerializeToString()
        cliente = mock.Mock()
        cliente.decrypt_poll_vote.side_effect = TypeError("expected bytes, LP_Bytes found")
        cliente._NewClient__client.DecryptPollVote.return_value = serializado  # una versión futura que ya devuelve bytes
        with mock.patch("neonize._binder.free_bytes"):
            with self.assertRaises(RuntimeError):
                main._descifrar_voto(cliente, SimpleNamespace(SerializeToString=lambda: b"x"))

    def test_un_voto_que_no_se_puede_leer_no_tumba_a_pati(self):
        cliente = mock.Mock()
        cliente.decrypt_poll_vote.side_effect = RuntimeError("sin clave")
        main.al_recibir("central", cliente, self.voto_msg(id="V2"))
        self.assertIn("no pude leer el voto: RuntimeError: sin clave", self.bitacora())

    def test_voto_de_una_encuesta_enviada_antes_de_reiniciar_se_anota_como_no_reconocida(self):
        cliente = self.cliente_con_voto(["✅ Bien"])
        with mock.patch.object(logica, "procesar_voto_encuesta") as pv:
            main.al_recibir("central", cliente, self.voto_msg(poll="VIEJA", id="V3"))
        self.assertIn("no es de Pati o no está registrada", self.bitacora())
        pv.assert_not_called()

    def test_voto_de_alguien_no_registrado_se_ignora(self):
        cliente = self.cliente_con_voto(["✅ Bien"])
        main.al_recibir("central", cliente, self.voto_msg(ident="51977776666", id="V4"))
        self.p_prospecto.assert_not_called()
        self.assertNotIn("ENCUESTA", self.bitacora())

    def test_la_bitacora_anota_como_llego_el_boton_y_no_el_texto(self):
        with mock.patch.object(logica, "procesar_boton"):
            self.recibir("central", ident="51900000003", texto="", boton="pati|listo:7", id="L1")
        self.recibir("central", ident="51900000003", texto="clave secreta 123", id="L2")
        log = self.bitacora()
        self.assertIn("Mafer | BOTÓN | listo:7 (llegó como botón; campos:", log)
        self.assertIn("Mafer | TEXTO | 17 caracteres", log)
        self.assertNotIn("clave secreta", log)  # nunca se guarda lo que escribe la gente

    def test_mensaje_sin_texto_anota_sus_campos_para_depurar(self):
        m = fake_msg(texto="", id="S1")
        m.Message.ListFields = lambda: [(SimpleNamespace(name="stickerMessage"), None)]
        main.al_recibir("central", mock.Mock(), m)
        self.assertIn("SIN TEXTO | campos: ['stickerMessage']", self.bitacora())


class TestPausaPorHumano(Base):
    def test_una_persona_que_escribe_pausa_ese_chat_y_esa_linea(self):
        self.recibir("corporativa", propio=True, id="H1", chat="51999999999")
        self.assertTrue(logica.chat_pausado("corporativa:51999999999"))
        self.assertFalse(logica.chat_pausado("central:51999999999"))
        self.recibir("corporativa", ident="51999999999", alterno="", id="C1")  # el cliente escribe: Pati se calla
        self.p_procesar.assert_not_called()

    def test_los_mensajes_que_envio_pati_no_cuentan_como_una_persona(self):
        main._ids_pati.append("PATI1")
        self.recibir(propio=True, id="PATI1", chat="51999999999")
        self.assertFalse(logica.chat_pausado("central:51999999999"))

    def test_el_historial_al_vincular_no_pausa_chats(self):
        self.recibir("corporativa", propio=True, id="OLD", chat="51999999999", edad_s=logica.MAX_EDAD_PROPIO_SEG + 60)
        self.assertFalse(logica.chat_pausado("corporativa:51999999999"))

    def test_en_pausa_pati_no_responde(self):
        logica.pausar_por_humano("central:51900000001")
        self.recibir()
        self.p_procesar.assert_not_called()


class TestRutasPorLinea(Base):
    def test_registrado_en_la_central_se_procesa_con_su_persona(self):
        self.recibir("central", ident="51900000003")
        persona = self.p_procesar.call_args[0][1]
        salida = self.p_procesar.call_args[0][0]
        self.assertEqual((persona["nombre"], salida.linea), ("Mafer", "central"))

    def test_desconocido_en_la_central_no_se_registra_y_recibe_info(self):
        self.recibir("central", ident="51955555555")
        self.p_prospecto.assert_called_once()
        self.p_procesar.assert_not_called()
        self.assertEqual(len(self.filas("personas")), 2)

    def test_desconocido_en_la_corporativa_se_registra_como_cliente(self):
        self.recibir("corporativa", ident="51955555555", pushname="Rosa")
        persona = self.p_procesar.call_args[0][1]
        self.assertEqual((persona["rol"], persona["nombre"]), ("cliente", "Rosa"))
        self.assertEqual(self.p_procesar.call_args[0][0].linea, "corporativa")

    def test_el_equipo_en_la_corporativa_se_procesa_en_esa_linea(self):
        self.recibir("corporativa", ident="51900000003")
        self.assertEqual(self.p_procesar.call_args[0][0].linea, "corporativa")
        self.assertEqual(self.p_procesar.call_args[0][1]["nombre"], "Mafer")

    def test_el_negocio_puede_no_atender_desconocidos(self):
        with mock.patch.dict(NEG, {"atender_desconocidos": False}):
            self.recibir("corporativa", ident="51955555555")
        self.p_procesar.assert_not_called()

    def test_numeros_ignorados_no_reciben_respuesta(self):
        with mock.patch.dict(NEG, {"ignorar": ["51955555555"]}):
            self.recibir("corporativa", ident="51955555555")
        self.p_procesar.assert_not_called()

    def test_aprende_el_id_interno_cuando_llega_con_telefono_alterno(self):
        self.recibir("central", ident="777888", alterno="51900000003")
        self.assertEqual(db.persona_por_id("777888")["nombre"], "Mafer")


class TestSalidaYEnvios(Base):
    def test_los_avisos_internos_siempre_salen_por_la_central(self):
        cliente_corp = object()
        salida = main.SalidaWA("corporativa", cliente_corp, fake_msg())
        with mock.patch.object(main, "enviar") as enviar:
            salida.enviar("51900000002", "aviso")
        self.assertIs(enviar.call_args[0][0], main.central)

    def test_las_respuestas_salen_por_la_linea_que_recibio_el_mensaje(self):
        cliente_corp, msg = object(), fake_msg()
        salida = main.SalidaWA("corporativa", cliente_corp, msg)
        with mock.patch.object(main, "responder") as responder:
            salida.responder("hola")
        self.assertEqual(responder.call_args[0], (cliente_corp, "hola", msg, None))

    def test_los_mensajes_a_clientes_salen_por_la_corporativa_si_existe(self):
        corp = object()
        with mock.patch.object(main, "corporativa", corp):
            self.assertIs(main._linea_clientes(), corp)
        with mock.patch.object(main, "corporativa", None):
            self.assertIs(main._linea_clientes(), main.central)

    def test_recordatorios_a_clientes_salen_por_la_linea_del_negocio(self):
        db.crear_recordatorio("Lucía", "51900000004", "Hola Lucía", "2000-01-01 10:00")
        corp = object()
        with mock.patch.object(main, "corporativa", corp), mock.patch.object(main, "enviar") as enviar:
            main.enviar_recordatorios_job()
        self.assertIs(enviar.call_args[0][0], corp)
        self.assertEqual(self.filas("recordatorios")[0]["enviado"], 1)

    def test_el_resumen_diario_sale_por_la_central(self):
        with mock.patch.object(main, "enviar") as enviar:
            main.resumen_diario_job()
        self.assertIs(enviar.call_args[0][0], main.central)
        self.assertEqual(enviar.call_args[0][1].User, "51900000001")

    def test_un_envio_fallido_no_marca_el_recordatorio_como_enviado(self):
        db.crear_recordatorio("Lucía", "51900000004", "Hola", "2000-01-01 10:00")
        with mock.patch.object(main, "enviar", side_effect=RuntimeError("sin red")):
            main.enviar_recordatorios_job()
        self.assertEqual(self.filas("recordatorios")[0]["enviado"], 0)


class TestReportesPeriodicos(Base):
    def test_el_reporte_llega_al_dueno_por_la_central_si_hay_tareas(self):
        mafer = db.persona_por_id("51900000003")
        db.crear_tarea(mafer["id"], "Limpiar", None, None)
        with mock.patch.object(main, "enviar") as enviar:
            main.reporte_estado_job()
        self.assertEqual(enviar.call_count, 1)
        self.assertIs(enviar.call_args[0][0], main.central)
        self.assertEqual(enviar.call_args[0][1].User, "51900000001")
        self.assertIn("Limpiar", enviar.call_args[0][2])

    def test_sin_tareas_no_se_envia_nada(self):
        with mock.patch.object(main, "enviar") as enviar:
            main.reporte_estado_job()
        enviar.assert_not_called()

    def test_el_scheduler_programa_un_reporte_por_cada_hora_configurada(self):
        trabajos = []
        falso = mock.Mock()
        falso.add_job.side_effect = lambda f, *a, **k: trabajos.append((f.__name__, a, k))
        with mock.patch.object(main, "BackgroundScheduler", return_value=falso),                 mock.patch.object(main, "_scheduler", None), mock.patch.object(db, "resumen_enviado_hoy", return_value=True):
            main.arrancar_scheduler()
        reportes = [(k["hour"], k["minute"], k["id"]) for nombre, a, k in trabajos if nombre == "reporte_estado_job"]
        self.assertEqual(reportes, [(13, 0, "reporte_1300"), (17, 30, "reporte_1730")])
        self.assertIn("resumen_diario_job", [n for n, _, _ in trabajos])  # el resumen de las 7 p. m. sigue
        falso.start.assert_called_once()

    def test_sin_horas_configuradas_no_hay_reportes_extra(self):
        trabajos = []
        falso = mock.Mock()
        falso.add_job.side_effect = lambda f, *a, **k: trabajos.append(f.__name__)
        with mock.patch.dict(NEG, {"horas_reporte": []}), mock.patch.object(main, "BackgroundScheduler", return_value=falso),                 mock.patch.object(main, "_scheduler", None), mock.patch.object(db, "resumen_enviado_hoy", return_value=True):
            main.arrancar_scheduler()
        self.assertNotIn("reporte_estado_job", trabajos)


class TestSeguimientoAutomatico(Base):
    def test_el_trabajo_de_seguimiento_llama_a_la_logica_con_salida_por_la_central(self):
        with mock.patch.object(logica, "ejecutar_seguimiento", return_value=[("previo", 1)]) as ejecutar:
            main.seguimiento_job()
        salida = ejecutar.call_args[0][0]
        self.assertIsInstance(salida, main.SalidaJob)
        self.assertEqual(salida.linea, "central")

    def test_los_avisos_del_seguimiento_salen_por_la_central(self):
        with mock.patch.object(main, "enviar") as enviar:
            main.SalidaJob().enviar("51900000003", "hola")
        self.assertIs(enviar.call_args[0][0], main.central)
        self.assertEqual(enviar.call_args[0][1].User, "51900000003")

    def test_un_error_en_el_seguimiento_no_tumba_el_programa(self):
        with mock.patch.object(logica, "ejecutar_seguimiento", side_effect=RuntimeError("boom")):
            main.seguimiento_job()  # no debe lanzar

    def test_el_scheduler_programa_el_seguimiento_cada_minuto(self):
        trabajos = []
        falso = mock.Mock()
        falso.add_job.side_effect = lambda f, *a, **k: trabajos.append((f.__name__, a, k))
        with mock.patch.object(main, "BackgroundScheduler", return_value=falso),                 mock.patch.object(main, "_scheduler", None), mock.patch.object(db, "resumen_enviado_hoy", return_value=True):
            main.arrancar_scheduler()
        seg = [(a, k) for n, a, k in trabajos if n == "seguimiento_job"]
        self.assertEqual(len(seg), 1)
        self.assertEqual((seg[0][0], seg[0][1]["minutes"], seg[0][1]["max_instances"]), (("interval",), 1, 1))


class TestConfiguracion(unittest.TestCase):
    def test_siempre_existe_la_linea_central(self):
        self.assertIn("central", main.LINEAS)

    def test_la_corporativa_solo_existe_si_el_negocio_la_configura(self):
        self.assertEqual("corporativa" in main.LINEAS, bool(negocio.config()["lineas"].get("corporativa")))


if __name__ == "__main__":
    unittest.main()
