"""Pruebas de la ficha del cliente, el agendado de citas por WhatsApp y la agenda del negocio (sin WhatsApp ni IA)."""
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

import agenda
import db
import ia
import logica
import negocio
from test_flujos import LLMFalso, SalidaFalsa

LIMA = ZoneInfo("America/Lima")
AHORA = datetime(2030, 1, 7, 8, 0, tzinfo=LIMA)  # lunes 7 de enero de 2030, 8:00
TEL = "51911111111"
AGENDA_CFG = {"dias": ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado"], "desde": "09:00",
              "hasta": "18:00", "paso_min": 30, "simultaneas": 1, "anticipacion_horas": 2, "dias_adelante": 14}
SERVICIOS = [{"nombre": "Consulta general", "duracion_min": 30}, {"nombre": "Vacunación", "duracion_min": 30},
             {"nombre": "Baño y corte", "duracion_min": 60}]


def negocio_demo(rubro="veterinaria", servicios=SERVICIOS, agenda_cfg=AGENDA_CFG, **extra):
    return {
        **negocio.PERFILES[rubro], "rubro": rubro, "nombre": "Clínica Demo", "servicios": [s["nombre"] for s in servicios],
        "servicios_detalle": servicios, "horario": "Lunes a sábado, 9:00 a 18:00", "atender_desconocidos": True,
        "ignorar": [], "lineas": {"central": "51900000100", "corporativa": "51900000200"},
        "avisar_completadas": True, "horas_reporte": [], "avisar_citas": True, "seguimiento": {"activo": True, "recordar_min_antes": 15, "gracia_min": 10, "avisos_desde": "07:00", "avisos_hasta": "21:00"},
        "agenda": agenda.configurar(agenda_cfg), **extra,
    }


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        db.DB = f"{self.tmp.name}/t.db"
        db.init()
        with db.conn() as c:
            c.executemany("INSERT INTO personas(telefono, lid, nombre, rol, extras) VALUES(?,?,?,?,?)",
                          [("51900000001", None, "Andrés", "maestro", ""),
                           ("51900000002", None, "Ashly", "trabajador", "atencion"),
                           ("51900000003", None, "Mafer", "trabajador", "")])
        self.neg = negocio_demo()
        self.ahora = AHORA
        for p in (mock.patch.object(negocio, "config", lambda: self.neg),
                  mock.patch.object(logica, "ahora", lambda: self.ahora)):
            p.start()
            self.addCleanup(p.stop)
        self.s = SalidaFalsa("corporativa")
        logica.registrar_desconocido(TEL, None, "Rosa")

    def persona(self, tel=TEL):
        return db.persona_por_id(tel)

    def dice(self, texto, tel=TEL):
        logica.procesar(self.s, self.persona(tel), texto)

    def hablar(self, *mensajes):
        for m in mensajes:
            self.dice(m)

    @property
    def ultima(self):
        return self.s.respuestas[-1]

    def citas(self, estado=None):
        with db.conn() as c:
            filas = [dict(f) for f in c.execute("SELECT * FROM citas ORDER BY id")]
        return [f for f in filas if estado is None or f["estado"] == estado]

    def avisos_a(self, tel):
        return [t for t_, t in self.s.enviados if t_ == tel]

    def cita_directa(self, fecha_hora, nombre="Rosa", mascota="Luna", servicio="Vacunación", dur=30, tel=TEL):
        return db.crear_cita_si_libre(nombre, tel, mascota, servicio, fecha_hora, dur, 1)


