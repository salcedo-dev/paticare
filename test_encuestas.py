"""Pruebas de las encuestas al equipo: crear, enviar, votar, resultados, recordatorio, check-in diario."""
import json
import tempfile
import unittest
from datetime import timedelta
from unittest import mock

import db
import logica
import negocio
from test_seguimiento import AHORA, ASHLY, DUENO, MAFER, Base


class BaseEnc(Base):
    def setUp(self):
        super().setUp()
        self.neg["encuestas"] = {"recordar_min": 30}
        with db.conn() as c:
            c.execute("INSERT INTO personas(telefono, lid, nombre, rol, extras) VALUES('51900000009', NULL, 'Olenka', 'trabajador', '')")
        self.olenka = db.persona_por_id("51900000009")

    def enviar_encuesta(self, texto="encuesta ¿Quién cubre el sábado? opciones: yo, no puedo"):
        self.dice(self.dueno, texto)
        self.dice(self.dueno, "sí")
        return db.ultima_encuesta()

    def vota(self, persona, opcion, enc=None):
        enc = enc or db.ultima_encuesta()
        logica.procesar_voto_encuesta(self.s, persona, enc["id"], opcion)

    def correr_encuestas(self, minutos):
        self.ahora = AHORA + timedelta(minutes=minutos)
        return logica.revisar_encuestas(self.s)


class TestParsear(BaseEnc):
    def test_pregunta_con_opciones(self):
        p, o = logica._parsear_encuesta("encuesta ¿Quién cubre el sábado? opciones: yo, no puedo")
        self.assertEqual((p, o), ("¿Quién cubre el sábado?", ["Yo", "No puedo"]))

    def test_con_dos_puntos_y_mayusculas(self):
        p, o = logica._parsear_encuesta("Haz una encuesta al equipo: ¿Qué turno prefieren? Opciones: mañana / tarde / noche")
        self.assertEqual((p, o), ("¿Qué turno prefieren?", ["Mañana", "Tarde", "Noche"]))

    def test_sin_opciones_es_si_no(self):
        p, o = logica._parsear_encuesta("encuesta ¿Pueden venir el sábado?")
        self.assertEqual(o, logica.OPCIONES_SI_NO)

    def test_pregunta_al_equipo_con_si(self):
        p, o = logica._parsear_encuesta("pregúntale al equipo si pueden quedarse hasta las 8")
        self.assertEqual((p, o), ("¿Pueden quedarse hasta las 8?", logica.OPCIONES_SI_NO))

    def test_o_separa_opciones(self):
        _, o = logica._parsear_encuesta("encuesta ¿Cuándo? opciones: viernes o sábado")
        self.assertEqual(o, ["Viernes", "Sábado"])

    def test_opciones_repetidas_se_juntan(self):
        _, o = logica._parsear_encuesta("encuesta ¿Cuándo? opciones: sí, Sí, no")
        self.assertEqual(o, ["Sí", "No"])

    def test_sin_pregunta_explica_como_hacerla(self):
        p, msg = logica._parsear_encuesta("encuesta")
        self.assertIsNone(p)
        self.assertIn("Por ejemplo", msg)

    def test_una_sola_opcion_no_sirve(self):
        p, msg = logica._parsear_encuesta("encuesta ¿Vienes? opciones: sí")
        self.assertIsNone(p)
        self.assertIn("entre 2 y 8", msg)

    def test_demasiadas_opciones(self):
        p, msg = logica._parsear_encuesta("encuesta ¿Cuál? opciones: a, b, c, d, e, f, g, h, i")
        self.assertIsNone(p)

    def test_pregunta_muy_larga(self):
        p, msg = logica._parsear_encuesta("encuesta " + "x" * 250)
        self.assertIsNone(p)
        self.assertIn("muy larga", msg)


