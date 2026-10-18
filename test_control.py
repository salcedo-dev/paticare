"""Pruebas del control del dueño: cancelar, reasignar, cambiar plazo, recordar, avisar al equipo, historial, deshacer."""
import unittest
from datetime import timedelta

import db
import logica
from test_flujos import tareas
from test_seguimiento import AHORA, ASHLY, DUENO, MAFER, Base, hora


class TestCancelar(Base):
    def test_cancela_con_confirmacion_y_avisa(self):
        tid = self.tarea(self.mafer, "Limpiar quirófano")
        self.dice(self.dueno, "cancela la tarea de Mafer")
        self.assertIn("¿Confirmas?", self.ultima)
        self.assertEqual(self.fila(tid)["estado"], "pendiente")  # nada pasa sin el SÍ
        self.assertEqual(self.s.enviados, [])
        self.dice(self.dueno, "sí")
        self.assertEqual(self.fila(tid)["estado"], "cancelada")
        self.assertIn("se canceló: *Limpiar quirófano*", self.avisos_a(MAFER)[0])

    def test_no_deja_todo_igual(self):
        tid = self.tarea(self.mafer, "Limpiar quirófano")
        self.dice(self.dueno, "cancela lo de Mafer")
        self.dice(self.dueno, "no")
        self.assertEqual(self.fila(tid)["estado"], "pendiente")
        self.assertEqual(self.s.enviados, [])
        self.dice(self.dueno, "sí")  # ya no hay nada pendiente
        self.assertIn("No tengo nada pendiente", self.ultima)

    def test_varias_tareas_pregunta_cual(self):
        a = self.tarea(self.mafer, "Limpiar quirófano")
        b = self.tarea(self.mafer, "Bañar a Firulais")
        self.dice(self.dueno, "cancela la tarea de Mafer")
        self.assertIn("1. Limpiar quirófano", self.ultima)
        self.dice(self.dueno, "2")
        self.assertIn("¿Confirmas?", self.ultima)
        self.dice(self.dueno, "sí")
        self.assertEqual((self.fila(a)["estado"], self.fila(b)["estado"]), ("pendiente", "cancelada"))

    def test_todas(self):
        a = self.tarea(self.mafer, "Limpiar quirófano")
        b = self.tarea(self.mafer, "Bañar a Firulais")
        self.dice(self.dueno, "cancela las tareas de Mafer")
        self.dice(self.dueno, "todas")
        self.dice(self.dueno, "sí")
        self.assertEqual((self.fila(a)["estado"], self.fila(b)["estado"]), ("cancelada", "cancelada"))

    def test_palabra_de_la_tarea_elige_sola(self):
        a = self.tarea(self.mafer, "Limpiar quirófano")
        b = self.tarea(self.mafer, "Bañar a Firulais")
        self.dice(self.dueno, "cancela lo del quirófano de Mafer")
        self.assertIn("¿Confirmas?", self.ultima)
        self.dice(self.dueno, "sí")
        self.assertEqual((self.fila(a)["estado"], self.fila(b)["estado"]), ("cancelada", "pendiente"))

    def test_no_toca_tareas_de_otra_persona(self):
        a = self.tarea(self.ashly, "Atender caja")
        self.dice(self.dueno, "cancela la tarea de Mafer")
        self.assertIn("Mafer no tiene tareas pendientes", self.ultima)
        self.assertEqual(self.fila(a)["estado"], "pendiente")

    def test_tarea_completada_antes_del_si_no_se_cancela(self):
        tid = self.tarea(self.mafer, "Limpiar quirófano")
        self.dice(self.dueno, "cancela lo de Mafer")
        self.dice(self.mafer, "listo")
        self.s.enviados.clear()
        self.dice(self.dueno, "sí")
        self.assertEqual(self.fila(tid)["estado"], "completada")
        self.assertIn("ya no están pendientes", self.ultima)
        self.assertEqual(self.s.enviados, [])

    def test_cancelada_no_recibe_avisos_de_plazo(self):
        tid = self.tarea(self.mafer, "Limpiar", hora(10))
        self.dice(self.dueno, "cancela lo de Mafer")
        self.dice(self.dueno, "sí")
        self.s.enviados.clear()
        self.assertEqual(self.correr(), [])
        self.assertEqual(self.s.enviados, [])

    def test_cancelar_cita_de_cliente_sigue_a_la_ia(self):
        self.llm.poner({"accion": "ninguna", "tipo": "ninguno", "datos": {}, "pregunta": None,
                                "respuesta": "No cancelo citas."})
        self.dice(self.dueno, "cancela la cita de Firulais")
        self.assertEqual(self.ultima, "No cancelo citas.")


