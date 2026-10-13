"""Pruebas de la lógica de PatiCare SIN WhatsApp y SIN Gemini (LLM falso).

Ejecutar:  venv\\Scripts\\python.exe -m unittest -v
Cada prueba usa una base de datos temporal; no toca pati.db.
"""
import importlib
import tempfile
import unittest
from datetime import timedelta
from unittest import mock

import db
import ia
import logica


class SalidaFalsa:
    """Registra lo que Pati diría/enviaría en vez de usar WhatsApp."""

    def __init__(self):
        self.respuestas = []
        self.enviados = []  # (telefono, texto)
        self.falla_envio = False

    def responder(self, texto):
        self.respuestas.append(texto)

    def enviar(self, telefono, texto):
        if self.falla_envio:
            raise RuntimeError("sin red")
        self.enviados.append((telefono, texto))

    @property
    def ultima(self):
        return self.respuestas[-1]


class LLMFalso:
    """Reemplaza ia.interpretar_dueno con respuestas preparadas."""

    def __init__(self):
        self.cola = []
        self.llamadas = []

    def poner(self, resultado):
        self.cola.append(resultado)

    def __call__(self, mensaje, trabajadores, clientes, historial, propuesta):
        self.llamadas.append({"mensaje": mensaje, "historial": historial, "propuesta": propuesta,
                              "trabajadores": trabajadores, "clientes": clientes})
        r = self.cola.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def tareas(accion, *items):
    """items: (responsable, actividad, detalles, horario)"""
    return {
        "accion": accion, "tipo": "tareas", "pregunta": None, "respuesta": None,
        "datos": {"tareas": [{"responsable": r, "actividad": a, "detalles": d, "horario": h}
                             for r, a, d, h in items]},
    }


def futuro(dias=1, hora="10:00"):
    return (logica.ahora() + timedelta(days=dias)).strftime(f"%Y-%m-%d {hora}")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        db.DB = f"{self.tmp.name}/test.db"
        db.init()
        with db.conn() as c:
            c.executemany(
                "INSERT INTO personas(telefono, lid, nombre, rol) VALUES(?,?,?,?)",
                [("51900000001", None, "Andrés", "maestro"),
                 ("51900000002", None, "Ashly", "trabajador"),
                 ("51900000003", None, "Mafer", "trabajador"),
                 ("51900000004", None, "Lucía", "cliente")],
            )
        self.llm = LLMFalso()
        p = mock.patch.object(ia, "interpretar_dueno", self.llm)
        p.start()
        self.addCleanup(p.stop)
        self.s = SalidaFalsa()
        self.dueno = self.persona("Andrés")
        self.ashly = self.persona("Ashly")
        self.mafer = self.persona("Mafer")
        self.lucia = self.persona("Lucía")

    def persona(self, nombre):
        with db.conn() as c:
            return c.execute("SELECT * FROM personas WHERE nombre=?", (nombre,)).fetchone()

    def dueno_dice(self, texto):
        logica.procesar(self.s, self.dueno, texto)

    def dice(self, persona, texto):
        logica.procesar(self.s, persona, texto)

    def filas(self, tabla):
        with db.conn() as c:
            return [dict(f) for f in c.execute(f"SELECT * FROM {tabla}")]


