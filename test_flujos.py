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
import negocio


class SalidaFalsa:
    """Registra lo que Pati diría/enviaría en vez de usar WhatsApp."""

    def __init__(self, linea="central"):
        self.linea = linea  # "central" (dueño y equipo) o "corporativa" (clientes)
        self.respuestas = []
        self.enviados = []  # (telefono, texto)
        self.falla_envio = False
        self.encuestas = []  # (telefono, pregunta, opciones)
        self.ids_encuestas = []  # id de mensaje que devolvió cada envío
        self.encuestas_multiples = []  # si cada encuesta permitía marcar varias opciones
        self.botones_respuestas = []  # lista de (etiqueta, id) o None, en paralelo a respuestas
        self.botones_enviados = []  # idem, en paralelo a enviados

    def responder(self, texto, botones=None):
        self.respuestas.append(texto)
        self.botones_respuestas.append(botones)

    def enviar(self, telefono, texto, botones=None):
        if self.falla_envio:
            raise RuntimeError("sin red")
        self.enviados.append((telefono, texto))
        self.botones_enviados.append(botones)

    def enviar_encuesta(self, telefono, pregunta, opciones, multiple=False):
        if self.falla_envio:
            raise RuntimeError("sin red")
        self.encuestas.append((telefono, pregunta, list(opciones)))
        self.encuestas_multiples.append(multiple)
        self.ids_encuestas.append(f"ENC-{len(self.encuestas)}")
        return self.ids_encuestas[-1]

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

    def __call__(self, mensaje, trabajadores, clientes, historial, propuesta, negocio=None, dueno=None):
        self.llamadas.append({"mensaje": mensaje, "historial": historial, "propuesta": propuesta,
                              "trabajadores": trabajadores, "clientes": clientes, "negocio": negocio,
                              "dueno": dueno})
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


NEGOCIO_DEMO = {
    **negocio.PERFILES["veterinaria"], "rubro": "veterinaria", "nombre": "Clínica Demo",
    "servicios": ["Consulta general", "Vacunación", "Baño y corte"],
    "horario": "Lunes a sábado, 9:00 a 18:00", "atender_desconocidos": True, "ignorar": [],
    "lineas": {"central": "51900000100", "corporativa": "51900000200"},
    "avisar_completadas": True, "horas_reporte": [], "seguimiento": {"activo": True, "recordar_min_antes": 15, "gracia_min": 10, "avisos_desde": "07:00", "avisos_hasta": "21:00"},
}


class Base(unittest.TestCase):
    def setUp(self):
        self.neg = dict(NEGOCIO_DEMO)
        p = mock.patch.object(negocio, "config", lambda: self.neg)
        p.start()
        self.addCleanup(p.stop)
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
        db.set_estado(self.lucia["id"], {"nombre_ok": True, "nombre_dado": True})  # ya dio su nombre

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
        self.assertIn("Responde con el número", self.s.ultima)
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
        self.s.linea = "corporativa"
        self.dice(self.lucia, "¿Qué servicios ofrecen?")
        self.assertIn("Vacunación", self.s.ultima)
        self.assertEqual(self.s.enviados, [])

    def test_cliente_quiere_cita_se_deriva_al_dueno(self):
        self.s.linea = "corporativa"
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