class TestNombreDelCliente(Base):
    def test_primer_contacto_pide_el_nombre_y_luego_muestra_el_menu(self):
        self.dice("hola")
        self.assertIn("¿cómo te llamas?", self.ultima)
        self.assertEqual(db.get_estado(self.persona()["id"])["flujo"], "nombre")
        self.dice("rosa pérez")
        self.assertIn("Mucho gusto, Rosa Pérez", self.s.respuestas[-2])
        self.assertIn("1️⃣", self.ultima)  # después del nombre, el menú
        self.assertEqual(self.persona()["nombre"], "Rosa Pérez")
        self.assertTrue(db.get_estado(self.persona()["id"])["nombre_dado"])

    def test_puede_omitir_el_nombre(self):
        self.hablar("hola", "omitir")
        self.assertIn("Sin problema", self.s.respuestas[-2])
        self.assertIn("1️⃣", self.ultima)
        self.assertEqual(self.persona()["nombre"], "Rosa")  # se queda el de WhatsApp
        est = db.get_estado(self.persona()["id"])
        self.assertTrue(est["nombre_ok"])
        self.assertNotIn("nombre_dado", est)

    def test_si_ya_pide_algo_concreto_no_se_le_interrumpe_con_el_nombre(self):
        self.dice("quiero ver los servicios")
        self.assertIn("Servicios:", self.ultima)
        self.assertNotIn("llamas", self.ultima)

    def test_al_agendar_se_pide_el_nombre_si_no_lo_dio(self):
        self.dice("2")
        self.assertIn("necesito tu nombre", self.ultima)
        self.dice("Rosa")
        self.assertIn("¿Qué servicio necesitas?", self.ultima)
        self.assertEqual(self.persona()["nombre"], "Rosa")

    def test_tras_el_nombre_se_atiende_lo_que_escribio_al_inicio(self):
        self.dice("buenas")
        self.dice("Luis")
        self.assertIn("1️⃣", self.ultima)

    def test_nombres_invalidos_no_atrapan_al_cliente(self):
        self.dice("hola")
        self.dice("12345")
        self.assertIn("No te entendí", self.ultima)
        self.dice("???")
        self.assertIn("1️⃣", self.ultima)  # tras dos intentos sigue sin nombre y con el menú
        self.assertTrue(db.get_estado(self.persona()["id"])["nombre_ok"])
        self.dice("hola")
        self.assertNotIn("llamas", self.ultima)  # no se vuelve a preguntar

    def test_palabras_de_menu_no_se_toman_como_nombre(self):
        self.dice("hola")
        self.dice("servicios")
        self.assertNotEqual(self.persona()["nombre"], "Servicios")

    def test_el_equipo_no_recibe_la_pregunta_del_nombre(self):
        self.dice("hola", tel="51900000003")  # Mafer escribiendo como si fuera cliente
        self.assertIn("1️⃣", self.ultima)
        self.assertEqual(db.persona_por_id("51900000003")["nombre"], "Mafer")

    def test_el_nombre_conserva_las_particulas_en_minuscula(self):
        self.hablar("hola", "maría josé de la cruz")
        self.assertEqual(self.persona()["nombre"], "María José de la Cruz")


