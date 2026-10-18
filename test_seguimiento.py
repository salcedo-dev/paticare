"""Pruebas de plazos y seguimiento automático de tareas (recordatorios, vencidas, aviso al dueño, "más tiempo")."""
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

import db
import ia
import logica
import negocio
from test_flujos import LLMFalso, SalidaFalsa, tareas

LIMA = ZoneInfo("America/Lima")
AHORA = datetime(2030, 1, 7, 8, 0, tzinfo=LIMA)  # lunes 7 de enero de 2030, 8:00
SEG = {"activo": True, "recordar_min_antes": 15, "gracia_min": 10, "avisos_desde": "07:00", "avisos_hasta": "21:00"}
DUENO, ASHLY, MAFER = "51900000001", "51900000002", "51900000003"


def hora(minutos_desde_ahora):
    """Plazo "YYYY-MM-DD HH:MM" a tantos minutos del AHORA de las pruebas."""
    return (AHORA.replace(tzinfo=None) + timedelta(minutes=minutos_desde_ahora)).strftime("%Y-%m-%d %H:%M")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        db.DB = f"{self.tmp.name}/t.db"
        db.init()
        with db.conn() as c:
            c.executemany("INSERT INTO personas(telefono, lid, nombre, rol, extras) VALUES(?,?,?,?,?)",
                          [(DUENO, None, "Andrés", "maestro", ""), (ASHLY, None, "Ashly", "trabajador", "atencion"),
                           (MAFER, None, "Mafer", "trabajador", "")])
        self.neg = {**negocio.PERFILES["veterinaria"], "rubro": "veterinaria", "nombre": "Clínica Demo", "servicios": [],
                    "servicios_detalle": [], "horario": None, "atender_desconocidos": True, "ignorar": [],
                    "lineas": {}, "avisar_completadas": True, "horas_reporte": [], "avisar_citas": True, "agenda": None,
                    "seguimiento": dict(SEG)}
        self.ahora = AHORA
        for p in (mock.patch.object(negocio, "config", lambda: self.neg),
                  mock.patch.object(logica, "ahora", lambda: self.ahora)):
            p.start()
            self.addCleanup(p.stop)
        self.s = SalidaFalsa("central")
        self.llm = LLMFalso()
        p = mock.patch.object(ia, "interpretar_dueno", self.llm)
        p.start()
        self.addCleanup(p.stop)
        self.mafer, self.ashly, self.dueno = (db.persona_por_id(t) for t in (MAFER, ASHLY, DUENO))

    def tarea(self, persona, actividad, vence=None, **cols):
        tid = db.crear_tarea(persona["id"], actividad, None, None, vence)
        for k, v in cols.items():
            with db.conn() as c:
                c.execute(f"UPDATE tareas SET {k}=? WHERE id=?", (v, tid))
        return tid

    def fila(self, tid):
        with db.conn() as c:
            return dict(c.execute("SELECT * FROM tareas WHERE id=?", (tid,)).fetchone())

    def avisos_a(self, tel):
        return [t for t_, t in self.s.enviados if t_ == tel]

    def correr(self, minutos=0):
        self.ahora = AHORA + timedelta(minutes=minutos)
        return logica.ejecutar_seguimiento(self.s)

    def dice(self, persona, texto):
        logica.procesar(self.s, persona, texto)

    @property
    def ultima(self):
        return self.s.respuestas[-1]