class TestCrearYEnviar(BaseEnc):
    def test_muestra_la_propuesta_y_no_envia_sin_el_si(self):
        self.dice(self.dueno, "encuesta ¿Quién cubre el sábado? opciones: yo, no puedo")
        r = self.ultima
        self.assertIn("¿Quién cubre el sábado?", r)
        self.assertIn("Ashly, Mafer, Olenka", r)
        self.assertNotIn("Andrés,", r)
        self.assertIn("¿La envío?", r)
        self.assertIn(".resultados", r)  # cómo ver el avance cuando quiera
        self.assertEqual(self.s.encuestas, [])
        self.assertIsNone(db.ultima_encuesta())

    def test_con_el_no_no_pasa_nada(self):
        self.dice(self.dueno, "encuesta ¿Vienes? opciones: sí, no")
        self.dice(self.dueno, "no")
        self.assertEqual(self.s.encuestas, [])
        self.assertIsNone(db.ultima_encuesta())

    def test_con_el_si_sale_a_todo_el_equipo_menos_al_dueno(self):
        enc = self.enviar_encuesta()
        self.assertEqual({t for t, _, _ in self.s.encuestas}, {ASHLY, MAFER, "51900000009"})
        self.assertEqual(self.s.encuestas[0][1:], ("¿Quién cubre el sábado?", ["Yo", "No puedo"]))
        self.assertEqual(len(db.destinatarios_encuesta(enc["id"])), 3)
        self.assertIn("3 persona(s)", self.ultima)

    def test_el_id_del_mensaje_queda_guardado_para_reconocer_los_votos(self):
        enc = self.enviar_encuesta()
        self.assertEqual(db.envio_por_msg(self.s.ids_encuestas[0])["encuesta_id"], enc["id"])

    def test_si_falla_un_envio_se_dice_y_los_demas_salen(self):
        self.dice(self.dueno, "encuesta ¿Vienes? opciones: sí, no")
        original = self.s.enviar_encuesta
        self.s.enviar_encuesta = lambda tel, p, o: (_ for _ in ()).throw(RuntimeError("sin red")) if tel == MAFER else original(tel, p, o)
        self.dice(self.dueno, "sí")
        self.assertEqual({t for t, _, _ in self.s.encuestas}, {ASHLY, "51900000009"})
        self.assertIn("No pude avisar a: Mafer", self.ultima)

    def test_sin_equipo(self):
        with db.conn() as c:
            c.execute("UPDATE personas SET rol='cliente' WHERE rol='trabajador'")
        self.dice(self.dueno, "encuesta ¿Vienes?")
        self.assertIn("No hay nadie más", self.ultima)

    def test_un_trabajador_no_puede_crear_encuestas(self):
        self.dice(self.mafer, "encuesta ¿Vienes?")
        self.assertEqual(self.s.encuestas, [])

    def test_con_una_propuesta_pendiente_no_se_mezcla(self):
        from test_flujos import tareas
        self.llm.poner(tareas("nueva", ("Ashly", "Atender caja", None, None)))
        self.dice(self.dueno, "ashly atiende caja")
        self.dice(self.dueno, "encuesta ¿Vienes? opciones: sí, no")
        self.assertIn("Primero responde *SÍ* o *NO*", self.ultima)
        self.assertEqual(self.s.encuestas, [])

    def test_la_pregunta_con_como_va_no_se_confunde_con_el_estado_de_tareas(self):
        self.dice(self.dueno, "encuesta ¿cómo va el día? opciones: bien, mal")
        self.assertIn("¿La envío?", self.ultima)
        self.assertEqual(self.llm.llamadas, [])  # tampoco gasta IA

    def test_mensajes_que_solo_mencionan_encuesta_siguen_a_la_ia(self):
        self.llm.poner({"accion": "ninguna", "tipo": "ninguno", "datos": {}, "pregunta": None, "respuesta": "ia"})
        self.dice(self.dueno, "mafer hace la encuesta de clientes")
        self.assertEqual(self.ultima, "ia")
        self.assertEqual(self.s.encuestas, [])