class TestClientesNuevos(Base):
    def setUp(self):
        super().setUp()
        self.s.linea = "corporativa"  # los clientes escriben por la línea del negocio

    def nuevo(self, tel="51911111111", lid=None, nombre="Rosa", conocido=True):
        """Cliente nuevo. Por defecto ya dio su nombre; conocido=False prueba el primer contacto."""
        p = logica.registrar_desconocido(tel, lid, nombre)
        if p is not None and conocido:
            db.set_estado(p["id"], {"nombre_ok": True, "nombre_dado": True})
        return p

    def test_desconocido_se_registra_como_cliente_una_sola_vez(self):
        a = self.nuevo()
        b = self.nuevo()
        self.assertEqual((a["rol"], a["nombre"]), ("cliente", "Rosa"))
        self.assertEqual(a["id"], b["id"])
        self.assertEqual(len([p for p in self.filas("personas") if p["rol"] == "cliente"]), 2)  # Lucía + Rosa

    def test_sin_nombre_se_llama_cliente(self):
        self.assertEqual(self.nuevo(nombre="  ")["nombre"], "Cliente")

    def test_con_lid_se_vuelve_a_encontrar(self):
        p = self.nuevo(tel="51922222222", lid="9988")
        self.assertEqual(db.persona_por_id("9988")["id"], p["id"])
        self.assertEqual(db.persona_por_id("51922222222")["id"], p["id"])

    def test_negocio_puede_no_atender_desconocidos(self):
        self.neg["atender_desconocidos"] = False
        self.assertIsNone(self.nuevo())

    def test_numeros_ignorados_no_se_atienden(self):
        self.neg["ignorar"] = ["51911111111", "777"]
        self.assertIsNone(self.nuevo(tel="51911111111"))
        self.assertIsNone(self.nuevo(tel="51933333333", lid="777"))
        self.assertIsNotNone(self.nuevo(tel="51944444444"))

    def test_primer_mensaje_muestra_el_menu_con_el_nombre_del_negocio(self):
        p = self.nuevo()
        self.dice(p, "buenas tardes")
        self.assertIn("asistente virtual de Clínica Demo", self.s.ultima)
        for opcion in ("1️⃣", "2️⃣", "3️⃣"):
            self.assertIn(opcion, self.s.ultima)

    def test_opcion_1_lista_solo_los_servicios_reales(self):
        p = self.nuevo()
        self.dice(p, "1")
        for servicio in self.neg["servicios"]:
            self.assertIn(servicio, self.s.ultima)
        self.assertIn("Lunes a sábado", self.s.ultima)
        self.assertNotIn("Cirugía", self.s.ultima)  # nada inventado

    def test_sin_servicios_cargados_no_inventa_y_deriva(self):
        self.neg["servicios"] = []
        p = self.nuevo()
        self.dice(p, "qué servicios tienen")
        self.assertNotIn("•", self.s.ultima)
        self.assertIn("una persona del equipo", self.s.ultima)
        self.assertEqual(self.s.enviados[0][0], "51900000001")  # se avisa al dueño

    def test_opcion_2_y_3_derivan_al_dueno(self):
        p = self.nuevo(nombre="Rosa")
        self.dice(p, "2")
        self.dice(p, "3")
        self.assertEqual(len(self.s.enviados), 2)
        self.assertIn("agendar una cita", self.s.enviados[0][1])
        self.assertIn("hablar con una persona", self.s.enviados[1][1])
        self.assertIn("Rosa", self.s.enviados[1][1])

    def test_no_se_guarda_lo_que_escribe_el_cliente(self):
        p = self.nuevo()
        self.dice(p, "mi número secreto es 123456")
        textos = [m["texto"] for m in self.filas("mensajes")]
        self.assertFalse(any("secreto" in t for t in textos))

    def test_tope_de_respuestas_avisa_una_sola_vez_y_deriva(self):
        p = self.nuevo()
        tope = logica.MAX_RESPUESTAS_CLIENTE_HORA
        for _ in range(tope + 4):
            self.dice(p, "hola")
        self.assertEqual(len(self.s.respuestas), tope + 1)  # las del tope + UN aviso, luego silencio
        self.assertIn("una persona del equipo te escribirá", self.s.respuestas[-1])
        self.assertEqual(len(self.s.enviados), 1)  # una sola alerta al dueño, no una por mensaje
        self.assertIn("muchos mensajes seguidos", self.s.enviados[0][1])

    def test_el_tope_es_una_ventana_movil_y_pati_vuelve_a_responder(self):
        p = self.nuevo()
        for _ in range(logica.MAX_RESPUESTAS_CLIENTE_HORA + 2):
            self.dice(p, "hola")
        antes = len(self.s.respuestas)
        with mock.patch.object(db, "respuestas_recientes", return_value=2):  # pasó la hora
            self.dice(p, "1")
        self.assertEqual(len(self.s.respuestas), antes + 1)
        self.assertIn("Vacunación", self.s.ultima)
        self.assertNotIn("limite_avisado", db.get_estado(p["id"]))  # el aviso se puede dar otra vez

    def test_sin_alcanzar_el_tope_no_hay_aviso(self):
        p = self.nuevo()
        self.dice(p, "hola")
        self.assertNotIn("persona del equipo te escribirá", self.s.ultima)
        self.assertEqual(self.s.enviados, [])


class TestPausaPorHumano(Base):
    def test_se_calla_hasta_5_minutos_despues_del_ultimo_mensaje(self):
        t0 = logica.ahora()
        with mock.patch.object(logica, "ahora", return_value=t0):
            self.assertFalse(logica.chat_pausado("chat1"))
            logica.pausar_por_humano("chat1")
            self.assertTrue(logica.chat_pausado("chat1"))
        with mock.patch.object(logica, "ahora", return_value=t0 + timedelta(minutes=4, seconds=59)):
            self.assertTrue(logica.chat_pausado("chat1"))
        with mock.patch.object(logica, "ahora", return_value=t0 + timedelta(minutes=5, seconds=1)):
            self.assertFalse(logica.chat_pausado("chat1"))

    def test_cada_mensaje_de_la_persona_extiende_la_pausa(self):
        t0 = logica.ahora()
        with mock.patch.object(logica, "ahora", return_value=t0):
            logica.pausar_por_humano("chat1")
        with mock.patch.object(logica, "ahora", return_value=t0 + timedelta(minutes=4)):
            logica.pausar_por_humano("chat1")  # la persona sigue escribiendo
        with mock.patch.object(logica, "ahora", return_value=t0 + timedelta(minutes=8)):
            self.assertTrue(logica.chat_pausado("chat1"))
        with mock.patch.object(logica, "ahora", return_value=t0 + timedelta(minutes=9, seconds=1)):
            self.assertFalse(logica.chat_pausado("chat1"))

    def test_la_pausa_es_solo_de_ese_chat(self):
        logica.pausar_por_humano("chat1")
        self.assertFalse(logica.chat_pausado("chat2"))