class TestRecordatorios(Base):
    def test_recordatorio_antes_de_que_venza(self):
        tid = self.tarea(self.mafer, "Limpiar quirófano", hora(10))
        self.assertEqual(self.correr(), [("previo", tid)])
        aviso = self.avisos_a(MAFER)[0]
        self.assertIn("En 10 min vence: *Limpiar quirófano*", aviso)
        self.assertIn("*más tiempo*", aviso)
        self.assertEqual(self.fila(tid)["rec_previo"], 1)

    def test_todavia_falta_mucho_no_se_avisa(self):
        self.tarea(self.mafer, "A", hora(60))
        self.assertEqual(self.correr(), [])
        self.assertEqual(self.s.enviados, [])

    def test_el_recordatorio_previo_no_se_repite(self):
        self.tarea(self.mafer, "A", hora(10))
        self.correr()
        self.correr(1)
        self.correr(5)
        self.assertEqual(len(self.avisos_a(MAFER)), 1)

    def test_aviso_al_vencer_y_guarda_a_que_tarea_se_refiere(self):
        tid = self.tarea(self.mafer, "Limpiar", hora(-10))
        self.assertEqual(self.correr(), [("vencida", tid)])
        aviso = self.avisos_a(MAFER)[0]
        self.assertIn("Ya pasó la hora de *Limpiar*", aviso)
        self.assertIn("¿La hiciste?", aviso)
        self.assertEqual(db.get_estado(self.mafer["id"])["seguimiento_tarea_id"], tid)
        f = self.fila(tid)
        self.assertEqual((f["rec_previo"], f["aviso_dueno"]), (1, 0))  # ya no hace falta el recordatorio previo
        self.assertIsNotNone(f["rec_vencida_en"])

    def test_el_aviso_de_vencida_no_se_repite(self):
        self.tarea(self.mafer, "A", hora(-10))
        self.correr()
        self.correr(1)
        self.correr(2)
        self.assertEqual(len(self.avisos_a(MAFER)), 1)

    def test_secuencia_completa_previo_vencida_y_escalada_una_sola_vez(self):
        tid = self.tarea(self.mafer, "Limpiar", hora(10))
        self.assertEqual(self.correr(0), [("previo", tid)])
        self.assertEqual(self.correr(5), [])
        self.assertEqual(self.correr(10), [("vencida", tid)])
        self.assertEqual(self.correr(19), [])  # dentro de la gracia (10 min)
        self.assertEqual(self.avisos_a(DUENO), [])
        self.assertEqual(self.correr(20), [("escalada", tid)])
        aviso = self.avisos_a(DUENO)[0]
        self.assertIn("🔴 *Mafer* no completó a tiempo: Limpiar", aviso)
        self.assertIn("Vencía hoy 08:10", aviso)
        self.assertEqual(self.correr(40), [])  # nunca más
        self.assertEqual(len(self.avisos_a(DUENO)), 1)
        self.assertEqual(len(self.avisos_a(MAFER)), 2)  # solo el previo y el de vencida

    def test_si_la_completa_antes_de_la_gracia_no_hay_aviso_al_dueno(self):
        tid = self.tarea(self.mafer, "Limpiar", hora(-5))
        self.correr()
        self.ahora = AHORA + timedelta(minutes=5)
        self.dice(self.mafer, "listo")
        self.s.enviados.clear()
        self.correr(30)
        self.assertEqual(self.fila(tid)["estado"], "completada")
        self.assertEqual(self.avisos_a(DUENO), [])

    def test_el_dueno_no_se_escala_a_si_mismo(self):
        with db.conn() as c:
            c.execute("UPDATE personas SET extras='trabajador' WHERE nombre='Andrés'")
        self.dueno = db.persona_por_id(DUENO)
        tid = self.tarea(self.dueno, "Revisar caja", hora(-10))
        self.assertEqual(self.correr(), [("vencida", tid)])
        self.assertEqual(self.correr(15), [("escalada", tid)])  # se marca, pero no se manda nada
        self.assertEqual(len(self.avisos_a(DUENO)), 1)  # solo el aviso de vencida (su propia tarea)
        self.assertIn("Ya pasó la hora", self.avisos_a(DUENO)[0])

    def test_tareas_sin_plazo_no_se_vigilan(self):
        self.tarea(self.mafer, "Sin plazo")
        self.assertEqual(self.correr(500), [])

    def test_tareas_completadas_canceladas_o_con_incidencia_no_se_vigilan(self):
        self.tarea(self.mafer, "Hecha", hora(-30), estado="completada")
        self.tarea(self.mafer, "Cancelada", hora(-30), estado="cancelada")
        self.tarea(self.mafer, "Con problema", hora(-30), estado="con_incidencia")
        self.assertEqual(self.correr(), [])
        self.assertEqual(self.s.enviados, [])

    def test_cada_tarea_se_vigila_por_separado(self):
        t1 = self.tarea(self.mafer, "A", hora(10))
        t2 = self.tarea(self.ashly, "B", hora(-10))
        self.assertEqual(sorted(self.correr()), sorted([("previo", t1), ("vencida", t2)]))
        self.assertEqual(len(self.avisos_a(MAFER)), 1)
        self.assertEqual(len(self.avisos_a(ASHLY)), 1)