class TestVotos(BaseEnc):
    def test_votar_se_guarda_y_se_confirma(self):
        self.enviar_encuesta()
        self.vota(self.mafer, "Yo")
        self.assertEqual([(v["nombre"], v["opcion"]) for v in db.votos_de(db.ultima_encuesta()["id"])], [("Mafer", "Yo")])
        self.assertIn("Anotado: Yo", self.ultima)

    def test_cambiar_el_voto_lo_actualiza_y_no_duplica(self):
        self.enviar_encuesta()
        self.vota(self.mafer, "Yo")
        self.vota(self.mafer, "No puedo")
        votos = db.votos_de(db.ultima_encuesta()["id"])
        self.assertEqual([(v["nombre"], v["opcion"]) for v in votos], [("Mafer", "No puedo")])
        self.assertIn("Cambié tu voto", self.ultima)

    def test_votar_lo_mismo_dos_veces_no_repite_mensajes(self):
        self.enviar_encuesta()
        self.vota(self.mafer, "Yo")
        n = len(self.s.respuestas)
        self.vota(self.mafer, "Yo")
        self.assertEqual(len(self.s.respuestas), n)

    def test_una_opcion_que_no_existe_se_ignora(self):
        self.enviar_encuesta()
        self.vota(self.mafer, "Mañana")
        self.assertEqual(db.votos_de(db.ultima_encuesta()["id"]), [])

    def test_votar_en_una_encuesta_que_no_existe(self):
        logica.procesar_voto_encuesta(self.s, self.mafer, 999, "Yo")  # no falla
        self.assertEqual(self.s.respuestas, [])

    def test_la_linea_corporativa_no_cuenta(self):
        self.enviar_encuesta()
        self.s.linea = "corporativa"
        self.vota(self.mafer, "Yo")
        self.assertEqual(db.votos_de(db.ultima_encuesta()["id"]), [])


class TestResultados(BaseEnc):
    def test_cuando_votan_todos_el_dueno_recibe_los_resultados_una_vez(self):
        self.enviar_encuesta()
        self.vota(self.mafer, "Yo")
        self.vota(self.ashly, "No puedo")
        self.assertEqual(self.avisos_a(DUENO), [])  # todavía falta Olenka
        self.vota(self.olenka, "No puedo")
        aviso = self.avisos_a(DUENO)[0]
        self.assertIn("¿Quién cubre el sábado?", aviso)
        self.assertIn("Yo: *1* (Mafer)", aviso)
        self.assertIn("No puedo: *2* (Ashly, Olenka)", aviso)
        self.assertIn("Respondieron todos", aviso)
        self.vota(self.olenka, "Yo")  # cambia de opinión después: no se manda otro resumen
        self.assertEqual(len(self.avisos_a(DUENO)), 1)

    def test_el_dueno_pide_los_resultados_cuando_quiere(self):
        self.enviar_encuesta()
        self.vota(self.mafer, "Yo")
        self.dice(self.dueno, "cómo va la encuesta")
        r = self.ultima
        self.assertIn("Yo: *1* (Mafer)", r)
        self.assertIn("Sin responder (2): Ashly, Olenka", r)

    def test_resultados_con_otras_frases(self):
        self.enviar_encuesta()
        for frase in ("resultados de la encuesta", "quién no ha votado en la encuesta", "cómo va la encuesta?"):
            with self.subTest(frase=frase):
                self.dice(self.dueno, frase)
                self.assertIn("Sin responder (3)", self.ultima)

    def test_sin_encuestas_enviadas(self):
        self.dice(self.dueno, "resultados de la encuesta")
        self.assertIn("Todavía no he enviado ninguna encuesta", self.ultima)

    def test_los_resultados_son_de_la_ultima_encuesta(self):
        self.enviar_encuesta("encuesta ¿Primera? opciones: a, b")
        self.enviar_encuesta("encuesta ¿Segunda? opciones: c, d")
        self.dice(self.dueno, "resultados de la encuesta")
        self.assertIn("¿Segunda?", self.ultima)