class TestReasignar(Base):
    def test_pasa_la_tarea_y_avisa_a_los_dos(self):
        tid = self.tarea(self.mafer, "Limpiar quirófano", hora(60))
        self.dice(self.dueno, "pasa la tarea de Mafer a Ashly")
        self.assertIn("pasar de Mafer a Ashly", self.ultima)
        self.dice(self.dueno, "sí")
        f = self.fila(tid)
        self.assertEqual(f["responsable_id"], self.ashly["id"])
        self.assertIn("🆕 *Limpiar quirófano*", self.avisos_a(ASHLY)[0])
        self.assertIn("⏰ hoy 09:00", self.avisos_a(ASHLY)[0])
        self.assertIn("ahora la hace Ashly", self.avisos_a(MAFER)[0])

    def test_reinicia_avisos_del_plazo(self):
        tid = self.tarea(self.mafer, "Limpiar", hora(5), rec_previo=1)
        self.dice(self.dueno, "reasigna lo de Mafer a Ashly")
        self.dice(self.dueno, "sí")
        self.assertEqual(self.fila(tid)["rec_previo"], 0)
        self.assertEqual(self.correr(), [("previo", tid)])
        self.assertTrue(self.avisos_a(ASHLY))  # el aviso va a la nueva responsable

    def test_sin_dos_nombres_no_es_reasignar(self):
        self.tarea(self.mafer, "Limpiar")
        self.llm.poner({"accion": "ninguna", "tipo": "ninguno", "datos": {}, "pregunta": None,
                                "respuesta": "ok ia"})
        self.dice(self.dueno, "pasa lo de Mafer")
        self.assertEqual(self.ultima, "ok ia")


class TestPlazo(Base):
    def test_cambia_plazo_con_confirmacion(self):
        tid = self.tarea(self.mafer, "Limpiar", hora(30), rec_previo=1)
        self.dice(self.dueno, "cambia el plazo de Mafer a las 6pm")
        self.assertIn("hoy 18:00", self.ultima)
        self.assertEqual(self.fila(tid)["vence_en"], hora(30))
        self.dice(self.dueno, "sí")
        f = self.fila(tid)
        self.assertEqual(f["vence_en"], AHORA.strftime("%Y-%m-%d") + " 18:00")
        self.assertEqual(f["rec_previo"], 0)
        self.assertIn("nuevo plazo para *Limpiar*: hoy 18:00", self.avisos_a(MAFER)[0])

    def test_en_minutos(self):
        tid = self.tarea(self.mafer, "Limpiar", None)
        self.dice(self.dueno, "extiende el plazo de Mafer en 30 minutos")
        self.dice(self.dueno, "sí")
        self.assertEqual(self.fila(tid)["vence_en"], hora(30))

    def test_sin_hora_entendible_va_a_la_ia(self):
        self.tarea(self.mafer, "Limpiar")
        self.llm.poner({"accion": "ninguna", "tipo": "ninguno", "datos": {}, "pregunta": None,
                                "respuesta": "¿a qué hora?"})
        self.dice(self.dueno, "cambia el plazo de Mafer")
        self.assertEqual(self.ultima, "¿a qué hora?")


class TestRecordar(Base):
    def test_recuerdale_pide_confirmacion_y_envia_pendientes(self):
        self.tarea(self.mafer, "Limpiar quirófano")
        self.dice(self.dueno, "recuérdale a Mafer")
        self.assertEqual(self.s.enviados, [])
        self.dice(self.dueno, "sí")
        aviso = self.avisos_a(MAFER)[0]
        self.assertIn("Andrés te recuerda", aviso)
        self.assertIn("Limpiar quirófano", aviso)

    def test_sin_pendientes(self):
        self.dice(self.dueno, "recuérdale a Mafer")
        self.assertIn("no tiene tareas pendientes", self.ultima)

    def test_recordatorio_de_cliente_con_hora_va_a_la_ia(self):
        self.tarea(self.mafer, "Limpiar")
        self.llm.poner({"accion": "ninguna", "tipo": "ninguno", "datos": {}, "pregunta": None,
                                "respuesta": "ia"})
        self.dice(self.dueno, "recuérdale a Mafer mañana a las 10 la reunión")
        self.assertEqual(self.ultima, "ia")


class TestAnuncio(Base):
    def test_avisa_a_todos_menos_al_dueno_con_confirmacion(self):
        self.dice(self.dueno, "avisa al equipo que mañana abrimos a las 8")
        self.assertIn("📢 Mañana abrimos a las 8", self.ultima)
        self.assertIn("Ashly, Mafer", self.ultima)
        self.assertEqual(self.s.enviados, [])
        self.dice(self.dueno, "sí")
        self.assertIn("Mañana abrimos a las 8", self.avisos_a(MAFER)[0])
        self.assertIn("Aviso de Andrés", self.avisos_a(ASHLY)[0])
        self.assertEqual(self.avisos_a(DUENO), [])

    def test_con_tilde_y_dos_puntos(self):
        self.dice(self.dueno, "Avísales a todos: hoy cerramos temprano")
        self.assertIn("Hoy cerramos temprano", self.ultima)

    def test_no_enviado_si_dice_no(self):
        self.dice(self.dueno, "avisa al equipo que hay reunión")
        self.dice(self.dueno, "no")
        self.assertEqual(self.s.enviados, [])