class TestDueno(Base):
    def test_dos_tareas_dos_responsables(self):
        self.llm.poner(tareas("nueva", ("Ashly", "Bañar al perro", None, "Ahora"),
                              ("Mafer", "Limpiar el quirófano", None, None)))
        self.dueno_dice("ashly baña al perro y mafer limpia el quirofano")
        self.assertIn("Ashly: Bañar al perro", self.s.ultima)
        self.assertIn("Responde *SÍ* o *NO*", self.s.ultima)
        self.assertEqual(self.filas("tareas"), [])  # nada se guarda antes de confirmar
        self.assertEqual(self.s.enviados, [])

        self.dueno_dice("Sí")
        t = self.filas("tareas")
        self.assertEqual({(x["responsable_id"], x["actividad"]) for x in t},
                         {(self.ashly["id"], "Bañar al perro"), (self.mafer["id"], "Limpiar el quirófano")})
        self.assertEqual({tel for tel, _ in self.s.enviados}, {"51900000002", "51900000003"})
        self.assertIn("Envié estas tareas", self.s.ultima)

    def test_correccion_no_duplica(self):
        self.llm.poner(tareas("nueva", ("Ashly", "Bañar al perro", None, "dentro de 2 min"),
                              ("Mafer", "Bañar al perro", None, None)))
        self.dueno_dice("ashly dentro de 2 min y al resto")
        self.llm.poner(tareas("corregir", ("Ashly", "Bañar al perro", None, "Ahora"),
                              ("Mafer", "Bañar al perro", None, "Ahora")))
        self.dueno_dice("Todos que lo hagan ahora")
        self.assertEqual(self.filas("tareas"), [])  # corregir no crea nada
        self.assertEqual(self.llm.llamadas[1]["propuesta"]["tipo"], "tareas")  # la IA vio la propuesta previa
        self.dueno_dice("sí")
        t = self.filas("tareas")
        self.assertEqual(len(t), 2)
        self.assertEqual({x["horario"] for x in t}, {"Ahora"})

    def test_propuesta_incompleta_pregunta_y_no_confirma(self):
        r = tareas("nueva", (None, "Bañar al perro", None, None))
        r["pregunta"] = "¿Quién baña al perro?"
        self.llm.poner(r)
        self.dueno_dice("baña al perro")
        self.assertIn("❓ ¿Quién baña al perro?", self.s.ultima)
        self.dueno_dice("sí")
        self.assertIn("Todavía falta", self.s.ultima)
        self.assertEqual(self.filas("tareas"), [])
        self.assertEqual(self.s.enviados, [])

    def test_responsable_inventado_se_descarta(self):
        self.llm.poner(tareas("nueva", ("Fantasma", "Bañar al perro", None, None)))
        self.dueno_dice("fantasma baña al perro")
        self.assertIn("sin responsable", self.s.ultima)
        self.dueno_dice("sí")
        self.assertEqual(self.filas("tareas"), [])

    def test_confirmaciones_informales(self):
        for frase in ("sip", "Siii!!", "okkk dale", "ya pe", "SÍ, dale", "sip dale", "de acuerdo"):
            with self.subTest(frase=frase):
                self.llm.poner(tareas("nueva", ("Ashly", "Bañar al perro", None, None)))
                self.dueno_dice("ashly baña al perro")
                self.dueno_dice(frase)
                self.assertEqual(self.filas("tareas")[-1]["actividad"], "Bañar al perro")
                self.assertIn("Envié estas tareas", self.s.ultima)
                db.set_estado(self.dueno["id"], {})

    def test_si_pero_con_cambio_no_confirma(self):
        self.llm.poner(tareas("nueva", ("Ashly", "Bañar al perro", None, None)))
        self.dueno_dice("ashly baña al perro")
        self.llm.poner(tareas("corregir", ("Ashly", "Bañar al perro", None, "17:00")))
        self.dueno_dice("sí pero a las 5")
        self.assertEqual(self.filas("tareas"), [])  # fue una corrección, no una confirmación
        self.assertEqual(len(self.llm.llamadas), 2)

    def test_rechazo_cancela(self):
        self.llm.poner(tareas("nueva", ("Ashly", "Bañar al perro", None, None)))
        self.dueno_dice("ashly baña al perro")
        self.dueno_dice("no")
        self.assertEqual(self.filas("tareas"), [])
        self.assertNotIn("propuesta", db.get_estado(self.dueno["id"]))
        self.dueno_dice("sí")  # ya no hay nada que confirmar
        self.assertEqual(self.filas("tareas"), [])

    def test_si_sin_propuesta_no_ejecuta_ni_llama_a_la_ia(self):
        self.dueno_dice("sí")
        self.assertIn("No tengo nada pendiente", self.s.ultima)
        self.assertEqual(self.llm.llamadas, [])
        self.assertEqual(self.filas("tareas"), [])
        self.assertEqual(self.s.enviados, [])

    def test_doble_si_no_duplica(self):
        self.llm.poner(tareas("nueva", ("Ashly", "Bañar al perro", None, None)))
        self.dueno_dice("ashly baña al perro")
        self.dueno_dice("sí")
        self.dueno_dice("sí")
        self.assertEqual(len(self.filas("tareas")), 1)
        self.assertEqual(len(self.s.enviados), 1)

    def test_error_429_conserva_estado_y_no_ejecuta(self):
        self.llm.poner(tareas("nueva", ("Ashly", "Bañar al perro", None, None)))
        self.dueno_dice("ashly baña al perro")
        antes = db.get_estado(self.dueno["id"])
        self.llm.poner(ia.GeminiError("cuota", "HTTP 429"))
        self.dueno_dice("mejor que sea a las 5")
        self.assertIn("cupo", self.s.ultima)
        self.assertEqual(db.get_estado(self.dueno["id"]), antes)
        self.assertEqual(self.filas("tareas"), [])
        self.assertEqual(self.s.enviados, [])
        self.dueno_dice("sí")  # la propuesta original sigue viva
        self.assertEqual(len(self.filas("tareas")), 1)

    def test_json_invalido_no_ejecuta(self):
        self.llm.poner(ia.GeminiError("respuesta", "JSON inválido"))
        self.dueno_dice("xiomara en 2 min")
        self.assertIn("No logré entender", self.s.ultima)
        self.assertEqual(db.get_estado(self.dueno["id"]), {})
        self.assertEqual(self.filas("tareas"), [])

    def test_reinicio_conserva_propuesta(self):
        self.llm.poner(tareas("nueva", ("Ashly", "Bañar al perro", None, None)))
        self.dueno_dice("ashly baña al perro")
        importlib.reload(logica)  # "reinicio": se pierde todo lo que estuviera en memoria
        self.s = SalidaFalsa()
        logica.procesar(self.s, self.dueno, "sí")
        self.assertEqual(len(self.filas("tareas")), 1)
        self.assertEqual(len(self.s.enviados), 1)

    def test_pedido_nuevo_no_se_mezcla_con_la_propuesta(self):
        self.llm.poner(tareas("nueva", ("Ashly", "Bañar al perro", None, None)))
        self.dueno_dice("ashly baña al perro")
        self.llm.poner(tareas("nueva", ("Mafer", "Limpiar el quirófano", None, None)))
        self.dueno_dice("mafer limpia el quirofano")
        self.assertIn("reemplazo", self.s.ultima)
        self.dueno_dice("sí")  # ambiguo: no confirma ninguna
        self.assertEqual(self.filas("tareas"), [])
        self.dueno_dice("seguir")
        self.dueno_dice("sí")
        self.assertEqual([x["actividad"] for x in self.filas("tareas")], ["Bañar al perro"])

    def test_reemplazar_usa_la_nueva(self):
        self.llm.poner(tareas("nueva", ("Ashly", "Bañar al perro", None, None)))
        self.dueno_dice("ashly baña al perro")
        self.llm.poner(tareas("nueva", ("Mafer", "Limpiar el quirófano", None, None)))
        self.dueno_dice("mafer limpia el quirofano")
        self.dueno_dice("reemplazar")
        self.dueno_dice("sí")
        self.assertEqual([x["actividad"] for x in self.filas("tareas")], ["Limpiar el quirófano"])

    def test_propuesta_caducada(self):
        self.llm.poner(tareas("nueva", ("Ashly", "Bañar al perro", None, None)))
        self.dueno_dice("ashly baña al perro")
        despues = logica.ahora() + timedelta(hours=logica.PROPUESTA_TTL_HORAS + 1)
        with mock.patch.object(logica, "ahora", return_value=despues):
            self.dueno_dice("sí")
        self.assertIn("caducó", self.s.ultima)
        self.assertEqual(self.filas("tareas"), [])

    def test_fallo_al_guardar_no_borra_la_propuesta(self):
        estado = {"propuesta": logica._nueva_propuesta(
            "tareas", {"tareas": [{"responsable": "Ashly", "actividad": "A", "detalles": None, "horario": None},
                                  {"responsable": "NoExiste", "actividad": "B", "detalles": None, "horario": None}]})}
        db.set_estado(self.dueno["id"], estado)
        self.dueno_dice("sí")
        self.assertIn("No pude guardar", self.s.ultima)
        self.assertEqual(self.filas("tareas"), [])  # transacción revertida: ni siquiera la tarea A
        self.assertIn("propuesta", db.get_estado(self.dueno["id"]))

    def test_envio_fallido_guarda_la_tarea_y_avisa(self):
        self.llm.poner(tareas("nueva", ("Ashly", "Bañar al perro", None, None)))
        self.dueno_dice("ashly baña al perro")
        self.s.falla_envio = True
        self.dueno_dice("sí")
        self.assertEqual(len(self.filas("tareas")), 1)
        self.assertIn("no pude enviar", self.s.ultima)
        self.assertEqual([e["tipo"] for e in self.filas("eventos")], ["creada", "envio_fallido"])

    def test_la_ia_recibe_historial_y_propuesta(self):
        self.llm.poner(tareas("nueva", ("Ashly", "Bañar al perro", None, None)))
        self.dueno_dice("ashly baña al perro")
        self.llm.poner(tareas("corregir", ("Ashly", "Bañar al perro", None, "17:00")))
        self.dueno_dice("a las 5")
        llamada = self.llm.llamadas[1]
        self.assertEqual(llamada["mensaje"], "a las 5")
        self.assertEqual([h["rol"] for h in llamada["historial"]], ["usuario", "pati"])  # sin el mensaje actual
        self.assertIsNotNone(llamada["propuesta"])
        self.assertEqual(llamada["trabajadores"], ["Ashly", "Mafer"])

    def test_charla_sin_accion(self):
        self.llm.poner({"accion": "ninguna", "tipo": "ninguno", "datos": None,
                        "pregunta": None, "respuesta": "¡Hola! ¿En qué te ayudo?"})
        self.dueno_dice("hola pati")
        self.assertEqual(self.s.ultima, "¡Hola! ¿En qué te ayudo?")

    def test_recordatorio_se_guarda_al_confirmar(self):
        f = futuro()
        self.llm.poner({"accion": "nueva", "tipo": "recordatorio", "pregunta": None, "respuesta": None,
                        "datos": {"cliente": "Lucía", "telefono": None, "fecha_hora": f,
                                  "mensaje_a_enviar": "Hola Lucía, te esperamos mañana."}})
        self.dueno_dice("recuérdale a lucia mañana a las 10")
        self.assertIn("51900000004", self.s.ultima)  # teléfono tomado de la BD, no inventado
        self.assertEqual(self.filas("recordatorios"), [])
        self.dueno_dice("sí")
        r = self.filas("recordatorios")
        self.assertEqual((r[0]["telefono"], r[0]["fecha_hora"], r[0]["enviado"]), ("51900000004", f, 0))
        self.assertEqual(self.s.enviados, [])  # el envío es programado, no inmediato

    def test_recordatorio_sin_telefono_pregunta(self):
        self.llm.poner({"accion": "nueva", "tipo": "recordatorio", "pregunta": None, "respuesta": None,
                        "datos": {"cliente": "Pedro", "telefono": None, "fecha_hora": futuro(),
                                  "mensaje_a_enviar": "Hola Pedro"}})
        self.dueno_dice("avísale a pedro mañana")
        self.assertIn("❓", self.s.ultima)
        self.assertIn("teléfono", self.s.ultima)
        self.dueno_dice("sí")
        self.assertEqual(self.filas("recordatorios"), [])

    def test_cita_completa_y_telefono_normalizado(self):
        f = futuro(2, "15:00")
        self.llm.poner({"accion": "nueva", "tipo": "cita", "pregunta": None, "respuesta": None,
                        "datos": {"cliente": "Pedro", "telefono": "+51 987 654 321", "mascota": "Luna",
                                  "servicio": "Chequeo", "fecha_hora": f}})
        self.dueno_dice("agenda cita para pedro, chequeo de luna")
        self.dueno_dice("sí")
        c = self.filas("citas")
        self.assertEqual((c[0]["telefono"], c[0]["mascota"], c[0]["fecha_hora"]), ("51987654321", "Luna", f))

    def test_cita_con_fecha_pasada_no_se_confirma(self):
        self.llm.poner({"accion": "nueva", "tipo": "cita", "pregunta": None, "respuesta": None,
                        "datos": {"cliente": "Pedro", "telefono": "987654321", "mascota": "Luna",
                                  "servicio": "Chequeo", "fecha_hora": "2020-01-01 10:00"}})
        self.dueno_dice("cita para pedro")
        self.assertIn("ya pasó", self.s.ultima)
        self.dueno_dice("sí")
        self.assertEqual(self.filas("citas"), [])

    def test_cita_sin_cliente_no_se_confirma(self):
        self.llm.poner({"accion": "nueva", "tipo": "cita", "pregunta": None, "respuesta": None,
                        "datos": {"cliente": None, "telefono": "987654321", "mascota": "Luna",
                                  "servicio": "Chequeo", "fecha_hora": futuro()}})
        self.dueno_dice("cita para luna")
        self.dueno_dice("sí")
        self.assertEqual(self.filas("citas"), [])