class TestRecordatorioUnico(BaseEnc):
    def test_antes_de_los_30_min_no_se_hace_nada(self):
        self.enviar_encuesta()
        self.s.enviados.clear()
        self.assertEqual(self.correr_encuestas(20), [])
        self.assertEqual(self.s.enviados, [])

    def test_a_los_30_min_se_recuerda_solo_a_quien_no_voto_y_una_vez(self):
        self.enviar_encuesta()
        self.vota(self.mafer, "Yo")
        self.s.enviados.clear()
        self.assertEqual(self.correr_encuestas(31), [("recordatorio", db.ultima_encuesta()["id"])])
        self.assertEqual({t for t, _ in self.s.enviados}, {ASHLY, "51900000009"})
        self.assertIn("Te falta responder", self.s.enviados[0][1])
        self.s.enviados.clear()
        self.assertEqual(self.correr_encuestas(60), [])  # no se repite
        self.assertEqual(self.s.enviados, [])

    def test_no_hay_resumen_automatico_por_tiempo_solo_cuando_votan_todos_o_lo_pide_el_dueno(self):
        self.enviar_encuesta()
        self.vota(self.mafer, "Yo")
        self.correr_encuestas(31)
        self.s.enviados.clear()
        self.assertEqual(self.correr_encuestas(500), [])  # ni a las 2 h ni mucho después
        self.assertEqual(self.avisos_a(DUENO), [])

    def test_si_ya_voto_todo_el_mundo_no_hay_recordatorio_ni_resumen_extra(self):
        self.enviar_encuesta()
        for p in (self.mafer, self.ashly, self.olenka):
            self.vota(p, "Yo")
        self.s.enviados.clear()
        self.assertEqual(self.correr_encuestas(200), [])
        self.assertEqual(self.s.enviados, [])

    def test_de_noche_no_se_envia_nada(self):
        self.enviar_encuesta()
        self.s.enviados.clear()
        self.ahora = AHORA + timedelta(hours=14)  # 22:00
        self.assertEqual(logica.revisar_encuestas(self.s), [])
        self.assertEqual(self.s.enviados, [])

    def test_una_encuesta_de_ayer_se_cierra_sin_avisar(self):
        self.enviar_encuesta()
        self.s.enviados.clear()
        self.ahora = AHORA + timedelta(days=1, hours=1)
        self.assertEqual(logica.revisar_encuestas(self.s), [])
        self.assertEqual(self.s.enviados, [])
        self.assertEqual(db.encuestas_sin_cerrar(), [])

    def test_recordar_en_cero_lo_apaga(self):
        self.neg["encuestas"] = {"recordar_min": 0}
        self.enviar_encuesta()
        self.s.enviados.clear()
        self.assertEqual(self.correr_encuestas(60), [])