class TestAgendarCita(Base):
    def listo(self):
        """Cliente que ya dio su nombre."""
        self.hablar("hola", "Rosa")

    def test_flujo_completo_veterinaria(self):
        self.listo()
        self.dice("2")
        self.assertIn("¿Qué servicio necesitas?", self.ultima)
        self.assertIn("3. Baño y corte", self.ultima)
        self.dice("3")
        self.assertIn("¿Qué día te acomoda?", self.ultima)
        for esperado in ("1. Hoy (lun 07/01)", "2. Mañana (mar 08/01)", "3. mié 09/01"):
            self.assertIn(esperado, self.ultima)
        self.dice("2")
        self.assertIn("Mañana (mar 08/01): estos horarios están libres", self.ultima)
        self.assertIn("1. 09:00", self.ultima)
        self.dice("1")
        self.assertIn("¿Cómo se llama la mascota?", self.ultima)
        self.dice("firulais")
        self.assertIn("Esto es lo que voy a agendar", self.ultima)
        for esperado in ("Servicio: Baño y corte", "Mañana (mar 08/01) a las 09:00", "Mascota: Firulais",
                         "A nombre de: Rosa"):
            self.assertIn(esperado, self.ultima)
        self.assertEqual(self.citas(), [])  # nada se guarda antes de confirmar
        self.dice("sí")
        self.assertIn("quedó agendada", self.ultima)
        cita = self.citas()[0]
        self.assertEqual((cita["telefono"], cita["cliente_nombre"], cita["mascota"], cita["servicio"]),
                         (TEL, "Rosa", "Firulais", "Baño y corte"))
        self.assertEqual((cita["fecha_hora"], cita["duracion_min"], cita["origen"], cita["estado"]),
                         ("2030-01-08 09:00", 60, "cliente", "confirmada"))
        self.assertEqual(cita["cliente_id"], self.persona()["id"])
        self.assertEqual([m["nombre"] for m in db.mascotas_de(self.persona()["id"])], ["Firulais"])
        self.assertEqual(db.get_estado(self.persona()["id"]).get("flujo"), None)  # conversación terminada

    def test_quien_atiende_recibe_un_aviso_de_la_cita_nueva(self):
        self.listo()
        self.hablar("2", "3", "2", "1", "firulais", "sí")
        avisos = self.avisos_a("51900000002")  # Ashly tiene el rol atencion
        self.assertEqual(len(avisos), 1)
        for esperado in ("Nueva cita agendada por Pati", "Rosa", "Baño y corte", "Firulais", "2030-01-08 09:00"):
            self.assertIn(esperado, avisos[0])
        self.assertEqual(self.avisos_a("51900000001"), [])  # el dueño no recibe cada cita

    def test_los_avisos_de_citas_se_pueden_apagar(self):
        self.neg["avisar_citas"] = False
        self.listo()
        self.hablar("2", "3", "2", "1", "firulais", "sí")
        self.assertEqual(self.s.enviados, [])
        self.assertEqual(len(self.citas()), 1)

    def test_consultorio_dental_no_pregunta_por_mascota(self):
        self.neg = negocio_demo("dentista")
        self.listo()
        self.hablar("2", "1", "2", "1")
        self.assertIn("Esto es lo que voy a agendar", self.ultima)
        self.assertNotIn("Paciente:", self.ultima)
        self.dice("sí")
        self.assertEqual(self.citas()[0]["mascota"], "Rosa")  # el paciente es quien agenda
        self.assertEqual(db.mascotas_de(self.persona()["id"]), [])

    def test_si_hay_un_solo_servicio_no_lo_pregunta(self):
        self.neg = negocio_demo(servicios=[{"nombre": "Consulta general", "duracion_min": 30}])
        self.listo()
        self.dice("2")
        self.assertIn("¿Qué día te acomoda?", self.ultima)
        self.assertIn("*Consulta general*", self.ultima)

    def test_cliente_con_una_mascota_guardada_confirma_con_si(self):
        db.agregar_mascota(self.persona()["id"], "Luna")
        self.listo()
        self.hablar("2", "1", "2", "1")
        self.assertIn("¿La cita es para *Luna*?", self.ultima)
        self.dice("sí")
        self.assertIn("Mascota: Luna", self.ultima)

    def test_cliente_con_una_mascota_puede_decir_que_es_otra(self):
        db.agregar_mascota(self.persona()["id"], "Luna")
        self.listo()
        self.hablar("2", "1", "2", "1", "no")
        self.assertIn("¿Cómo se llama la mascota?", self.ultima)
        self.dice("Rocky")
        self.assertIn("Mascota: Rocky", self.ultima)
        self.dice("sí")
        self.assertEqual({m["nombre"] for m in db.mascotas_de(self.persona()["id"])}, {"Luna", "Rocky"})

    def test_cliente_con_varias_mascotas_elige_por_numero_o_agrega_otra(self):
        for n in ("Luna", "Max"):
            db.agregar_mascota(self.persona()["id"], n)
        self.listo()
        self.hablar("2", "1", "2", "1")
        self.assertIn("1. Luna", self.ultima)
        self.assertIn("2. Max", self.ultima)
        self.dice("2")
        self.assertIn("Mascota: Max", self.ultima)

    def test_las_mascotas_no_se_duplican_aunque_cambie_la_mayuscula(self):
        db.agregar_mascota(self.persona()["id"], "Luna")
        db.agregar_mascota(self.persona()["id"], "luna")
        self.assertEqual(len(db.mascotas_de(self.persona()["id"])), 1)

    def test_los_horarios_ocupados_no_se_ofrecen(self):
        self.cita_directa("2030-01-08 09:00", dur=60, tel="51922222222", nombre="Otra", mascota="Toby")
        self.listo()
        self.hablar("2", "3", "2")  # baño de 60 min, mañana
        self.assertNotIn("09:00", self.ultima)
        self.assertNotIn("09:30", self.ultima)  # 9:30 a 10:30 chocaría con la cita de 9:00 a 10:00
        self.assertIn("1. 10:00", self.ultima)

    def test_si_otra_persona_ocupa_el_horario_antes_de_confirmar(self):
        self.listo()
        self.hablar("2", "1", "2", "1", "luna")  # servicio 1, mañana 09:00, ya en confirmación
        self.cita_directa("2030-01-08 09:00", tel="51922222222", nombre="Otra", mascota="Toby")
        self.dice("sí")
        self.assertEqual([c["telefono"] for c in self.citas()], ["51922222222"])  # no se duplicó
        self.assertIn("acaba de ocuparse", self.ultima)
        self.assertNotIn("1. 09:00", self.s.respuestas[-2])  # vuelve a ofrecer horarios sin el ocupado
        self.assertEqual(db.get_estado(self.persona()["id"])["paso"], "hora")

    def test_otro_dia_vuelve_a_la_lista_de_dias(self):
        self.listo()
        self.hablar("2", "1", "2", "otro día")
        self.assertIn("¿Qué día te acomoda?", self.ultima)

    def test_se_puede_escribir_una_fecha(self):
        self.listo()
        self.hablar("2", "1", "14/01")
        self.assertIn("lun 14/01: estos horarios están libres", self.ultima)

    def test_se_puede_escribir_una_hora_que_no_estaba_en_la_lista_corta(self):
        self.listo()
        self.hablar("2", "1", "2")  # mañana: 18 horarios, solo se muestran 12
        self.assertIn("o escribe otra hora", self.ultima)
        self.dice("4:30pm")
        self.assertNotIn("No entendí", self.ultima)
        self.assertEqual(db.get_estado(self.persona()["id"])["hora"], "16:30")

    def test_dia_o_hora_invalidos_se_reintentan_y_luego_se_sale(self):
        self.listo()
        self.hablar("2", "1")
        self.dice("el otro mes")
        self.assertIn("No entendí el día", self.ultima)
        self.dice("asdf")
        self.dice("qwerty")
        self.assertIn("1️⃣", self.ultima)  # tras 3 intentos: menú
        self.assertIn("hablar con una persona", self.ultima)
        self.assertIsNone(db.get_estado(self.persona()["id"]).get("flujo"))

    def test_una_hora_no_ofrecida_no_se_acepta(self):
        self.listo()
        self.hablar("2", "1", "2")
        self.dice("3am")
        self.assertIn("Elige una de las horas libres", self.ultima)
        self.assertEqual(self.citas(), [])

    def test_responder_no_en_la_confirmacion_no_agenda_nada(self):
        self.listo()
        self.hablar("2", "1", "2", "1", "luna", "no")
        self.assertIn("no agendé nada", self.ultima)
        self.assertEqual(self.citas(), [])
        self.assertIsNone(db.get_estado(self.persona()["id"]).get("flujo"))

    def test_salidas_en_cualquier_momento(self):
        for salida in ("salir", "menu", "cancelar", "no gracias"):
            with self.subTest(salida=salida):
                self.hablar("2", "1")
                self.dice(salida)
                self.assertIn("lo dejamos así", self.ultima)
                self.assertIn("1️⃣", self.ultima)
                self.assertIsNone(db.get_estado(self.persona()["id"]).get("flujo"))
                db.set_estado(self.persona()["id"], {"nombre_ok": True, "nombre_dado": True})

    def test_pedir_una_persona_en_medio_del_flujo_deriva_a_atencion(self):
        self.listo()
        self.hablar("2", "1")
        self.dice("quiero hablar con una persona")
        self.assertEqual([t for t, _ in self.s.enviados], ["51900000002"])
        self.assertIsNone(db.get_estado(self.persona()["id"]).get("flujo"))

    def test_un_flujo_abandonado_caduca(self):
        self.listo()
        self.hablar("2", "1")
        self.ahora = AHORA + timedelta(hours=logica.FLUJO_CADUCA_HORAS + 1)
        self.dice("1")  # sin flujo, "1" es "ver servicios"
        self.assertIn("Servicios:", self.ultima)

    def test_sin_horarios_libres_se_deriva_a_una_persona(self):
        self.neg = negocio_demo(agenda_cfg={**AGENDA_CFG, "dias_adelante": 1})
        for dia in ("2030-01-07", "2030-01-08"):
            db.crear_cita(f"Otro", "51922222222", "Toby", "Consulta general", f"{dia} 09:00", duracion_min=540)
        self.listo()
        self.hablar("2", "1")
        self.assertIn("no tengo horarios libres", self.ultima)
        self.assertIn("no hay horarios libres", self.avisos_a("51900000002")[0])

    def test_sin_agenda_configurada_no_se_inventan_horarios_y_se_deriva(self):
        self.neg = negocio_demo(agenda_cfg=None)
        self.listo()
        self.dice("2")
        self.assertIn("asesor", self.ultima)
        self.assertEqual(len(self.avisos_a("51900000002")), 1)
        self.assertEqual(self.citas(), [])

    def test_las_respuestas_de_la_conversacion_guiada_no_cuentan_para_el_tope_por_hora(self):
        self.listo()
        self.hablar("2", "3", "2", "1", "firulais", "sí")
        self.assertLessEqual(db.respuestas_recientes(self.persona()["id"], 60, "corporativa"), 1)
        self.assertGreater(db.respuestas_recientes(self.persona()["id"], 60, "corporativa-flujo"), 6)

    def test_la_cita_agendada_recibe_recordatorio_y_las_canceladas_no(self):
        self.listo()
        self.hablar("2", "1", "2", "1", "luna", "sí")
        self.assertEqual(len(db.citas_proximas()), 1)
        db.cancelar_cita(self.citas()[0]["id"], TEL)
        self.assertEqual(db.citas_proximas(), [])

    def test_el_menu_ofrece_mis_citas_solo_si_hay_agenda(self):
        self.listo()
        self.dice("hola")
        self.assertIn("*mis citas*", self.ultima)
        self.neg = negocio_demo(agenda_cfg=None)
        self.dice("hola")
        self.assertNotIn("mis citas", self.ultima)

    def test_el_estado_sobrevive_a_un_reinicio(self):
        import importlib
        self.listo()
        self.hablar("2", "3")
        importlib.reload(logica)  # "reinicio": se pierde la memoria
        with mock.patch.object(logica, "ahora", lambda: self.ahora):
            logica.procesar(self.s, self.persona(), "2")
        self.assertIn("Mañana (mar 08/01): estos horarios están libres", self.ultima)