class TestVentanaDeAvisos(Base):
    def correr_a(self, h, m, dia=7):
        self.ahora = datetime(2030, 1, dia, h, m, tzinfo=LIMA)
        return logica.ejecutar_seguimiento(self.s)

    def test_no_se_avisa_de_noche_y_se_avisa_al_amanecer(self):
        tid = self.tarea(self.mafer, "A", "2030-01-07 19:00")  # venció a las 7 p. m.
        self.assertEqual(self.correr_a(22, 0), [])  # 10 p. m.: fuera de la ventana
        self.assertEqual(self.correr_a(3, 0, dia=8), [])  # madrugada
        self.assertEqual(self.correr_a(6, 59, dia=8), [])
        self.assertEqual(self.correr_a(7, 0, dia=8), [("vencida", tid)])  # apenas abre la ventana
        self.assertEqual(len(self.avisos_a(MAFER)), 1)

    def test_limites_exactos_de_la_ventana(self):
        tid = self.tarea(self.mafer, "A", "2030-01-06 12:00")  # venció ayer
        self.assertEqual(self.correr_a(20, 59), [("vencida", tid)])  # 20:59 todavía es de día
        t2 = self.tarea(self.ashly, "B", "2030-01-06 12:00")
        self.assertEqual(self.correr_a(21, 0), [])  # 21:00 ya no
        self.assertEqual(self.fila(t2)["rec_vencida_en"], None)

    def test_la_ventana_se_puede_configurar(self):
        self.neg["seguimiento"].update(avisos_desde="09:00", avisos_hasta="10:00")
        self.tarea(self.mafer, "A", hora(-10))
        self.assertEqual(self.correr(0), [])  # 08:00 está fuera de la ventana
        self.assertEqual(len(self.correr(61)), 1)  # 09:01

    def test_se_puede_apagar_el_seguimiento(self):
        self.neg["seguimiento"]["activo"] = False
        self.tarea(self.mafer, "A", hora(-10))
        self.assertEqual(self.correr(), [])

    def test_los_minutos_configurables(self):
        self.neg["seguimiento"].update(recordar_min_antes=30, gracia_min=0)
        tid = self.tarea(self.mafer, "A", hora(25))
        self.assertEqual(self.correr(), [("previo", tid)])
        self.assertEqual(self.correr(25), [("vencida", tid)])
        self.assertEqual(self.correr(26), [("escalada", tid)])  # sin gracia: al minuto siguiente

    def test_un_fallo_de_envio_no_marca_el_aviso_y_se_reintenta(self):
        t1 = self.tarea(self.mafer, "A", hora(10))
        t2 = self.tarea(self.ashly, "B", hora(10))
        original = self.s.enviar

        def falla_con_mafer(tel, texto):
            if tel == MAFER:
                raise RuntimeError("sin red")
            original(tel, texto)

        self.s.enviar = falla_con_mafer
        self.assertEqual(self.correr(), [("previo", t2)])  # la de Ashly sí salió
        self.assertEqual(self.fila(t1)["rec_previo"], 0)  # la de Mafer queda pendiente
        self.s.enviar = original
        self.assertEqual(self.correr(1), [("previo", t1)])  # se reintenta al minuto siguiente
        self.assertEqual(len(self.avisos_a(MAFER)), 1)