class TestCheckin(BaseEnc):
    def test_el_checkin_sale_a_todo_el_equipo_una_vez_por_dia(self):
        r = logica.enviar_checkin(self.s)
        self.assertEqual(len(r[1]), 3)
        self.assertEqual(self.s.encuestas[0][1:], (logica.CHECKIN_PREGUNTA, logica.CHECKIN_OPCIONES))
        self.assertEqual(db.ultima_encuesta()["tipo"], "checkin")
        self.assertIsNone(logica.enviar_checkin(self.s))  # el mismo día no se repite
        self.assertEqual(len(self.s.encuestas), 3)

    def test_al_dia_siguiente_vuelve_a_salir(self):
        logica.enviar_checkin(self.s)
        self.ahora = AHORA + timedelta(days=1)
        self.assertIsNotNone(logica.enviar_checkin(self.s))

    def test_sin_equipo_no_envia(self):
        with db.conn() as c:
            c.execute("UPDATE personas SET rol='cliente' WHERE rol='trabajador'")
        self.assertIsNone(logica.enviar_checkin(self.s))

    def test_todo_bien_solo_celebra(self):
        logica.enviar_checkin(self.s)
        self.vota(self.mafer, logica.CHECKIN_OPCIONES[0])
        self.assertIn("Qué bueno", self.ultima)
        self.assertEqual(self.avisos_a(DUENO), [])

    def test_un_problema_pregunta_el_detalle_y_avisa_al_dueno(self):
        logica.enviar_checkin(self.s)
        self.vota(self.mafer, logica.CHECKIN_OPCIONES[1])
        self.assertIn("Cuéntame qué pasa", self.ultima)
        self.assertIn("Mafer", self.avisos_a(DUENO)[0])
        self.dice(self.mafer, "se rompió la autoclave")
        self.assertIn("se rompió la autoclave", self.avisos_a(DUENO)[-1])
        self.assertIn("Anotado", self.ultima)
        self.assertIsNone(db.get_estado(self.mafer["id"]).get("esperando"))

    def test_mas_tiempo_avisa_al_dueno(self):
        logica.enviar_checkin(self.s)
        self.vota(self.ashly, logica.CHECKIN_OPCIONES[2])
        self.assertIn("Ashly", self.avisos_a(DUENO)[0])

    def test_el_dueno_lo_lanza_a_mano_con_confirmacion(self):
        self.dice(self.dueno, "haz el check-in")
        self.assertIn("¿Cómo va tu día?", self.ultima)
        self.assertEqual(self.s.encuestas, [])
        self.dice(self.dueno, "sí")
        self.assertEqual(len(self.s.encuestas), 3)
        self.assertEqual(db.ultima_encuesta()["tipo"], "checkin")

    def test_la_frase_antigua_encuesta_de_prueba_tambien_funciona(self):
        self.dice(self.dueno, "encuesta de prueba")
        self.dice(self.dueno, "sí")
        self.assertEqual(db.ultima_encuesta()["tipo"], "checkin")



class TestConfiguracion(unittest.TestCase):
    def config(self, contenido):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            if contenido is not None:
                with open(f"{d}/negocio.json", "w", encoding="utf-8") as f:
                    json.dump(contenido, f)
            with mock.patch.object(db, "DATA_DIR", d):
                return negocio.config()

    def test_valores_por_defecto(self):
        c = self.config(None)
        self.assertIsNone(c["checkin_hora"])  # el check-in diario está apagado hasta que el negocio lo pida
        self.assertEqual(c["encuestas"], {"recordar_min": 30})
        self.assertTrue(c["botones"])

    def test_se_lee_la_hora_y_los_tiempos(self):
        c = self.config({"checkin": {"hora": "8:30"}, "encuestas": {"recordar_min": 15}})
        self.assertEqual(c["checkin_hora"], "08:30")
        self.assertEqual(c["encuestas"], {"recordar_min": 15})

    def test_una_hora_invalida_apaga_el_checkin(self):
        for hora in ("25:00", "abc", "", None, 8):
            with self.subTest(hora=hora):
                self.assertIsNone(self.config({"checkin": {"hora": hora}})["checkin_hora"])
        self.assertIsNone(self.config({"checkin": "17:30"})["checkin_hora"])  # formato equivocado: no se cae

    def test_tiempos_absurdos_se_acotan(self):
        c = self.config({"encuestas": {"recordar_min": -5}})
        self.assertEqual(c["encuestas"], {"recordar_min": 0})