class TestTrabajador(Base):
    def test_completar_con_una_tarea(self):
        tid = db.crear_tarea(self.ashly["id"], "Bañar al perro", None, None)
        self.dice(self.ashly, "✅ listo")
        self.assertEqual(self.filas("tareas")[0]["estado"], "completada")
        self.assertIn("completada", self.s.ultima)
        self.assertEqual(tid, 1)

    def test_varias_tareas_elige_numero_y_sobrevive_reinicio(self):
        db.crear_tarea(self.mafer["id"], "Limpiar quirófano", None, None)
        db.crear_tarea(self.mafer["id"], "Preparar vacunas", None, "3:00 p.m.")
        self.dice(self.mafer, "listo")
        self.assertIn("1. Limpiar quirófano", self.s.ultima)
        importlib.reload(logica)
        logica.procesar(self.s, self.mafer, "2")
        estados = {x["actividad"]: x["estado"] for x in self.filas("tareas")}
        self.assertEqual(estados, {"Limpiar quirófano": "pendiente", "Preparar vacunas": "completada"})

    def test_numero_fuera_de_rango_no_cambia_nada(self):
        db.crear_tarea(self.mafer["id"], "A", None, None)
        db.crear_tarea(self.mafer["id"], "B", None, None)
        self.dice(self.mafer, "listo")
        self.dice(self.mafer, "7")
        self.assertIn("No entendí", self.s.ultima)
        self.assertEqual({x["estado"] for x in self.filas("tareas")}, {"pendiente"})

    def test_incidencia_avisa_al_dueno(self):
        db.crear_tarea(self.ashly["id"], "Recoger medicamentos", None, None)
        self.dice(self.ashly, "⚠️ problema")
        self.assertIn("¿Qué pasó", self.s.ultima)
        self.dice(self.ashly, "La farmacia estaba cerrada")
        self.assertEqual(self.filas("tareas")[0]["estado"], "con_incidencia")
        tel, texto = self.s.enviados[0]
        self.assertEqual(tel, "51900000001")
        self.assertIn("La farmacia estaba cerrada", texto)
        self.assertEqual([e["tipo"] for e in self.filas("eventos")], ["creada", "incidencia"])

    def test_que_tengo_pendiente_solo_muestra_las_propias(self):
        db.crear_tarea(self.ashly["id"], "Tarea de Ashly", None, None)
        db.crear_tarea(self.mafer["id"], "Tarea de Mafer", None, None)
        self.dice(self.ashly, "¿qué tengo pendiente?")
        self.assertIn("Tarea de Ashly", self.s.ultima)
        self.assertNotIn("Tarea de Mafer", self.s.ultima)

    def test_no_puede_tocar_tareas_ajenas(self):
        tid = db.crear_tarea(self.mafer["id"], "Tarea de Mafer", None, None)
        self.assertFalse(db.completar_tarea(tid, self.ashly["id"]))
        self.assertFalse(db.incidencia_tarea(tid, "x", self.ashly["id"]))
        self.assertEqual(self.filas("tareas")[0]["estado"], "pendiente")
        # aunque el estado guardado de Ashly apunte a la tarea de Mafer, no se completa
        db.set_estado(self.ashly["id"], {"esperando": "cual_tarea", "tareas": [{"id": tid, "actividad": "x"}]})
        self.dice(self.ashly, "1")
        self.assertEqual(self.filas("tareas")[0]["estado"], "pendiente")
        self.assertIn("ya no está abierta", self.s.ultima)

    def test_sin_tareas(self):
        self.dice(self.ashly, "listo")
        self.assertIn("No tienes tareas pendientes", self.s.ultima)


