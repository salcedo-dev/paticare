"""Casos reales del chat del dueño: "mándame el resultado en 10 min", "recuérdales sus tareas e incidencias resueltas"."""
from datetime import timedelta

import db
import logica
from test_encuestas import BaseEnc
from test_seguimiento import AHORA, ASHLY, DUENO, MAFER


class TestResultadosCuandoLoPide(BaseEnc):
    """El dueño ve los resultados cuando quiere (sin configurar tiempos)."""

    def test_avisame_los_resultados_ya_son_los_de_la_encuesta_no_el_estado_de_tareas(self):
        self.enviar_encuesta()
        self.vota(self.mafer, "Yo")
        self.dice(self.dueno, "avisame los resultados ya")  # la frase exacta del chat
        self.assertIn("Yo: *1* (Mafer)", self.ultima)
        self.assertNotIn("Estado de las tareas", self.ultima)

    def test_otras_formas_de_pedirlos(self):
        self.enviar_encuesta()
        for frase in ("resultados", "dame los resultados", "como va", "cómo vamos", "mándame el resumen ahora"):
            with self.subTest(frase=frase):
                self.dice(self.dueno, frase)
                self.assertIn("Sin responder (3)", self.ultima)

    def test_sin_encuesta_reciente_cómo_va_sigue_siendo_el_estado_de_tareas(self):
        self.tarea(self.mafer, "Limpiar")
        self.dice(self.dueno, "cómo va todo")
        self.assertIn("Estado de las tareas", self.ultima)

    def test_una_encuesta_de_hace_mas_de_un_dia_ya_no_cuenta(self):
        self.enviar_encuesta()
        self.ahora = AHORA + timedelta(days=2)
        self.tarea(self.mafer, "Limpiar")
        self.dice(self.dueno, "resultados")
        self.assertNotIn("Sin responder", self.ultima)

    def test_ya_no_se_puede_programar_los_resultados_a_cierta_hora(self):
        self.enviar_encuesta()
        self.llm.poner({"accion": "ninguna", "tipo": "ninguno", "datos": {}, "pregunta": None, "respuesta": "ia"})
        self.dice(self.dueno, "mándame el resultado en 10 min")
        self.assertEqual(self.ultima, "ia")  # no es una función: lo entiende la IA como cualquier otra frase


class TestRecordarAlEquipo(BaseEnc):
    def crear(self):
        self.a = self.tarea(self.ashly, "Caja")
        self.b = self.tarea(self.mafer, "Limpiar", hora(60))
        self.c = self.tarea(self.mafer, "Bañar")
        self.d = self.tarea(self.olenka, "Revisar vacunas")

    def test_les_recuerda_a_cada_uno_sus_pendientes_con_confirmacion(self):
        self.crear()
        self.dice(self.dueno, "recuérdales sus tareas pendientes")
        r = self.ultima
        self.assertIn("Ashly (1)", r)
        self.assertIn("Mafer (2)", r)
        self.assertIn("Olenka (1)", r)
        self.assertEqual(self.s.enviados, [])
        self.dice(self.dueno, "sí")
        self.assertIn("Limpiar", self.avisos_a(MAFER)[0])
        self.assertIn("Bañar", self.avisos_a(MAFER)[0])
        self.assertNotIn("Caja", self.avisos_a(MAFER)[0])  # cada uno ve solo lo suyo
        self.assertEqual(len(self.avisos_a(MAFER)), 1)
        self.assertEqual(self.avisos_a(DUENO), [])
        self.assertIn("3 persona(s)", self.ultima)

    def test_con_una_sola_tarea_va_con_botones(self):
        self.crear()
        self.neg["botones"] = True
        self.dice(self.dueno, "recuérdales sus tareas pendientes")
        self.dice(self.dueno, "sí")
        i = [t for t, _ in self.s.enviados].index(ASHLY)
        self.assertEqual(self.s.botones_enviados[i][0], ("✅ Listo", f"listo:{self.a}"))

    def test_mandales_el_estado_de_sus_tareas_tambien_es_recordarles(self):
        self.crear()
        self.dice(self.dueno, "mándales el estado de sus tareas")
        self.assertIn("Recordarle a cada uno", self.ultima)  # no el reporte del dueño

    def test_solo_a_las_personas_nombradas(self):
        self.crear()
        self.dice(self.dueno, "recuérdales a mafer y ashly sus tareas pendientes")
        self.assertIn("Mafer (2)", self.ultima)
        self.assertIn("Ashly (1)", self.ultima)
        self.assertNotIn("Olenka", self.ultima)

    def test_sin_tareas_pendientes(self):
        self.dice(self.dueno, "recuérdales sus tareas pendientes")
        self.assertIn("Nadie tiene tareas pendientes", self.ultima)

    def test_con_no_no_se_envia_nada(self):
        self.crear()
        self.dice(self.dueno, "recuérdales sus tareas pendientes")
        self.dice(self.dueno, "no")
        self.assertEqual(self.s.enviados, [])

    def test_si_una_tarea_se_completa_antes_del_si_no_se_le_recuerda(self):
        self.crear()
        self.dice(self.dueno, "recuérdales sus tareas pendientes")
        db.completar_tarea(self.a, self.ashly["id"])
        self.dice(self.dueno, "sí")
        self.assertEqual(self.avisos_a(ASHLY), [])
        self.assertTrue(self.avisos_a(MAFER))

    def test_no_se_le_recuerda_al_dueno_sus_propias_tareas(self):
        self.crear()
        self.tarea(self.dueno, "Revisar caja")
        self.dice(self.dueno, "recuérdales sus tareas pendientes")
        self.dice(self.dueno, "sí")
        self.assertEqual(self.avisos_a(DUENO), [])