class TestVerYCancelarCitas(Base):
    def setUp(self):
        super().setUp()
        self.hablar("hola", "Rosa")

    def test_mis_citas(self):
        self.cita_directa("2030-01-08 10:00")
        self.dice("mis citas")
        self.assertIn("Tus próximas citas", self.ultima)
        self.assertIn("Vacunación el Mañana (mar 08/01) a las 10:00", self.ultima)

    def test_mis_citas_sin_citas(self):
        self.dice("mis citas")
        self.assertIn("No tienes citas próximas", self.ultima)

    def test_las_citas_pasadas_no_aparecen(self):
        self.ahora = AHORA + timedelta(days=3)
        self.cita_directa("2030-01-08 10:00")
        self.dice("mis citas")
        self.assertIn("No tienes citas próximas", self.ultima)

    def test_cancelar_una_cita(self):
        self.cita_directa("2030-01-08 10:00")
        self.dice("quiero cancelar mi cita")
        self.assertIn("¿Cancelo tu cita de Vacunación", self.ultima)
        self.dice("sí")
        self.assertIn("Cancelé tu cita", self.ultima)
        self.assertEqual(self.citas()[0]["estado"], "cancelada")
        aviso = self.avisos_a("51900000002")[0]
        self.assertIn("Cita cancelada por el cliente", aviso)
        self.assertIn("2030-01-08 10:00", aviso)

    def test_el_horario_cancelado_vuelve_a_estar_libre(self):
        self.cita_directa("2030-01-08 10:00")
        self.assertNotIn("10:00", agenda.huecos(self.neg["agenda"], datetime(2030, 1, 8).date(), 30,
                                               db.citas_ocupadas("2030-01-08"), AHORA.replace(tzinfo=None)))
        self.hablar("cancelar cita", "sí")
        self.assertIn("10:00", agenda.huecos(self.neg["agenda"], datetime(2030, 1, 8).date(), 30,
                                            db.citas_ocupadas("2030-01-08"), AHORA.replace(tzinfo=None)))

    def test_decir_no_deja_la_cita_en_pie(self):
        self.cita_directa("2030-01-08 10:00")
        self.hablar("cancelar cita", "no")
        self.assertIn("sigue en pie", self.ultima)
        self.assertEqual(self.citas()[0]["estado"], "confirmada")
        self.assertEqual(self.avisos_a("51900000002"), [])

    def test_con_varias_citas_se_elige_cual_cancelar(self):
        self.cita_directa("2030-01-08 10:00", servicio="Vacunación")
        self.cita_directa("2030-01-09 11:00", servicio="Consulta general", mascota="Max")
        self.dice("cancelar cita")
        self.assertIn("1. Vacunación", self.ultima)
        self.assertIn("2. Consulta general", self.ultima)
        self.dice("2")
        self.assertIn("¿Cancelo tu cita de Consulta general", self.ultima)
        self.dice("sí")
        self.assertEqual([c["estado"] for c in self.citas()], ["confirmada", "cancelada"])

    def test_cancelar_sin_citas(self):
        self.dice("cancelar cita")
        self.assertIn("No tienes citas próximas para cancelar", self.ultima)

    def test_reprogramar_cancela_y_empieza_a_agendar_con_el_mismo_servicio(self):
        self.cita_directa("2030-01-08 10:00", servicio="Baño y corte", dur=60)
        self.hablar("quiero reprogramar mi cita", "sí")
        self.assertEqual(self.citas()[0]["estado"], "cancelada")
        self.assertIn("¿Qué día te acomoda?", self.ultima)
        self.assertIn("*Baño y corte*", self.ultima)

    def test_nadie_puede_cancelar_la_cita_de_otro_telefono(self):
        cid = self.cita_directa("2030-01-08 10:00", tel="51922222222", nombre="Otra", mascota="Toby")
        self.assertFalse(db.cancelar_cita(cid, TEL))
        self.assertEqual(self.citas()[0]["estado"], "confirmada")

    def test_sin_agenda_configurada_cancelar_se_deriva(self):
        self.neg = negocio_demo(agenda_cfg=None)
        self.dice("cancelar cita")
        self.assertIn("aviso al equipo", self.ultima)
        self.assertEqual(len(self.avisos_a("51900000002")), 1)

    def test_una_cita_ya_cancelada_no_se_cancela_dos_veces(self):
        cid = self.cita_directa("2030-01-08 10:00")
        self.assertTrue(db.cancelar_cita(cid, TEL))
        self.assertFalse(db.cancelar_cita(cid, TEL))


