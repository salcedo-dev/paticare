"""Pruebas de los botones de WhatsApp en los avisos al equipo (lógica, sin WhatsApp)."""
from datetime import timedelta

import db
import logica
from test_flujos import tareas
from test_seguimiento import AHORA, ASHLY, DUENO, MAFER, Base, hora


class BaseBotones(Base):
    def setUp(self):
        super().setUp()
        self.neg["botones"] = True


class TestMensajesConBotones(BaseBotones):
    def test_la_tarea_nueva_llega_con_tres_botones_y_sin_instrucciones(self):
        self.llm.poner(tareas("nueva", ("Mafer", "Limpiar", None, "para las 5")))
        self.llm.cola[0]["datos"]["tareas"][0]["vence"] = "2030-01-07 17:00"
        self.dice(self.dueno, "mafer limpia para las 5")
        self.dice(self.dueno, "sí")
        tid = db.tareas_pendientes(self.mafer["id"])[0]["id"]
        self.assertEqual(self.s.botones_enviados[0],
                         [("✅ Listo", f"listo:{tid}"), ("⚠️ Problema", f"problema:{tid}"), ("⏳ Más tiempo", f"mas:{tid}")])
        self.assertNotIn("Responde", self.avisos_a(MAFER)[0])  # los botones ya lo dicen

    def test_sin_plazo_no_hay_boton_de_mas_tiempo(self):
        self.llm.poner(tareas("nueva", ("Mafer", "Limpiar", None, None)))
        self.dice(self.dueno, "mafer limpia")
        self.dice(self.dueno, "sí")
        self.assertEqual([e for e, _ in self.s.botones_enviados[0]], ["✅ Listo", "⚠️ Problema"])

    def test_con_botones_apagados_todo_sigue_como_texto(self):
        self.neg["botones"] = False
        self.llm.poner(tareas("nueva", ("Mafer", "Limpiar", None, None)))
        self.dice(self.dueno, "mafer limpia")
        self.dice(self.dueno, "sí")
        self.assertEqual(self.s.botones_enviados, [None])
        self.assertIn("Responde ✅ *listo*", self.avisos_a(MAFER)[0])

    def test_recordatorio_y_aviso_de_vencida_traen_botones(self):
        a = self.tarea(self.mafer, "Limpiar", hora(10))
        self.correr()
        self.assertEqual(self.s.botones_enviados[-1], [("✅ Listo", f"listo:{a}"), ("⏳ Más tiempo", f"mas:{a}")])
        b = self.tarea(self.ashly, "Caja", hora(-10))
        self.correr()
        self.assertEqual([e for e, _ in self.s.botones_enviados[-1]], ["✅ Listo", "⚠️ Problema", "⏳ Más tiempo"])
        self.assertNotIn("listo", self.avisos_a(ASHLY)[-1].lower().replace("✅", ""))  # sin la línea de instrucciones
        self.assertEqual(b, int(self.s.botones_enviados[-1][0][1].split(":")[1]))

    def test_reasignar_envia_la_tarea_con_botones_a_quien_la_recibe(self):
        tid = self.tarea(self.mafer, "Limpiar", hora(60))
        self.dice(self.dueno, "pasa la tarea de Mafer a Ashly")
        self.dice(self.dueno, "sí")
        i = [t for t, _ in self.s.enviados].index(ASHLY)
        self.assertEqual(self.s.botones_enviados[i][0], ("✅ Listo", f"listo:{tid}"))