class TestMasTiempo(Base):
    def vencida(self, nombre="Limpiar", persona=None):
        persona = persona or self.mafer
        tid = self.tarea(persona, nombre, hora(-10))
        self.correr()
        self.s.enviados.clear()
        self.s.respuestas.clear()
        return tid

    def test_pide_mas_tiempo_y_da_una_hora(self):
        tid = self.vencida()
        self.dice(self.mafer, "necesito más tiempo")
        self.assertIn("¿Para qué hora terminas *Limpiar*?", self.ultima)
        self.dice(self.mafer, "6pm")
        self.assertIn("nuevo plazo: *hoy 18:00*", self.ultima)
        f = self.fila(tid)
        self.assertEqual((f["vence_en"], f["rec_previo"], f["aviso_dueno"]), ("2030-01-07 18:00", 0, 0))
        self.assertIsNone(f["rec_vencida_en"])  # los avisos se reinician
        aviso = self.avisos_a(DUENO)[0]
        self.assertIn("⏳ *Mafer* pidió más tiempo: Limpiar", aviso)
        self.assertIn("Nuevo plazo: hoy 18:00", aviso)

    def test_despues_de_pedir_mas_tiempo_vuelve_a_recordarse_y_ya_no_se_escala(self):
        tid = self.vencida()
        self.hablar_plazo("en 30 minutos")
        self.assertEqual(self.fila(tid)["vence_en"], "2030-01-07 08:30")
        self.s.enviados.clear()
        self.assertEqual(self.correr(20), [("previo", tid)])  # 10 min antes del nuevo plazo
        self.assertEqual(self.correr(40), [("vencida", tid)])
        self.assertEqual(self.avisos_a(DUENO), [])  # el dueño no recibe el aviso viejo

    def hablar_plazo(self, texto):
        self.dice(self.mafer, "más tiempo")
        self.dice(self.mafer, texto)

    def test_formas_de_pedir_mas_tiempo(self):
        for frase in ("más tiempo", "necesito mas tiempo", "me demoro un poco", "dame más tiempo", "me atraso"):
            with self.subTest(frase=frase):
                self.setUp()
                self.tarea(self.mafer, "A", hora(-10))
                self.dice(self.mafer, frase)
                self.assertIn("¿Para qué hora terminas", self.ultima)

    def test_con_varias_tareas_pregunta_cual(self):
        self.tarea(self.mafer, "A", hora(60))
        t2 = self.tarea(self.mafer, "B", hora(120))
        self.dice(self.mafer, "más tiempo")
        self.assertIn("¿Para cuál tarea necesitas más tiempo?", self.ultima)
        self.assertIn("2. B", self.ultima)
        self.dice(self.mafer, "2")
        self.assertIn("¿Para qué hora terminas *B*?", self.ultima)
        self.dice(self.mafer, "7pm")
        self.assertEqual(self.fila(t2)["vence_en"], "2030-01-07 19:00")

    def test_con_varias_tareas_se_refiere_a_la_que_le_recordaron(self):
        self.tarea(self.mafer, "A", hora(60))
        t2 = self.tarea(self.mafer, "B", hora(-10))
        self.correr()
        self.dice(self.mafer, "más tiempo")
        self.assertIn("¿Para qué hora terminas *B*?", self.ultima)  # sin preguntar cuál
        self.dice(self.mafer, "20:00")
        self.assertEqual(self.fila(t2)["vence_en"], "2030-01-07 20:00")

    def test_sin_tareas_pendientes(self):
        self.dice(self.mafer, "más tiempo")
        self.assertIn("No tienes tareas pendientes", self.ultima)

    def test_hora_incomprensible_se_reintenta_y_luego_se_suelta(self):
        tid = self.vencida()
        self.dice(self.mafer, "más tiempo")
        self.dice(self.mafer, "cuando pueda")
        self.assertIn("No entendí la hora", self.ultima)
        self.dice(self.mafer, "no sé")
        self.assertIn("No logré entender la hora", self.ultima)
        self.assertEqual(self.fila(tid)["vence_en"], hora(-10))  # sin cambios
        self.assertEqual(self.avisos_a(DUENO), [])
        self.assertNotIn("esperando", db.get_estado(self.mafer["id"]))

    def test_decir_no_deja_el_plazo_como_estaba(self):
        tid = self.vencida()
        self.dice(self.mafer, "más tiempo")
        self.dice(self.mafer, "no")
        self.assertIn("dejo el plazo", self.ultima)
        self.assertEqual(self.fila(tid)["vence_en"], hora(-10))

    def test_una_hora_que_ya_paso_hoy_se_entiende_como_manana(self):
        tid = self.vencida()
        self.hablar_plazo("7am")
        self.assertEqual(self.fila(tid)["vence_en"], "2030-01-08 07:00")

    def test_manana_a_una_hora(self):
        tid = self.vencida()
        self.hablar_plazo("mañana a las 10")
        self.assertEqual(self.fila(tid)["vence_en"], "2030-01-08 10:00")

    def test_el_dueno_con_tareas_propias_tambien_puede_pedir_mas_tiempo(self):
        with db.conn() as c:
            c.execute("UPDATE personas SET extras='trabajador' WHERE nombre='Andrés'")
        self.dueno = db.persona_por_id(DUENO)
        tid = self.tarea(self.dueno, "Revisar caja", hora(-10))
        self.correr()
        self.s.respuestas.clear()
        self.dice(self.dueno, "necesito más tiempo")
        self.assertIn("¿Para qué hora terminas *Revisar caja*?", self.ultima)
        self.dice(self.dueno, "6pm")
        self.assertEqual(self.fila(tid)["vence_en"], "2030-01-07 18:00")
        self.assertEqual(self.llm.llamadas, [])  # sin IA ni aviso a sí mismo

    def test_no_se_puede_cambiar_el_plazo_de_la_tarea_de_otro(self):
        tid = self.tarea(self.ashly, "De Ashly", hora(60))
        self.assertFalse(db.cambiar_vence(tid, self.mafer["id"], "2030-01-09 10:00"))
        self.assertEqual(self.fila(tid)["vence_en"], hora(60))

    def test_no_se_cambia_el_plazo_de_una_tarea_completada(self):
        tid = self.tarea(self.mafer, "Hecha", hora(60), estado="completada")
        self.assertFalse(db.cambiar_vence(tid, self.mafer["id"], "2030-01-09 10:00"))

    def test_responder_listo_tras_el_aviso_completa_y_avisa_al_dueno(self):
        tid = self.vencida()
        self.dice(self.mafer, "listo")
        self.assertEqual(self.fila(tid)["estado"], "completada")
        self.assertIn("completó: Limpiar", self.avisos_a(DUENO)[0])

    def test_parse_plazo(self):
        ahora_n = AHORA.replace(tzinfo=None)
        casos = {
            "en 30 minutos": datetime(2030, 1, 7, 8, 30), "en 30 min": datetime(2030, 1, 7, 8, 30),
            "en una hora": datetime(2030, 1, 7, 9, 0), "en 2 horas": datetime(2030, 1, 7, 10, 0),
            "en media hora": datetime(2030, 1, 7, 8, 30), "6pm": datetime(2030, 1, 7, 18, 0),
            "a las 6": datetime(2030, 1, 7, 18, 0), "18:30": datetime(2030, 1, 7, 18, 30),
            "para las 9:15 am": datetime(2030, 1, 7, 9, 15), "mañana a las 10": datetime(2030, 1, 8, 10, 0),
            "7am": datetime(2030, 1, 8, 7, 0),
        }
        for texto, esperado in casos.items():
            with self.subTest(texto=texto):
                self.assertEqual(logica._parse_plazo(texto, ahora_n), esperado)
        for malo in ("asdf", "cuando pueda", "", "en 0 minutos", "en 99 horas"):
            with self.subTest(malo=malo):
                self.assertIsNone(logica._parse_plazo(malo, ahora_n))