class TestIncidenciasResueltas(BaseEnc):
    def crear(self):
        self.a = self.tarea(self.mafer, "Limpiar quirófano")
        db.incidencia_tarea(self.a, "se escapó la cabra", self.mafer["id"])
        self.b = self.tarea(self.ashly, "Caja")
        self.c = self.tarea(self.mafer, "Bañar")

    def estado(self, tid):
        return self.fila(tid)["estado"]

    def test_marca_como_resuelta_y_avisa_a_quien_la_tenia(self):
        self.crear()
        self.dice(self.dueno, "las incidencias ya han sido resueltas")
        self.assertIn("Limpiar quirófano", self.ultima)
        self.assertEqual(self.estado(self.a), "con_incidencia")  # nada cambia sin el sí
        self.dice(self.dueno, "sí")
        self.assertEqual(self.estado(self.a), "pendiente")
        self.assertIn("Ya se resolvió tu problema con *Limpiar quirófano*", self.avisos_a(MAFER)[0])
        self.assertEqual(self.avisos_a(ASHLY), [])  # Ashly no tenía incidencias
        self.assertIn("1 incidencia", self.ultima)

    def test_queda_en_el_historial(self):
        self.crear()
        self.dice(self.dueno, "las incidencias ya están resueltas")
        self.dice(self.dueno, "sí")
        self.dice(self.dueno, "historial de Mafer")
        self.assertIn("resuelta", self.ultima)

    def test_de_una_persona(self):
        self.crear()
        otra = self.tarea(self.ashly, "Atender")
        db.incidencia_tarea(otra, "falló la impresora", self.ashly["id"])
        self.dice(self.dueno, "la incidencia de mafer ya está resuelta")
        self.dice(self.dueno, "sí")
        self.assertEqual(self.estado(self.a), "pendiente")
        self.assertEqual(self.estado(otra), "con_incidencia")

    def test_sin_incidencias_abiertas(self):
        self.dice(self.dueno, "las incidencias ya fueron resueltas")
        self.assertIn("No hay incidencias abiertas", self.ultima)

    def test_el_caso_exacto_del_chat_hace_las_dos_cosas_con_un_solo_si(self):
        self.crear()
        self.dice(self.dueno, "recuerdales sus tareas pendientes y las incidencias ya ah nsido resueltas")
        r = self.ultima
        self.assertIn("resueltas* 1 incidencia", r)
        self.assertIn("Recordarle a cada uno", r)
        self.assertEqual(self.llm.llamadas, [])  # sin IA: ya no inventa tareas
        self.dice(self.dueno, "sí")
        self.assertEqual(self.estado(self.a), "pendiente")
        mensajes = self.avisos_a(MAFER)
        self.assertEqual(len(mensajes), 1)  # un solo mensaje por persona
        self.assertIn("Ya se resolvió tu problema con *Limpiar quirófano*", mensajes[0])
        self.assertIn("Bañar", mensajes[0])
        self.assertEqual(len(self.avisos_a(ASHLY)), 1)
        self.assertIn("Resolví 1 incidencia", self.ultima)

    def test_resolver_una_tarea_que_ya_no_tiene_incidencia_no_falla(self):
        self.crear()
        self.dice(self.dueno, "las incidencias ya han sido resueltas")
        db.resolver_incidencia(self.a)  # alguien más la resolvió antes del sí
        self.dice(self.dueno, "sí")
        self.assertEqual(self.estado(self.a), "pendiente")

    def test_un_trabajador_no_puede_resolver_incidencias(self):
        self.crear()
        self.dice(self.mafer, "las incidencias ya han sido resueltas")
        self.assertEqual(self.estado(self.a), "con_incidencia")


class TestCancelarTodas(BaseEnc):
    def test_cancela_todas_las_tareas_pendientes_con_confirmacion(self):
        a = self.tarea(self.mafer, "A")
        b = self.tarea(self.ashly, "B")
        self.dice(self.dueno, "cancela todas las tareas pendientes")
        self.assertIn("A", self.ultima)
        self.assertEqual((self.fila(a)["estado"], self.fila(b)["estado"]), ("pendiente", "pendiente"))
        self.dice(self.dueno, "sí")
        self.assertEqual((self.fila(a)["estado"], self.fila(b)["estado"]), ("cancelada", "cancelada"))
        self.assertIn("se canceló", self.avisos_a(MAFER)[0])

    def test_sin_tareas(self):
        self.dice(self.dueno, "cancela todas las tareas")
        self.assertIn("No hay tareas pendientes", self.ultima)

    def test_cancela_sola_sigue_siendo_un_no(self):
        a = self.tarea(self.mafer, "A")
        self.dice(self.dueno, "cancela")
        self.assertEqual(self.fila(a)["estado"], "pendiente")


def hora(minutos):
    from test_seguimiento import hora as h
    return h(minutos)