class TestAgendaParaElDueno(Base):
    """El dueño (y quien atiende) preguntan por la agenda; Pati responde con los datos guardados, sin IA."""

    def setUp(self):
        super().setUp()
        self.llm = LLMFalso()
        p = mock.patch.object(ia, "interpretar_dueno", self.llm)
        p.start()
        self.addCleanup(p.stop)
        self.s = SalidaFalsa("central")
        self.cita_directa("2030-01-07 11:00", mascota="Luna", servicio="Vacunación", dur=30, tel="51933333333")
        self.cita_directa("2030-01-08 09:00", mascota="Max", servicio="Consulta general", tel="51944444444",
                          nombre="Pedro")
        self.cita_directa("2030-01-08 10:00", mascota="Toby", servicio="Baño y corte", dur=60, tel="51955555555",
                          nombre="Ana")
        self.cita_directa("2030-01-11 15:00", mascota="Kira", servicio="Vacunación", tel="51966666666", nombre="Sofía")
        self.dueno = db.persona_por_id("51900000001")

    def pregunta(self, texto, persona=None):
        logica.procesar(self.s, persona or self.dueno, texto)

    def test_citas_de_manana(self):
        self.pregunta("¿qué citas hay mañana?")
        self.assertIn("Citas de mañana", self.ultima)
        self.assertIn("• 09:00 — Max (Pedro) · Consulta general", self.ultima)
        self.assertIn("• 10:00 — Toby (Ana) · Baño y corte", self.ultima)
        self.assertNotIn("Luna", self.ultima)
        self.assertEqual(self.llm.llamadas, [])

    def test_citas_de_hoy(self):
        self.pregunta("citas de hoy")
        self.assertIn("Luna (Rosa) · Vacunación", self.ultima)
        self.assertNotIn("Max", self.ultima)

    def test_sin_fecha_son_hoy_y_manana(self):
        self.pregunta("qué citas hay")
        self.assertIn("de hoy y mañana", self.ultima)
        self.assertIn("Luna", self.ultima)
        self.assertIn("Max", self.ultima)
        self.assertNotIn("Kira", self.ultima)

    def test_la_semana_y_un_dia_de_la_semana(self):
        self.pregunta("qué citas tengo esta semana")
        self.assertIn("Kira", self.ultima)
        self.assertIn("(4)", self.ultima)
        self.pregunta("citas del viernes")
        self.assertIn("Kira", self.ultima)
        self.assertNotIn("Max", self.ultima)

    def test_dia_sin_citas(self):
        self.pregunta("qué citas hay pasado mañana")
        self.assertEqual(self.ultima, "No hay citas agendadas de pasado mañana.")

    def test_las_citas_canceladas_no_aparecen(self):
        db.cancelar_cita(self.citas()[1]["id"], "51944444444")
        self.pregunta("qué citas hay mañana")
        self.assertNotIn("Max", self.ultima)

    def test_en_consultorio_dental_se_muestra_solo_el_nombre_del_paciente(self):
        self.neg = negocio_demo("dentista")
        db.crear_cita("Juan", "51977777777", "Juan", "Limpieza", "2030-01-07 12:00")
        self.pregunta("citas de hoy")
        self.assertIn("• 12:00 — Juan · Limpieza", self.ultima)

    def test_las_ordenes_con_horas_no_se_confunden_con_la_agenda(self):
        for orden in ("cita para mañana a las 10 de luna", "agenda cita para pedro", "agenda una cita para el lunes a las 9"):
            with self.subTest(orden=orden):
                self.llm.poner({"accion": "ninguna", "tipo": "ninguno", "datos": None, "pregunta": None,
                                "respuesta": "ok"})
                antes = len(self.llm.llamadas)
                self.pregunta(orden)
                self.assertEqual(len(self.llm.llamadas), antes + 1)  # llegó a la IA, no a la agenda

    def test_con_una_propuesta_pendiente_se_conserva(self):
        self.llm.poner({"accion": "nueva", "tipo": "tareas", "pregunta": None, "respuesta": None,
                        "datos": {"tareas": [{"responsable": "Mafer", "actividad": "Limpiar", "detalles": None,
                                              "horario": None}]}})
        self.pregunta("mafer limpia")
        self.pregunta("qué citas hay mañana")
        self.assertIn("Sigo esperando tu *SÍ* o *NO*", self.ultima)
        self.pregunta("sí")
        self.assertEqual(len(self.s.enviados), 1)

    def test_quien_atiende_puede_consultar_la_agenda(self):
        self.pregunta("qué citas hay mañana", persona=db.persona_por_id("51900000002"))
        self.assertIn("Max (Pedro)", self.ultima)

    def test_un_trabajador_sin_rol_de_atencion_no_ve_la_agenda(self):
        self.pregunta("qué citas hay mañana", persona=db.persona_por_id("51900000003"))
        self.assertNotIn("Max", self.ultima)
        self.assertNotIn("Pedro", self.ultima)