class TestPlazosEnTareas(Base):
    def test_etiquetas(self):
        hoy = AHORA.date()
        self.assertEqual(logica.etiqueta_plazo("2030-01-07 17:00", hoy), "hoy 17:00")
        self.assertEqual(logica.etiqueta_plazo("2030-01-08 10:05", hoy), "mañana 10:05")
        self.assertEqual(logica.etiqueta_plazo("2030-01-06 09:00", hoy), "ayer 09:00")
        self.assertEqual(logica.etiqueta_plazo("2030-01-11 16:00", hoy), "vie 11/01 16:00")

    def test_vence_o_vencio(self):
        self.assertEqual(logica.texto_plazo("2030-01-07 17:00"), "vence hoy 17:00")
        self.assertEqual(logica.texto_plazo("2030-01-07 07:00"), "venció hoy 07:00")

    def test_el_dueno_ve_el_plazo_en_la_propuesta_y_el_trabajador_en_su_tarea(self):
        self.llm.poner(tareas("nueva", ("Mafer", "Limpiar", None, "para las 5")))
        self.llm.cola[0]["datos"]["tareas"][0]["vence"] = "2030-01-07 17:00"
        self.dice(self.dueno, "mafer limpia para las 5")
        self.assertIn("1. Mafer: Limpiar (⏰ hoy 17:00)", self.ultima)
        self.dice(self.dueno, "sí")
        self.assertEqual(self.fila(1)["vence_en"], "2030-01-07 17:00")
        self.assertIn("⏰ hoy 17:00", self.avisos_a(MAFER)[0])
        self.assertNotIn("🕒", self.avisos_a(MAFER)[0])

    def test_sin_plazo_se_muestra_el_horario_como_texto(self):
        self.llm.poner(tareas("nueva", ("Mafer", "Atender", None, "de 8 a 12")))
        self.dice(self.dueno, "mafer atiende de 8 a 12")
        self.assertIn("(de 8 a 12)", self.ultima)
        self.assertNotIn("⏰", self.ultima)
        self.dice(self.dueno, "sí")
        self.assertIn("🕒 de 8 a 12", self.avisos_a(MAFER)[0])
        self.assertIsNone(self.fila(1)["vence_en"])

    def test_un_plazo_que_ya_paso_se_descarta(self):
        self.llm.poner(tareas("nueva", ("Mafer", "Limpiar", None, "para las 7")))
        self.llm.cola[0]["datos"]["tareas"][0]["vence"] = "2030-01-07 07:00"  # antes de AHORA (08:00)
        self.dice(self.dueno, "mafer limpia para las 7")
        self.assertNotIn("⏰", self.ultima)
        self.dice(self.dueno, "sí")
        self.assertIsNone(self.fila(1)["vence_en"])

    def test_una_correccion_actualiza_el_plazo(self):
        self.llm.poner(tareas("nueva", ("Mafer", "Limpiar", None, "para las 5")))
        self.llm.cola[0]["datos"]["tareas"][0]["vence"] = "2030-01-07 17:00"
        self.dice(self.dueno, "mafer limpia para las 5")
        self.llm.poner(tareas("corregir", ("Mafer", "Limpiar", None, "para las 6")))
        self.llm.cola[0]["datos"]["tareas"][0]["vence"] = "2030-01-07 18:00"
        self.dice(self.dueno, "mejor para las 6")
        self.assertIn("(⏰ hoy 18:00)", self.ultima)
        self.dice(self.dueno, "sí")
        self.assertEqual(self.fila(1)["vence_en"], "2030-01-07 18:00")

    def test_la_lista_de_pendientes_muestra_el_plazo_y_marca_las_vencidas(self):
        self.tarea(self.mafer, "Futura", hora(120))
        self.tarea(self.mafer, "Vencida", hora(-30))
        self.tarea(self.mafer, "Sin plazo")
        self.dice(self.mafer, "qué tengo pendiente")
        self.assertIn("Futura · vence hoy 10:00", self.ultima)
        self.assertIn("Vencida · venció hoy 07:30", self.ultima)
        self.assertIn("3. Sin plazo", self.ultima)

    def test_el_reporte_separa_las_atrasadas(self):
        self.tarea(self.mafer, "Futura", hora(120))
        self.tarea(self.mafer, "Atrasada", hora(-30))
        self.tarea(self.ashly, "Sin plazo")
        texto = logica.texto_reporte_estado()
        self.assertIn("🔴 *Atrasadas* (1)", texto)
        self.assertIn("• Mafer: Atrasada (venció hoy 07:30)", texto)
        self.assertIn("⏳ *Pendientes* (2)", texto)
        self.assertIn("• Mafer: Futura (vence hoy 10:00)", texto)
        self.assertIn("• Ashly: Sin plazo", texto)
        self.assertLess(texto.index("Atrasadas"), texto.index("Pendientes"))

    def test_el_resumen_diario_incluye_las_atrasadas(self):
        self.tarea(self.mafer, "Atrasada", hora(-30))
        texto = logica.texto_resumen_diario()
        self.assertIn("🔴 *Atrasadas:*", texto)
        self.assertIn("Mafer: Atrasada (venció hoy 07:30)", texto)
        self.assertNotIn("Todo en orden", texto)

    def test_sin_atrasadas_el_resumen_sigue_diciendo_todo_en_orden(self):
        self.tarea(self.mafer, "A", hora(120))
        self.assertIn("Todo en orden", logica.texto_resumen_diario())

    def test_las_ayudas_mencionan_los_plazos(self):
        self.dice(self.mafer, "ayuda")
        self.assertIn("*más tiempo*", self.ultima)
        self.assertIn("*más tiempo*", self.ultima)
        self.dice(self.dueno, "ayuda")
        self.assertIn("*Plazos:*", self.ultima)