class TestRubros(Base):
    def test_dentista_usa_vocabulario_de_paciente(self):
        self.neg.update(negocio.PERFILES["dentista"], rubro="dentista", nombre="Consultorio Sonrisa")
        self.llm.poner({"accion": "nueva", "tipo": "cita", "pregunta": None, "respuesta": None,
                        "datos": {"cliente": "Pedro", "telefono": "987654321", "mascota": "Juan",
                                  "servicio": "Limpieza", "fecha_hora": futuro()}})
        self.dueno_dice("agenda a juan para limpieza")
        self.assertIn("Paciente: Juan", self.s.ultima)
        self.assertNotIn("Mascota", self.s.ultima)
        self.assertEqual(self.llm.llamadas[0]["negocio"]["sujeto"], "paciente")  # la IA recibe el perfil

    def test_recordatorio_de_cita_usa_el_nombre_y_vocabulario_del_negocio(self):
        self.neg.update(negocio.PERFILES["dentista"], nombre="Consultorio Sonrisa")
        texto = logica.mensaje_recordatorio_cita(
            {"mascota": "Juan", "servicio": "Limpieza", "fecha_hora": "2030-01-02 10:00"})
        self.assertIn("Paciente: Juan", texto)
        self.assertIn("Consultorio Sonrisa", texto)

    def test_dentista_el_paciente_es_el_cliente_si_no_se_dice_otro(self):
        self.neg.update(negocio.PERFILES["dentista"])
        self.llm.poner({"accion": "nueva", "tipo": "cita", "pregunta": None, "respuesta": None,
                        "datos": {"cliente": "Juan Pérez", "telefono": "987654321", "mascota": None,
                                  "servicio": "Limpieza", "fecha_hora": futuro()}})
        self.dueno_dice("agenda a juan perez")
        self.assertIn("Paciente: Juan Pérez", self.s.ultima)
        self.assertIn("¿La agendo?", self.s.ultima)

    def test_dentista_puede_ser_otra_persona_si_se_indica(self):
        self.neg.update(negocio.PERFILES["dentista"])
        self.llm.poner({"accion": "nueva", "tipo": "cita", "pregunta": None, "respuesta": None,
                        "datos": {"cliente": "Rosa", "telefono": "987654321", "mascota": "su hijo Luis",
                                  "servicio": "Limpieza", "fecha_hora": futuro()}})
        self.dueno_dice("cita de limpieza para el hijo de rosa, luis")
        self.assertIn("Paciente: su hijo Luis", self.s.ultima)

    def test_veterinaria_si_pregunta_por_la_mascota(self):
        self.llm.poner({"accion": "nueva", "tipo": "cita", "pregunta": None, "respuesta": None,
                        "datos": {"cliente": "Pedro", "telefono": "987654321", "mascota": None,
                                  "servicio": "Vacuna", "fecha_hora": futuro()}})
        self.dueno_dice("cita para pedro")
        self.assertIn("el nombre de la mascota", self.s.ultima)

    def test_falta_el_sujeto_se_pregunta_con_el_vocabulario_correcto(self):
        self.neg.update(negocio.PERFILES["dentista"], sujeto_es_cliente=False)
        self.llm.poner({"accion": "nueva", "tipo": "cita", "pregunta": None, "respuesta": None,
                        "datos": {"cliente": "Pedro", "telefono": "987654321", "mascota": None,
                                  "servicio": "Limpieza", "fecha_hora": futuro()}})
        self.dueno_dice("cita para pedro")
        self.assertIn("el nombre del paciente", self.s.ultima)