class TestClienteYResumen(Base):
    def test_cliente_pregunta_servicios(self):
        self.dice(self.lucia, "¿Qué servicios ofrecen?")
        self.assertIn("Vacunación", self.s.ultima)
        self.assertEqual(self.s.enviados, [])

    def test_cliente_quiere_cita_se_deriva_al_dueno(self):
        self.dice(self.lucia, "Quiero agendar una cita")
        self.assertIn("asesor", self.s.ultima)
        tel, texto = self.s.enviados[0]
        self.assertEqual(tel, "51900000001")
        self.assertIn("Lucía", texto)

    def test_resumen_diario(self):
        t1 = db.crear_tarea(self.ashly["id"], "A", None, None)
        db.crear_tarea(self.ashly["id"], "B", None, None)
        t3 = db.crear_tarea(self.mafer["id"], "C", None, None)
        db.completar_tarea(t1, self.ashly["id"])
        db.incidencia_tarea(t3, "x", self.mafer["id"])
        texto = logica.texto_resumen_diario()
        self.assertIn("Asignadas: 2 | Completadas: 1 | Pendientes: 1", texto)
        self.assertIn("Mafer reportó una incidencia", texto)


class TestBase(Base):
    def test_mensaje_repetido_se_detecta(self):
        self.assertTrue(db.registrar_mensaje_id("ABC123"))
        self.assertFalse(db.registrar_mensaje_id("ABC123"))

    def test_ids_distintos_pasan(self):
        self.assertTrue(db.registrar_mensaje_id("A"))
        self.assertTrue(db.registrar_mensaje_id("B"))


if __name__ == "__main__":
    unittest.main()