class TestBaseDeDatos(Base):
    def test_una_base_antigua_de_tareas_se_migra_sin_perder_datos(self):
        db.DB = f"{self.tmp.name}/vieja.db"
        c = sqlite3.connect(db.DB)
        c.executescript("""
            CREATE TABLE tareas(id INTEGER PRIMARY KEY AUTOINCREMENT, responsable_id INTEGER NOT NULL, actividad TEXT NOT NULL,
                detalles TEXT, horario TEXT, estado TEXT NOT NULL DEFAULT 'pendiente',
                creada_en TEXT NOT NULL DEFAULT (datetime('now','localtime')), completada_en TEXT);
            INSERT INTO tareas(responsable_id, actividad, horario) VALUES(1, 'Vieja', 'a las 5');
        """)
        c.commit()
        c.close()
        db.init()
        db.init()
        with db.conn() as c:
            f = c.execute("SELECT * FROM tareas").fetchone()
        self.assertEqual((f["actividad"], f["horario"], f["vence_en"], f["rec_previo"], f["aviso_dueno"]),
                         ("Vieja", "a las 5", None, 0, 0))

    def test_marcar_seguimiento_solo_acepta_campos_conocidos(self):
        tid = self.tarea(self.mafer, "A", hora(5))
        with self.assertRaises(ValueError):
            db.marcar_seguimiento(tid, "estado; DROP TABLE tareas", 1)

    def test_tareas_con_plazo_trae_los_datos_del_responsable(self):
        self.tarea(self.mafer, "A", hora(5))
        self.tarea(self.mafer, "Sin plazo")
        filas = db.tareas_con_plazo()
        self.assertEqual([(f["actividad"], f["resp_nombre"], f["resp_telefono"]) for f in filas],
                         [("A", "Mafer", MAFER)])