class TestToques(BaseBotones):
    def boton(self, persona, texto):
        logica.procesar_boton(self.s, persona, texto)

    def test_listo_completa_solo_esa_tarea_aunque_haya_varias(self):
        a = self.tarea(self.mafer, "A")
        b = self.tarea(self.mafer, "B")
        self.boton(self.mafer, f"listo:{b}")
        self.assertEqual((self.fila(a)["estado"], self.fila(b)["estado"]), ("pendiente", "completada"))
        self.assertIn("marqué *B*", self.ultima)
        self.assertIn("completó: B", self.avisos_a(DUENO)[0])  # el dueño se entera

    def test_tocar_dos_veces_no_duplica(self):
        a = self.tarea(self.mafer, "A")
        self.boton(self.mafer, f"listo:{a}")
        n = len(self.avisos_a(DUENO))
        self.boton(self.mafer, f"listo:{a}")
        self.assertIn("ya no está abierta", self.ultima)
        self.assertEqual(len(self.avisos_a(DUENO)), n)
        self.assertEqual(len([e for e in self.eventos() if e == "completada"]), 1)

    def eventos(self):
        with db.conn() as c:
            return [r["tipo"] for r in c.execute("SELECT tipo FROM eventos")]

    def test_no_se_puede_tocar_el_boton_de_la_tarea_de_otra_persona(self):
        a = self.tarea(self.ashly, "Caja")
        self.boton(self.mafer, f"listo:{a}")  # un id copiado o reenviado
        self.assertEqual(self.fila(a)["estado"], "pendiente")
        self.assertIn("ya no está abierta", self.ultima)

    def test_problema_pide_el_detalle_y_lo_avisa(self):
        a = self.tarea(self.mafer, "Limpiar")
        self.boton(self.mafer, f"problema:{a}")
        self.assertIn("¿Qué pasó con *Limpiar*?", self.ultima)
        self.dice(self.mafer, "no hay cloro")
        self.assertEqual(self.fila(a)["estado"], "con_incidencia")
        self.assertIn("no hay cloro", self.avisos_a(DUENO)[0])

    def test_mas_tiempo_ofrece_15_30_y_1_hora(self):
        a = self.tarea(self.mafer, "Limpiar", hora(5))
        self.boton(self.mafer, f"mas:{a}")
        self.assertEqual([e for e, _ in self.s.botones_respuestas[-1]], ["15 min", "30 min", "1 hora"])
        self.assertIn("Limpiar", self.ultima)

    def test_elegir_minutos_cambia_el_plazo_y_avisa_al_dueno(self):
        a = self.tarea(self.mafer, "Limpiar", hora(5), rec_previo=1)
        self.boton(self.mafer, f"plazo:{a}:30")
        f = self.fila(a)
        self.assertEqual(f["vence_en"], hora(30))
        self.assertEqual(f["rec_previo"], 0)  # se reinician los avisos
        self.assertIn("Limpiar", self.avisos_a(DUENO)[0])

    def test_mas_tiempo_tambien_se_puede_responder_escribiendo_la_hora(self):
        a = self.tarea(self.mafer, "Limpiar", hora(5))
        self.boton(self.mafer, f"mas:{a}")
        self.dice(self.mafer, "en 45 minutos")
        self.assertEqual(self.fila(a)["vence_en"], hora(45))

    def test_minutos_raros_no_hacen_nada(self):
        a = self.tarea(self.mafer, "Limpiar", hora(5))
        self.boton(self.mafer, f"plazo:{a}:99999")
        self.assertEqual(self.fila(a)["vence_en"], hora(5))

    def test_ids_invalidos_se_ignoran(self):
        a = self.tarea(self.mafer, "Limpiar")
        n = len(self.s.respuestas)
        for raro in ("", "listo", "listo:abc", "borrar:1", ":", "listo:99999"):
            self.boton(self.mafer, raro)
        self.assertEqual(self.fila(a)["estado"], "pendiente")
        self.assertEqual(len(self.s.respuestas), n + 1)  # solo "listo:99999" dice que esa tarea no está abierta

    def test_un_cliente_o_la_linea_corporativa_no_usan_botones(self):
        a = self.tarea(self.mafer, "Limpiar")
        self.s.linea = "corporativa"
        self.boton(self.mafer, f"listo:{a}")
        self.assertEqual(self.fila(a)["estado"], "pendiente")

    def test_tarea_cancelada_antes_del_toque(self):
        a = self.tarea(self.mafer, "Limpiar")
        db.cancelar_tarea(a)
        self.boton(self.mafer, f"listo:{a}")
        self.assertEqual(self.fila(a)["estado"], "cancelada")
        self.assertIn("ya no está abierta", self.ultima)

    def test_un_toque_de_listo_corta_un_paso_pendiente_de_la_conversacion(self):
        a = self.tarea(self.mafer, "A")
        b = self.tarea(self.mafer, "B")
        self.dice(self.mafer, "listo")  # queda esperando "¿cuál?"
        self.boton(self.mafer, f"listo:{a}")
        self.dice(self.mafer, "2")  # ya no debe interpretarse como elección de lista
        self.assertEqual(self.fila(b)["estado"], "pendiente")