class TestProblemaEnEncuestasLibres(BaseEnc):
    """Si una encuesta del dueño trae "Tengo un problema" / "Reportar problema", quien la marque cuenta qué pasa."""

    def encuesta(self, opciones="todo bien, tengo un problema"):
        return self.enviar_encuesta(f"encuesta ¿Cómo salió la jornada? opciones: {opciones}")

    def test_marcar_problema_pide_el_detalle_y_avisa_al_dueno(self):
        enc = self.encuesta()
        self.vota(self.mafer, "Tengo un problema", enc)
        self.assertIn("Cuéntame qué pasa", self.ultima)
        self.assertIn("Escribe *no*", self.ultima)
        aviso = self.avisos_a(DUENO)[0]
        self.assertIn("Mafer", aviso)
        self.assertIn("¿Cómo salió la jornada?", aviso)
        self.dice(self.mafer, "se rompió la autoclave")
        self.assertIn("se rompió la autoclave", self.avisos_a(DUENO)[-1])
        self.assertIn("Anotado", self.ultima)

    def test_el_detalle_queda_en_los_resultados(self):
        enc = self.encuesta()
        self.vota(self.mafer, "Tengo un problema", enc)
        self.dice(self.mafer, "se rompió la autoclave")
        self.vota(self.ashly, "Todo bien", enc)
        self.dice(self.dueno, ".resultados")
        self.assertIn("Tengo un problema: *1* (Mafer)", self.ultima)
        self.assertIn("↳ Mafer: se rompió la autoclave", self.ultima)
        self.assertNotIn("↳ Ashly", self.ultima)

    def test_distintas_formas_de_escribir_la_opcion(self):
        for opcion in ("Reportar problema", "Tengo un problema", "Hubo un inconveniente", "Reportar incidencia"):
            with self.subTest(opcion=opcion):
                self.assertTrue(logica._es_problema(opcion))

    def test_sin_problemas_no_es_un_problema(self):
        for opcion in ("Sin problemas", "No tengo problemas", "Todo bien", "Sin ningún inconveniente", "Con ganas"):
            with self.subTest(opcion=opcion):
                self.assertFalse(logica._es_problema(opcion))
        enc = self.encuesta("sin problemas, con problemas")
        self.vota(self.mafer, "Sin problemas", enc)
        self.assertIn("Anotado: Sin problemas", self.ultima)
        self.assertEqual(self.avisos_a(DUENO), [])

    def test_decir_no_omite_los_detalles(self):
        enc = self.encuesta()
        self.vota(self.mafer, "Tengo un problema", enc)
        n = len(self.avisos_a(DUENO))
        self.dice(self.mafer, "no")
        self.assertIn("sin detalles", self.ultima)
        self.assertEqual(len(self.avisos_a(DUENO)), n)  # no se reenvía un "no" como si fuera el problema

    def test_si_pasa_mucho_tiempo_lo_que_escribe_ya_es_otra_cosa(self):
        enc = self.encuesta()
        self.vota(self.mafer, "Tengo un problema", enc)
        n = len(self.avisos_a(DUENO))
        self.ahora = AHORA + timedelta(minutes=25)
        self.dice(self.mafer, "hola")
        self.assertEqual(len(self.avisos_a(DUENO)), n)
        self.assertNotIn("Anotado, se lo aviso", self.ultima)

    def test_el_mismo_voto_dos_veces_no_vuelve_a_preguntar(self):
        enc = self.encuesta()
        self.vota(self.mafer, "Tengo un problema", enc)
        n = len(self.s.respuestas)
        self.vota(self.mafer, "Tengo un problema", enc)
        self.assertEqual(len(self.s.respuestas), n)

    def test_cambiar_de_voto_a_otra_opcion_no_pide_detalle(self):
        enc = self.encuesta()
        self.vota(self.mafer, "Tengo un problema", enc)
        self.dice(self.mafer, "no")
        self.vota(self.mafer, "Todo bien", enc)
        self.assertIn("Cambié tu voto a: Todo bien", self.ultima)

    def test_el_checkin_sigue_igual_y_tambien_guarda_el_detalle(self):
        logica.enviar_checkin(self.s)
        enc = db.ultima_encuesta("checkin")
        self.vota(self.mafer, logica.CHECKIN_OPCIONES[1], enc)
        self.assertIn("Lamento eso", self.ultima)
        self.dice(self.mafer, "no hay luz")
        self.assertIn("no hay luz", self.avisos_a(DUENO)[-1])
        self.dice(self.dueno, ".resultados")
        self.assertIn("↳ Mafer: no hay luz", self.ultima)

    def test_otras_encuestas_sin_problema_siguen_igual(self):
        enc = self.encuesta("yo, no puedo")
        self.vota(self.mafer, "Yo", enc)
        self.assertIn("Anotado: Yo", self.ultima)
        self.assertEqual(self.avisos_a(DUENO), [])