class TestAyudaConCitas(Base):
    def test_la_ayuda_del_dueno_menciona_la_agenda_y_el_agendado_solo_si_existe(self):
        self.s = SalidaFalsa("central")
        logica.procesar(self.s, db.persona_por_id("51900000001"), "ayuda")
        self.assertIn("*Agenda:*", self.ultima)
        self.assertIn("les agendo citas en los horarios libres", self.ultima)
        self.assertIn("aviso a Ashly de cada cita", self.ultima)
        self.neg = negocio_demo(agenda_cfg=None)
        logica.procesar(self.s, db.persona_por_id("51900000001"), "ayuda")
        self.assertNotIn("les agendo citas", self.ultima)  # sin agenda configurada no promete agendar
        self.assertIn("menú de servicios", self.ultima)

    def test_quien_atiende_ve_funciones_de_citas_y_el_resto_no(self):
        self.s = SalidaFalsa("central")
        logica.procesar(self.s, db.persona_por_id("51900000002"), "qué funciones tienes")  # Ashly: atencion
        self.assertIn("citas hoy", self.ultima)
        self.assertIn("pide hablar con una persona", self.ultima)
        logica.procesar(self.s, db.persona_por_id("51900000003"), "qué funciones tienes")  # Mafer
        self.assertNotIn("citas", self.ultima)