class TestHistorial(Base):
    def test_muestra_linea_de_tiempo_real(self):
        tid = self.tarea(self.mafer, "Limpiar quirófano", hora(30))
        db.incidencia_tarea(tid, "no hay cloro", self.mafer["id"])
        db.completar_tarea(tid, self.mafer["id"])
        self.dice(self.dueno, "historial de Mafer")
        r = self.ultima
        self.assertIn("Historial de Mafer", r)
        self.assertIn("Limpiar quirófano", r)
        self.assertIn("⚠️ problema: no hay cloro", r)
        self.assertIn("✅ completada", r)

    def test_sin_nombre_pregunta(self):
        self.dice(self.dueno, "historial")
        self.assertIn("¿De quién", self.ultima)

    def test_persona_sin_tareas(self):
        self.dice(self.dueno, "qué pasó con Ashly")
        self.assertIn("todavía no tiene tareas", self.ultima)


class TestDeshacer(Base):
    def _enviar_tareas(self):
        self.llm.poner(tareas("nueva", ("Mafer", "Limpiar quirófano", None, None),
                              ("Ashly", "Atender caja", None, None)))
        self.dice(self.dueno, "mafer limpia el quirofano y ashly atiende caja")
        self.dice(self.dueno, "sí")
        self.s.enviados.clear()

    def test_deshace_el_ultimo_envio(self):
        self._enviar_tareas()
        self.dice(self.dueno, "deshacer")
        self.assertIn("Limpiar quirófano", self.ultima)
        self.assertEqual(self.s.enviados, [])
        self.dice(self.dueno, "sí")
        self.assertEqual(db.tareas_pendientes(self.mafer["id"]), [])
        self.assertEqual(db.tareas_pendientes(self.ashly["id"]), [])
        self.assertIn("se canceló", self.avisos_a(MAFER)[0])

    def test_no_deshace_lo_ya_completado(self):
        self._enviar_tareas()
        self.dice(self.mafer, "listo")
        self.dice(self.dueno, "deshacer")
        self.dice(self.dueno, "sí")
        self.assertEqual(db.tareas_pendientes(self.ashly["id"]), [])
        self.assertEqual(len(db.estado_tareas(self.mafer["id"])), 1)
        self.assertEqual(db.estado_tareas(self.mafer["id"])[0]["estado"], "completada")

    def test_pasados_30_minutos_no_se_puede(self):
        self._enviar_tareas()
        self.ahora = AHORA + timedelta(minutes=31)
        self.dice(self.dueno, "deshacer")
        self.assertIn("No tengo nada reciente que deshacer", self.ultima)
        self.assertEqual(len(db.tareas_pendientes(self.mafer["id"])), 1)

    def test_sin_envios_previos(self):
        self.dice(self.dueno, "deshacer")
        self.assertIn("No tengo nada reciente", self.ultima)

    def test_no_se_deshace_dos_veces(self):
        self._enviar_tareas()
        self.dice(self.dueno, "deshacer")
        self.dice(self.dueno, "sí")
        self.dice(self.dueno, "deshacer")
        self.assertIn("No tengo nada reciente", self.ultima)


class TestConvivencia(Base):
    def test_control_con_propuesta_pendiente_no_se_mezcla(self):
        self.tarea(self.mafer, "Limpiar")
        self.llm.poner(tareas("nueva", ("Ashly", "Atender caja", None, None)))
        self.dice(self.dueno, "ashly atiende caja")
        self.dice(self.dueno, "cancela lo de Mafer")
        self.assertIn("Primero responde *SÍ* o *NO*", self.ultima)
        self.dice(self.dueno, "sí")  # confirma la propuesta, no cancela nada
        self.assertEqual(len(db.tareas_pendientes(self.mafer["id"])), 1)
        self.assertEqual(len(db.tareas_pendientes(self.ashly["id"])), 1)

    def test_texto_que_no_es_si_ni_no_no_se_lo_pasa_a_la_ia_ni_borra_la_orden(self):
        tid = self.tarea(self.mafer, "Limpiar")
        self.dice(self.dueno, "cancela lo de Mafer")
        self.dice(self.dueno, "ashly atiende caja")  # una orden nueva mientras hay una esperando el sí
        self.assertIn("esperando tu *SÍ* o *NO*", self.ultima)
        self.assertEqual(self.llm.llamadas, [])  # la IA no la convierte en una propuesta equivocada
        self.dice(self.dueno, "sí")  # la orden original seguía ahí
        self.assertEqual(self.fila(tid)["estado"], "cancelada")

    def test_orden_caduca(self):
        tid = self.tarea(self.mafer, "Limpiar")
        self.dice(self.dueno, "cancela lo de Mafer")
        self.ahora = AHORA + timedelta(minutes=45)
        self.dice(self.dueno, "sí")
        self.assertEqual(self.fila(tid)["estado"], "pendiente")
        self.assertIn("caducó", self.ultima)

    def test_trabajador_no_puede_dar_ordenes_de_control(self):
        tid = self.tarea(self.ashly, "Atender caja")
        self.dice(self.mafer, "cancela lo de Ashly")
        self.dice(self.mafer, "sí")
        self.assertEqual(self.fila(tid)["estado"], "pendiente")

    def test_ayuda_menciona_control(self):
        self.dice(self.dueno, "ayuda")
        self.assertIn("deshacer", self.ultima)


if __name__ == "__main__":
    unittest.main()