class TestNegocioConfig(unittest.TestCase):
    def test_lee_negocio_json_y_aplica_el_perfil(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            with open(f"{d}/negocio.json", "w", encoding="utf-8") as f:
                f.write('{"nombre": "Sonrisa", "rubro": "dentista", "servicios": ["Limpieza"], "ignorar": ["1"]}')
            with mock.patch.object(db, "DATA_DIR", d):
                c = negocio.config()
        self.assertEqual((c["nombre"], c["sujeto"], c["servicios"], c["ignorar"]),
                         ("Sonrisa", "paciente", ["Limpieza"], ["1"]))
        self.assertTrue(c["atender_desconocidos"])

    def test_sin_archivo_usa_valores_neutros_y_no_inventa_servicios(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            with mock.patch.object(db, "DATA_DIR", d):
                c = negocio.config()
        self.assertEqual((c["servicios"], c["horario"], c["nombre"]), ([], None, "nuestro negocio"))

    def test_json_roto_no_tumba_a_pati(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            with open(f"{d}/negocio.json", "w", encoding="utf-8") as f:
                f.write("{esto no es json")
            with mock.patch.object(db, "DATA_DIR", d):
                self.assertEqual(negocio.config()["servicios"], [])

    def test_rubro_desconocido_cae_en_generico(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            with open(f"{d}/negocio.json", "w", encoding="utf-8") as f:
                f.write('{"rubro": "panaderia"}')
            with mock.patch.object(db, "DATA_DIR", d):
                self.assertEqual(negocio.config()["Sujeto"], "Cliente")


class TestDosLineas(Base):
    """La línea decide qué flujo se usa: central = dueño y equipo; corporativa = todos son clientes."""

    def dar_extra(self, nombre, extra):
        with db.conn() as c:
            c.execute("UPDATE personas SET extras=? WHERE nombre=?", (extra, nombre))
        self.dueno, self.ashly, self.mafer = self.persona("Andrés"), self.persona("Ashly"), self.persona("Mafer")

    def test_el_equipo_en_la_linea_corporativa_es_tratado_como_cliente(self):
        self.s.linea = "corporativa"
        db.crear_tarea(self.mafer["id"], "Limpiar", None, None)
        self.dice(self.mafer, "listo")  # en la central esto completaría su tarea
        self.assertIn("asistente virtual de Clínica Demo", self.s.ultima)  # menú de cliente
        self.assertEqual(self.filas("tareas")[0]["estado"], "pendiente")
        self.assertFalse(any("listo" in m["texto"] for m in self.filas("mensajes")))  # no se guarda

    def test_el_dueno_en_la_linea_corporativa_tambien_es_cliente(self):
        self.s.linea = "corporativa"
        self.dice(self.dueno, "hola")
        self.assertEqual(self.llm.llamadas, [])  # no se interpretó como orden del dueño
        self.assertIn("Soy Pati", self.s.ultima)

    def test_las_respuestas_quedan_registradas_con_su_linea(self):
        self.s.linea = "corporativa"
        self.dice(self.lucia, "hola")
        self.assertEqual({m["linea"] for m in self.filas("mensajes")}, {"corporativa"})

    def test_cliente_que_escribe_a_la_central_es_enviado_al_numero_del_negocio(self):
        self.dice(self.lucia, "hola")
        self.assertIn("número interno", self.s.ultima)
        self.assertIn("51900000200", self.s.ultima)
        self.assertEqual(self.s.enviados, [])  # no se molesta a nadie
        for _ in range(5):
            self.dice(self.lucia, "hola")
        self.assertEqual(len(self.s.respuestas), 3)  # no insiste más de 3 veces por hora

    def test_el_tope_de_clientes_cuenta_cada_linea_por_separado(self):
        for _ in range(logica.MAX_RESPUESTAS_CLIENTE_HORA):
            db.log_mensaje(self.lucia["id"], "pati", "x", "central")
        self.s.linea = "corporativa"
        self.dice(self.lucia, "1")
        self.assertIn("Vacunación", self.s.ultima)  # lo de la central no cuenta aquí

    def test_la_derivacion_va_a_quien_tiene_el_rol_atencion_no_al_dueno(self):
        self.dar_extra("Ashly", "atencion")
        self.s.linea = "corporativa"
        self.dice(self.lucia, "3")
        self.assertEqual([tel for tel, _ in self.s.enviados], ["51900000002"])  # solo Ashly

    def test_sin_rol_atencion_la_derivacion_va_al_dueno(self):
        self.s.linea = "corporativa"
        self.dice(self.lucia, "3")
        self.assertEqual([tel for tel, _ in self.s.enviados], ["51900000001"])

    def test_atencion_puede_ser_el_propio_dueno(self):
        self.dar_extra("Andrés", "atencion")
        self.s.linea = "corporativa"
        self.dice(self.lucia, "2")
        self.assertEqual([tel for tel, _ in self.s.enviados], ["51900000001"])

    def test_varias_personas_con_atencion_reciben_el_aviso(self):
        self.dar_extra("Ashly", "atencion")
        self.dar_extra("Mafer", "atencion,trabajador")
        self.s.linea = "corporativa"
        self.dice(self.lucia, "3")
        self.assertEqual({tel for tel, _ in self.s.enviados}, {"51900000002", "51900000003"})

    def test_las_incidencias_siguen_yendo_al_dueno(self):
        self.dar_extra("Ashly", "atencion")
        db.crear_tarea(self.mafer["id"], "Recoger", None, None)
        self.dice(self.mafer, "problema")
        self.dice(self.mafer, "no había nadie")
        self.assertEqual([tel for tel, _ in self.s.enviados], ["51900000001"])

    def test_desconocido_en_la_central_recibe_una_sola_respuesta_informativa_por_hora(self):
        logica._prospectos.clear()
        logica.atender_prospecto(self.s, "51955555555")
        logica.atender_prospecto(self.s, "51955555555")
        self.assertEqual(len(self.s.respuestas), 1)
        self.assertIn("Paticare", self.s.ultima)
        logica.atender_prospecto(self.s, "51966666666")  # otro número sí
        self.assertEqual(len(self.s.respuestas), 2)
        self.assertEqual(self.filas("personas")[-1]["rol"], "cliente")  # no se registró a nadie nuevo
        self.assertEqual(len(self.filas("personas")), 4)

    def test_la_pausa_es_por_linea_y_chat(self):
        logica.pausar_por_humano("corporativa:123")
        self.assertTrue(logica.chat_pausado("corporativa:123"))
        self.assertFalse(logica.chat_pausado("central:123"))


class TestSinBuclesNiMensajesViejos(Base):
    def test_las_lineas_de_pati_se_reconocen_como_propias(self):
        self.assertTrue(logica.es_linea_propia("51900000100"))
        self.assertTrue(logica.es_linea_propia("", "51900000200"))
        self.assertFalse(logica.es_linea_propia("51900000001", ""))
        self.assertFalse(logica.es_linea_propia("", None))

    def test_sin_lineas_configuradas_nada_es_propio(self):
        self.neg["lineas"] = {}
        self.assertFalse(logica.es_linea_propia("51900000100"))

    def test_edad_de_mensajes_en_segundos_y_milisegundos(self):
        ahora_s = 1_800_000_000
        self.assertEqual(logica.edad_segundos(ahora_s - 600, ahora_s), 600)
        self.assertEqual(logica.edad_segundos((ahora_s - 600) * 1000, ahora_s), 600)
        self.assertLess(logica.edad_segundos(ahora_s * 1000, ahora_s), 1)

    def test_limites_de_antiguedad_razonables(self):
        self.assertEqual(logica.MAX_EDAD_MENSAJE_SEG, 600)
        self.assertLess(logica.MAX_EDAD_PROPIO_SEG, logica.MAX_EDAD_MENSAJE_SEG)


class TestDuenoQueTambienTrabaja(Base):
    def dar_extra(self, nombre, extra):
        with db.conn() as c:
            c.execute("UPDATE personas SET extras=? WHERE nombre=?", (extra, nombre))
        self.dueno = self.persona("Andrés")

    def test_por_defecto_el_dueno_no_es_asignable(self):
        self.llm.poner(tareas("nueva", ("Andrés", "Revisar caja", None, None)))
        self.dueno_dice("yo reviso la caja")
        self.assertIn("sin responsable", self.s.ultima)
        self.assertNotIn("Andrés", self.llm.llamadas[0]["trabajadores"])

    def test_con_el_rol_adicional_trabajador_puede_asignarse_tareas(self):
        self.dar_extra("Andrés", "trabajador")
        self.llm.poner(tareas("nueva", ("Andrés", "Revisar caja", None, "17:00")))
        self.dueno_dice("yo reviso la caja a las 5")
        self.assertIn("Andrés: Revisar caja", self.s.ultima)
        self.assertIn("Andrés", self.llm.llamadas[0]["trabajadores"])
        self.assertEqual(self.llm.llamadas[0]["dueno"], "Andrés")  # la IA sabe quién es "yo"
        self.dueno_dice("sí")
        self.assertEqual(self.filas("tareas")[0]["responsable_id"], self.dueno["id"])
        self.assertEqual(self.s.enviados[0][0], "51900000001")  # le llega a él por la central

    def test_el_dueno_completa_su_propia_tarea_con_listo(self):
        self.dar_extra("Andrés", "trabajador")
        db.crear_tarea(self.dueno["id"], "Revisar caja", None, None)
        self.dueno_dice("listo")
        self.assertEqual(self.filas("tareas")[0]["estado"], "completada")
        self.assertEqual(self.llm.llamadas, [])  # sin gastar IA

    def test_el_dueno_pregunta_sus_pendientes(self):
        self.dar_extra("Andrés", "trabajador")
        db.crear_tarea(self.dueno["id"], "Revisar caja", None, None)
        self.dueno_dice("¿qué tengo pendiente?")
        self.assertIn("Revisar caja", self.s.ultima)
        self.assertEqual(self.llm.llamadas, [])

    def test_una_orden_larga_con_la_palabra_listo_no_se_confunde(self):
        self.dar_extra("Andrés", "trabajador")
        db.crear_tarea(self.dueno["id"], "Revisar caja", None, None)
        self.llm.poner(tareas("nueva", ("Ashly", "Bañar al perro", None, None)))
        self.dueno_dice("listo, ahora que ashly bañe al perro")
        self.assertEqual(self.filas("tareas")[0]["estado"], "pendiente")  # su tarea no se completó
        self.assertIn("Ashly: Bañar al perro", self.s.ultima)

    def test_con_varias_tareas_elige_por_numero_y_se_puede_cambiar_de_tema(self):
        self.dar_extra("Andrés", "trabajador")
        db.crear_tarea(self.dueno["id"], "A", None, None)
        db.crear_tarea(self.dueno["id"], "B", None, None)
        self.dueno_dice("listo")
        self.assertIn("¿Cuál terminaste?", self.s.ultima)
        self.llm.poner(tareas("nueva", ("Ashly", "Bañar al perro", None, None)))
        self.dueno_dice("mejor que ashly bañe al perro")  # cambió de tema: no queda atrapado
        self.assertIn("Ashly: Bañar al perro", self.s.ultima)
        self.assertEqual({t["estado"] for t in self.filas("tareas")}, {"pendiente"})

    def test_sin_el_rol_adicional_listo_no_toca_tareas(self):
        db.crear_tarea(self.dueno["id"], "Revisar caja", None, None)
        self.llm.poner({"accion": "ninguna", "tipo": "ninguno", "datos": None,
                        "pregunta": None, "respuesta": "¿Qué necesitas?"})
        self.dueno_dice("listo")
        self.assertEqual(self.filas("tareas")[0]["estado"], "pendiente")  # no se completó
        self.assertEqual(len(self.llm.llamadas), 1)  # se trató como mensaje normal del dueño

    def test_el_resumen_diario_incluye_al_dueno_si_trabaja(self):
        self.dar_extra("Andrés", "trabajador")
        db.crear_tarea(self.dueno["id"], "Revisar caja", None, None)
        self.assertIn("Andrés", logica.texto_resumen_diario())


class TestAvisosYReportes(Base):
    """El dueño se entera cuando alguien completa una tarea y puede pedir el estado cuando quiera."""

    def aviso_al_dueno(self):
        return [t for tel, t in self.s.enviados if tel == "51900000001"]

    def test_al_completar_una_tarea_el_dueno_recibe_un_aviso(self):
        db.crear_tarea(self.mafer["id"], "Limpiar quirófano", None, None)
        self.dice(self.mafer, "listo")
        aviso = self.aviso_al_dueno()
        self.assertEqual(len(aviso), 1)
        self.assertIn("*Mafer* completó: Limpiar quirófano", aviso[0])
        self.assertIn("Van 1 de 1", aviso[0])

    def test_el_aviso_cuenta_el_avance_del_equipo(self):
        db.crear_tarea(self.mafer["id"], "A", None, None)
        db.crear_tarea(self.ashly["id"], "B", None, None)
        db.crear_tarea(self.ashly["id"], "C", None, None)
        self.dice(self.mafer, "listo")
        self.assertIn("Van 1 de 3", self.aviso_al_dueno()[0])

    def test_completar_eligiendo_por_numero_tambien_avisa(self):
        db.crear_tarea(self.mafer["id"], "A", None, None)
        db.crear_tarea(self.mafer["id"], "B", None, None)
        self.dice(self.mafer, "listo")
        self.dice(self.mafer, "2")
        self.assertIn("completó: B", self.aviso_al_dueno()[0])

    def test_el_negocio_puede_apagar_los_avisos(self):
        self.neg["avisar_completadas"] = False
        db.crear_tarea(self.mafer["id"], "A", None, None)
        self.dice(self.mafer, "listo")
        self.assertEqual(self.aviso_al_dueno(), [])

    def test_el_dueno_no_recibe_aviso_de_sus_propias_tareas(self):
        with db.conn() as c:
            c.execute("UPDATE personas SET extras='trabajador' WHERE nombre='Andrés'")
        self.dueno = self.persona("Andrés")
        db.crear_tarea(self.dueno["id"], "Revisar caja", None, None)
        self.dueno_dice("listo")
        self.assertEqual(self.filas("tareas")[0]["estado"], "completada")
        self.assertEqual(self.aviso_al_dueno(), [])

    def preparar_equipo(self):
        db.crear_tarea(self.ashly["id"], "Escribir Hola", None, None)
        db.crear_tarea(self.mafer["id"], "Limpiar quirófano", None, None)
        t3 = db.crear_tarea(self.mafer["id"], "Recoger vacunas", None, None)
        db.completar_tarea(1, self.ashly["id"])
        db.incidencia_tarea(t3, "se escapó la cabra", self.mafer["id"])

    def test_el_reporte_separa_completadas_pendientes_e_incidencias(self):
        self.preparar_equipo()
        texto = logica.texto_reporte_estado()
        self.assertIn("✅ *Completadas* (1)", texto)
        self.assertIn("• Ashly: Escribir Hola", texto)
        self.assertIn("⏳ *Pendientes* (1)", texto)
        self.assertIn("• Mafer: Limpiar quirófano", texto)
        self.assertIn("⚠️ *Con incidencia* (1)", texto)
        self.assertIn("Recoger vacunas — se escapó la cabra", texto)
        self.assertIn("Van 1 de 3 completadas.", texto)

    def test_reporte_de_una_sola_persona(self):
        self.preparar_equipo()
        texto = logica.texto_reporte_estado("Mafer")
        self.assertIn("de Mafer", texto)
        self.assertNotIn("Ashly", texto)
        self.assertIn("Van 0 de 2", texto)

    def test_sin_tareas_no_hay_reporte(self):
        self.assertIsNone(logica.texto_reporte_estado())

    def test_las_tareas_canceladas_no_aparecen(self):
        t = db.crear_tarea(self.mafer["id"], "Vieja", None, None)
        with db.conn() as c:
            c.execute("UPDATE tareas SET estado='cancelada' WHERE id=?", (t,))
        self.assertIsNone(logica.texto_reporte_estado())

    def test_pendientes_de_dias_anteriores_siguen_apareciendo(self):
        t = db.crear_tarea(self.mafer["id"], "Arrastrada", None, None)
        with db.conn() as c:
            c.execute("UPDATE tareas SET creada_en='2020-01-01 10:00:00' WHERE id=?", (t,))
        self.assertIn("Arrastrada", logica.texto_reporte_estado())

    def test_frases_claras_se_reconocen_como_consulta(self):
        for frase in ("¿quienes ya culminaron hasta ahora sus tareas?", "cómo va todo", "como van las tareas",
                      "dame el reporte", "haz el reporte", "qué le falta a mafer", "reporte de tareas",
                      "¿cuántas tareas faltan?", "estado de las tareas", "quien ya hizo su tarea", "avance"):
            with self.subTest(frase=frase):
                self.assertTrue(logica._es_consulta_estado(logica.norm(frase)))

    def test_las_ordenes_normales_no_se_confunden_con_consultas(self):
        for frase in ("ashly baña al perro", "mafer limpia el quirofano pa las 5", "recuérdale a lucía mañana",
                      "sí", "listo", "agenda cita para pedro", "hola", "olenka haz el reporte de ventas",
                      "xiomara termina la presentación hoy"):
            with self.subTest(frase=frase):
                self.assertFalse(logica._es_consulta_estado(logica.norm(frase)))

    def test_el_dueno_pregunta_y_pati_responde_con_datos_sin_usar_ia(self):
        self.preparar_equipo()
        self.dueno_dice("quienes ya culminaron hasta ahora sus tareas?")
        self.assertIn("Estado de las tareas", self.s.ultima)
        self.assertIn("• Ashly: Escribir Hola", self.s.ultima)
        self.assertEqual(self.llm.llamadas, [])

    def test_pregunta_por_una_persona_filtra(self):
        self.preparar_equipo()
        self.dueno_dice("cómo va mafer")
        self.assertIn("de Mafer", self.s.ultima)
        self.assertNotIn("Ashly", self.s.ultima)

    def test_sin_tareas_lo_dice(self):
        self.dueno_dice("cómo va todo")
        self.assertEqual(self.s.ultima, "No hay tareas de hoy ni pendientes.")

    def test_consultar_no_borra_la_propuesta_pendiente(self):
        self.llm.poner(tareas("nueva", ("Ashly", "Bañar al perro", None, None)))
        self.dueno_dice("ashly baña al perro")
        self.dueno_dice("cómo va todo")
        self.assertIn("Sigo esperando tu *SÍ* o *NO*", self.s.ultima)
        self.dueno_dice("sí")
        self.assertEqual(len(self.filas("tareas")), 1)

    def test_si_la_ia_entiende_una_consulta_responde_con_datos_reales(self):
        self.preparar_equipo()
        self.llm.poner({"accion": "consulta", "tipo": "ninguno", "datos": {"persona": None},
                        "pregunta": None, "respuesta": None})
        self.dueno_dice("no, haz el reporte sin consultarles")
        self.assertIn("Estado de las tareas", self.s.ultima)
        self.assertEqual(self.filas("tareas")[0]["estado"], "completada")  # no se creó ninguna tarea nueva
        self.assertEqual(len(self.filas("tareas")), 3)

    def test_consulta_de_la_ia_sobre_una_persona_inexistente(self):
        self.llm.poner({"accion": "consulta", "tipo": "ninguno", "datos": {"persona": "Fantasma"},
                        "pregunta": None, "respuesta": None})
        self.dueno_dice("cómo le fue a fantasma")
        self.assertEqual(self.s.ultima, "No encuentro a Fantasma en el equipo.")


class TestAyuda(Base):
    """Cualquiera puede preguntar qué hace Pati; responde con texto fijo (sin IA) y solo con funciones reales."""

    FRASES = ("que funciones tienes", "¿Qué funciones tienes?", "para qué sirves", "ayuda", "/ayuda", "/help", "Help",
              "qué haces", "qué eres", "quién eres", "cómo funcionas", "qué puedo hacer", "qué puedes hacer",
              "cuáles son tus comandos", "explícame cómo funcionas", "menú", "necesito ayuda", "qué sabes hacer")

    def test_todas_las_formas_de_preguntar_se_reconocen(self):
        for frase in self.FRASES:
            with self.subTest(frase=frase):
                self.assertTrue(logica._es_pedido_ayuda(logica.norm(frase)))

    def test_los_mensajes_normales_no_se_confunden_con_ayuda(self):
        for frase in ("listo", "problema", "qué tengo pendiente", "ashly baña al perro", "no sé cómo funciona la máquina",
                      "sí", "no", "hola", "cómo va todo", "mafer limpia el quirófano pa las 5", "2",
                      "recuérdale a lucía mañana", "la farmacia estaba cerrada"):
            with self.subTest(frase=frase):
                self.assertFalse(logica._es_pedido_ayuda(logica.norm(frase)))

    def test_la_trabajadora_pregunta_que_funciones_tiene(self):
        self.dice(self.mafer, "que funciones tienes")
        texto = self.s.ultima
        for esperado in ("*listo*", "*problema*", "*más tiempo*", "*pendientes*", "Solo veo tus propias tareas"):
            self.assertIn(esperado, texto)
        self.assertLessEqual(len(texto.splitlines()), 4)  # corto: no abruma
        self.assertEqual(self.llm.llamadas, [])  # sin gastar IA
        self.assertEqual(self.s.enviados, [])  # no se molesta a nadie

    def test_la_ayuda_del_trabajador_no_muestra_funciones_del_dueno(self):
        self.dice(self.mafer, "ayuda")
        for prohibido in ("recordatorio", "estado del equipo", "confirmes con sí", "citas para clientes"):
            self.assertNotIn(prohibido, self.s.ultima.lower())

    def test_preguntas_concretas_reciben_una_respuesta_corta(self):
        casos = {"cómo completo una tarea": "*listo*", "cómo reporto un problema": "*problema*",
                 "cómo veo mis tareas": "*qué tengo pendiente*"}
        for pregunta, esperado in casos.items():
            with self.subTest(pregunta=pregunta):
                self.dice(self.mafer, pregunta)
                self.assertIn(esperado, self.s.ultima)
                self.assertIn("Escribe *ayuda*", self.s.ultima)
                self.assertNotIn("Solo veo y modifico", self.s.ultima)  # no es el texto completo

    def test_el_dueno_recibe_su_propia_ayuda_con_lo_que_de_verdad_existe(self):
        self.neg["horas_reporte"] = ["13:00", "17:00"]
        self.dueno_dice("¿qué funciones tienes?")
        texto = self.s.ultima
        for esperado in ("*Tareas:*", "*Recordatorios y citas", "*Estado del equipo:*", "¿cómo va todo?",
                         "a las 13:00, 17:00", "7:00 p. m.", "Nunca envío nada a otras personas sin tu confirmación"):
            self.assertIn(esperado, texto)
        self.assertEqual(self.llm.llamadas, [])

    def test_la_ayuda_del_dueno_nombra_a_quien_atiende(self):
        with db.conn() as c:
            c.execute("UPDATE personas SET extras='atencion' WHERE nombre='Ashly'")
        self.dueno_dice("ayuda")
        self.assertIn("aviso a Ashly si piden hablar con una persona", self.s.ultima)

    def test_sin_nadie_en_atencion_el_aviso_es_para_el_dueno(self):
        self.dueno_dice("ayuda")
        self.assertIn("aviso a ti si piden hablar con una persona", self.s.ultima)

    def test_si_el_dueno_tambien_trabaja_la_ayuda_lo_menciona(self):
        with db.conn() as c:
            c.execute("UPDATE personas SET extras='trabajador' WHERE nombre='Andrés'")
        self.dueno = self.persona("Andrés")
        self.dueno_dice("ayuda")
        self.assertIn("*Tus tareas:*", self.s.ultima)

    def test_pedir_ayuda_no_pierde_la_propuesta_pendiente(self):
        self.llm.poner(tareas("nueva", ("Ashly", "Bañar al perro", None, None)))
        self.dueno_dice("ashly baña al perro")
        self.dueno_dice("ayuda")
        self.assertIn("Sigo esperando tu *SÍ* o *NO*", self.s.ultima)
        self.dueno_dice("sí")
        self.assertEqual(len(self.filas("tareas")), 1)

    def test_pedir_ayuda_en_medio_de_elegir_una_tarea_conserva_el_paso(self):
        db.crear_tarea(self.mafer["id"], "A", None, None)
        db.crear_tarea(self.mafer["id"], "B", None, None)
        self.dice(self.mafer, "listo")
        self.dice(self.mafer, "ayuda")
        self.assertIn("*listo*", self.s.ultima)
        self.dice(self.mafer, "2")
        self.assertEqual({t["actividad"]: t["estado"] for t in self.filas("tareas")},
                         {"A": "pendiente", "B": "completada"})

    def test_si_esta_escribiendo_el_detalle_de_un_problema_no_se_toma_como_ayuda(self):
        db.crear_tarea(self.mafer["id"], "A", None, None)
        self.dice(self.mafer, "problema")
        self.dice(self.mafer, "que funciones tienes")  # aquí es el detalle del problema
        self.assertEqual(self.filas("tareas")[0]["estado"], "con_incidencia")
        self.assertEqual([e["detalle"] for e in self.filas("eventos") if e["tipo"] == "incidencia"],
                         ["que funciones tienes"])

    def test_la_tarea_nueva_es_corta_y_dice_como_responder(self):
        texto = logica.mensaje_trabajador("Mafer", {"actividad": "Limpiar", "detalles": None, "horario": None})
        self.assertLessEqual(len(texto.splitlines()), 4)
        self.assertIn("*listo*", texto)
        self.assertIn("*problema*", texto)

    def test_saludo_responde_con_los_pendientes(self):
        db.crear_tarea(self.mafer["id"], "Limpiar", None, None)
        self.dice(self.mafer, "hola")
        self.assertIn("Limpiar", self.s.ultima)
        self.assertIn("Mafer", self.s.ultima)

    def test_saludo_sin_tareas(self):
        self.dice(self.mafer, "hola")
        self.assertIn("No tienes tareas pendientes", self.s.ultima)

    def test_pregunta_sobre_sus_tareas_muestra_los_pendientes(self):
        db.crear_tarea(self.mafer["id"], "Limpiar", None, None)
        self.dice(self.mafer, "Entendido el dueño me ha asignado tareas?")
        self.assertIn("Limpiar", self.s.ultima)

    def test_completa_varias_a_la_vez(self):
        for n in ("A", "B", "C"):
            db.crear_tarea(self.mafer["id"], n, None, None)
        self.dice(self.mafer, "listo")
        self.dice(self.mafer, "1 y 3")
        self.assertEqual({t["actividad"]: t["estado"] for t in self.filas("tareas")},
                         {"A": "completada", "B": "pendiente", "C": "completada"})
        self.assertEqual(len([e for e in self.filas("eventos") if e["tipo"] == "completada"]), 2)

    def test_completa_todas(self):
        for n in ("A", "B"):
            db.crear_tarea(self.mafer["id"], n, None, None)
        self.dice(self.mafer, "listo")
        self.dice(self.mafer, "todas")
        self.assertEqual({t["estado"] for t in self.filas("tareas")}, {"completada"})

    def test_varias_con_coma_o_espacio(self):
        for n in ("A", "B", "C"):
            db.crear_tarea(self.mafer["id"], n, None, None)
        self.dice(self.mafer, "listo")
        self.dice(self.mafer, "2,3")
        self.assertEqual({t["actividad"]: t["estado"] for t in self.filas("tareas")},
                         {"A": "pendiente", "B": "completada", "C": "completada"})

    def test_varias_no_toca_tareas_fuera_de_la_lista(self):
        db.crear_tarea(self.mafer["id"], "A", None, None)
        db.crear_tarea(self.mafer["id"], "B", None, None)
        self.dice(self.mafer, "listo")
        self.dice(self.mafer, "1 y 9")  # el 9 no existe: no se completa nada
        self.assertEqual({x["estado"] for x in self.filas("tareas")}, {"pendiente"})

    def test_el_cliente_que_pregunta_que_haces_ve_el_menu(self):
        self.s.linea = "corporativa"
        self.dice(self.lucia, "qué funciones tienes")
        self.assertIn("asistente virtual de Clínica Demo", self.s.ultima)
        self.assertIn("1️⃣", self.s.ultima)


class TestBaseDeDatosYPersonas(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        db.DB = f"{self.tmp.name}/t.db"
        db.PERSONAS_JSON = f"{self.tmp.name}/personas.json"

    def test_una_base_antigua_se_migra_sin_perder_datos(self):
        import sqlite3
        c = sqlite3.connect(db.DB)
        c.executescript("""
            CREATE TABLE personas(id INTEGER PRIMARY KEY AUTOINCREMENT, telefono TEXT UNIQUE NOT NULL, lid TEXT,
                nombre TEXT NOT NULL, rol TEXT NOT NULL CHECK(rol IN ('maestro','trabajador','cliente')));
            CREATE TABLE mensajes(id INTEGER PRIMARY KEY AUTOINCREMENT, persona_id INTEGER NOT NULL, rol TEXT NOT NULL,
                texto TEXT NOT NULL, creado_en TEXT NOT NULL DEFAULT (datetime('now','localtime')));
            INSERT INTO personas(telefono, nombre, rol) VALUES('51900000001', 'Vieja', 'maestro');
            INSERT INTO mensajes(persona_id, rol, texto) VALUES(1, 'usuario', 'antiguo');
        """)
        c.commit()
        c.close()
        db.init()
        db.init()  # ejecutarlo dos veces no rompe nada
        with db.conn() as c:
            p = c.execute("SELECT * FROM personas").fetchone()
            m = c.execute("SELECT * FROM mensajes").fetchone()
        self.assertEqual((p["nombre"], p["extras"]), ("Vieja", ""))
        self.assertEqual((m["texto"], m["linea"]), ("antiguo", "central"))

    def test_personas_json_carga_roles_adicionales(self):
        db.init()
        with open(db.PERSONAS_JSON, "w", encoding="utf-8") as f:
            f.write('[{"telefono": "51900000001", "nombre": "Dueña", "rol": "maestro", "tambien": ["atencion", "trabajador"]},'
                    '{"telefono": "51900000002", "nombre": "Sofía", "rol": "trabajador"}]')
        db.cargar_personas()
        self.assertEqual([p["nombre"] for p in db.personas_con_extra("atencion")], ["Dueña"])
        self.assertEqual([p["nombre"] for p in db.asignables()], ["Dueña", "Sofía"])

    def test_recargar_personas_no_borra_el_lid_aprendido(self):
        db.init()
        with open(db.PERSONAS_JSON, "w", encoding="utf-8") as f:
            f.write('[{"telefono": "51900000001", "lid": null, "nombre": "Ana", "rol": "trabajador"}]')
        db.cargar_personas()
        db.guardar_lid(db.persona_por_id("51900000001")["id"], "777")
        db.cargar_personas()  # al reiniciar el bot se vuelve a leer el archivo
        self.assertEqual(db.persona_por_id("51900000001")["lid"], "777")

    def test_roles_adicionales_no_confunden_nombres_parecidos(self):
        db.init()
        with open(db.PERSONAS_JSON, "w", encoding="utf-8") as f:
            f.write('[{"telefono": "51900000001", "nombre": "Ana", "rol": "maestro", "tambien": ["atencion"]}]')
        db.cargar_personas()
        self.assertEqual(db.personas_con_extra("atenc"), [])  # solo coincide la palabra completa
        self.assertEqual(db.asignables(), [])


class TestBase(Base):
    def test_mensaje_repetido_se_detecta(self):
        self.assertTrue(db.registrar_mensaje_id("ABC123"))
        self.assertFalse(db.registrar_mensaje_id("ABC123"))

    def test_ids_distintos_pasan(self):
        self.assertTrue(db.registrar_mensaje_id("A"))
        self.assertTrue(db.registrar_mensaje_id("B"))


if __name__ == "__main__":
    unittest.main()