class TestBaseDeCitas(Base):
    def test_citas_adyacentes_si_pero_superpuestas_no(self):
        self.assertIsNotNone(self.cita_directa("2030-01-08 10:00", dur=30))
        self.assertIsNone(self.cita_directa("2030-01-08 10:00", dur=30))  # mismo horario
        self.assertIsNone(self.cita_directa("2030-01-08 09:45", dur=30))  # se pisa los últimos 15 min
        self.assertIsNotNone(self.cita_directa("2030-01-08 10:30", dur=30))  # justo cuando termina la otra
        self.assertIsNotNone(self.cita_directa("2030-01-08 09:30", dur=30))  # termina justo cuando empieza otra

    def test_con_dos_simultaneas_caben_dos(self):
        args = ("Rosa", TEL, "Luna", "Vacunación", "2030-01-08 10:00", 30, 2)
        self.assertIsNotNone(db.crear_cita_si_libre(*args))
        self.assertIsNotNone(db.crear_cita_si_libre(*args))
        self.assertIsNone(db.crear_cita_si_libre(*args))

    def test_una_cita_cancelada_libera_el_horario(self):
        cid = self.cita_directa("2030-01-08 10:00")
        db.cancelar_cita(cid, TEL)
        self.assertIsNotNone(self.cita_directa("2030-01-08 10:00"))

    def test_citas_ocupadas_en_minutos(self):
        self.cita_directa("2030-01-08 10:00", dur=60)
        self.assertEqual(db.citas_ocupadas("2030-01-08"), [(600, 660)])
        self.assertEqual(db.citas_ocupadas("2030-01-09"), [])

    def test_las_citas_creadas_por_el_dueno_tambien_bloquean_horarios(self):
        db.crear_cita("Pedro", "51900000009", "Max", "Vacunación", "2030-01-08 10:00")  # duración 30 por defecto
        self.assertIsNone(self.cita_directa("2030-01-08 10:00"))

    def test_una_base_antigua_de_citas_se_migra(self):
        db.DB = f"{self.tmp.name}/vieja.db"
        c = sqlite3.connect(db.DB)
        c.executescript("""
            CREATE TABLE citas(id INTEGER PRIMARY KEY AUTOINCREMENT, cliente_nombre TEXT NOT NULL, telefono TEXT NOT NULL,
                mascota TEXT NOT NULL, servicio TEXT NOT NULL, fecha_hora TEXT NOT NULL,
                recordatorio_enviado INTEGER NOT NULL DEFAULT 0, creada_en TEXT NOT NULL DEFAULT (datetime('now','localtime')));
            INSERT INTO citas(cliente_nombre, telefono, mascota, servicio, fecha_hora)
                VALUES('Vieja', '51900000009', 'Max', 'Vacuna', '2030-01-08 10:00');
        """)
        c.commit()
        c.close()
        db.init()
        db.init()
        with db.conn() as c:
            fila = c.execute("SELECT * FROM citas").fetchone()
        self.assertEqual((fila["duracion_min"], fila["estado"], fila["origen"]), (30, "confirmada", "dueno"))
        self.assertEqual(len(db.citas_proximas()), 1)