class TestConfiguracion(unittest.TestCase):
    def config_con(self, contenido):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            with open(f"{d}/negocio.json", "w", encoding="utf-8") as f:
                f.write(contenido)
            with mock.patch.object(db, "DATA_DIR", d):
                return negocio.config()["seguimiento"]

    def test_valores_por_defecto(self):
        self.assertEqual(self.config_con("{}"), SEG)

    def test_valores_validos(self):
        c = self.config_con('{"seguimiento": {"activo": false, "recordar_min_antes": 30, "gracia_min": 5, '
                            '"avisos_desde": "08:30", "avisos_hasta": "20:00"}}')
        self.assertEqual(c, {"activo": False, "recordar_min_antes": 30, "gracia_min": 5,
                             "avisos_desde": "08:30", "avisos_hasta": "20:00"})

    def test_valores_invalidos_caen_en_los_de_defecto_o_se_acotan(self):
        c = self.config_con('{"seguimiento": {"recordar_min_antes": "mucho", "gracia_min": -5, "avisos_desde": "tarde", '
                            '"avisos_hasta": 99}}')
        self.assertEqual((c["recordar_min_antes"], c["gracia_min"], c["avisos_desde"], c["avisos_hasta"]),
                         (15, 0, "07:00", "21:00"))

    def test_seguimiento_que_no_es_un_objeto_se_ignora(self):
        self.assertEqual(self.config_con('{"seguimiento": "si"}'), SEG)


if __name__ == "__main__":
    unittest.main()