class TestConfiguracionDeAgenda(unittest.TestCase):
    def config_con(self, contenido):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            with open(f"{d}/negocio.json", "w", encoding="utf-8") as f:
                f.write(contenido)
            with mock.patch.object(db, "DATA_DIR", d):
                return negocio.config()

    def test_servicios_como_texto_o_con_duracion(self):
        c = self.config_con('{"servicios": ["Consulta", {"nombre": "Baño", "duracion_min": 60}, {"nombre": "X", '
                            '"duracion_min": "mucho"}, {"sin_nombre": 1}, 5, ""]}')
        self.assertEqual(c["servicios"], ["Consulta", "Baño", "X"])
        self.assertEqual([s["duracion_min"] for s in c["servicios_detalle"]], [30, 60, 30])

    def test_sin_agenda_no_se_agenda_sola(self):
        c = self.config_con('{"servicios": ["Consulta"]}')
        self.assertIsNone(c["agenda"])
        self.assertTrue(c["avisar_citas"])

    def test_agenda_valida_y_aviso_apagado(self):
        c = self.config_con('{"agenda": {"dias": ["lunes"], "desde": "09:00", "hasta": "12:00"}, "avisar_citas": false}')
        self.assertEqual(c["agenda"]["dias"], [0])
        self.assertFalse(c["avisar_citas"])

    def test_agenda_rota_se_ignora(self):
        self.assertIsNone(self.config_con('{"agenda": {"dias": "lunes"}}')["agenda"])


if __name__ == "__main__":
    unittest.main()
