"""Pruebas del cliente de Gemini con respuestas HTTP simuladas (no gastan cupo ni usan internet)."""
import json
import unittest
from unittest import mock

import httpx

import ia


class Resp:
    def __init__(self, codigo, cuerpo="", headers=None):
        self.status_code = codigo
        self.text = cuerpo if isinstance(cuerpo, str) else json.dumps(cuerpo)
        self.headers = headers or {}

    def json(self):
        return json.loads(self.text)


def ok(texto):
    return Resp(200, {"candidates": [{"content": {"parts": [{"text": texto}]}}]})


class TestLlamar(unittest.TestCase):
    def setUp(self):
        for p in (mock.patch.object(ia.time, "sleep"), mock.patch.object(ia, "API_KEY", "clave-falsa")):
            p.start()
            self.addCleanup(p.stop)

    def llamar(self, *respuestas):
        with mock.patch.object(ia.httpx, "post", side_effect=list(respuestas)) as post:
            try:
                return ia._llamar("s", "c", 0.1), post.call_count
            except ia.GeminiError as e:
                e.llamadas = post.call_count
                raise

    def error(self, *respuestas):
        with self.assertRaises(ia.GeminiError) as cm:
            self.llamar(*respuestas)
        return cm.exception

    def test_ok(self):
        self.assertEqual(self.llamar(ok('{"a": 1}')), ({"a": 1}, 1))

    def test_429_sin_retry_after_es_cuota_y_no_reintenta(self):
        e = self.error(Resp(429, "quota"))
        self.assertEqual((e.kind, e.llamadas), ("cuota", 1))

    def test_429_con_retry_after_corto_reintenta_una_vez(self):
        self.assertEqual(self.llamar(Resp(429, "x", {"Retry-After": "3"}), ok("{}")), ({}, 2))

    def test_503_se_recupera_con_un_reintento(self):
        self.assertEqual(self.llamar(Resp(503, "x"), ok('{"a": 1}')), ({"a": 1}, 2))

    def test_503_dos_veces_es_transitorio(self):
        e = self.error(Resp(503, "x"), Resp(503, "x"))
        self.assertEqual((e.kind, e.llamadas), ("transitorio", 2))

    def test_timeout_dos_veces_es_transitorio(self):
        e = self.error(httpx.ReadTimeout("t"), httpx.ReadTimeout("t"))
        self.assertEqual((e.kind, e.llamadas), ("transitorio", 2))

    def test_404_y_403_son_configuracion_y_no_reintentan(self):
        for codigo in (400, 401, 403, 404):
            e = self.error(Resp(codigo, "x"))
            self.assertEqual((e.kind, e.llamadas), ("config", 1), codigo)

    def test_respuestas_invalidas(self):
        for r in (ok("esto no es json"), Resp(200, {"candidates": []}), Resp(200, {}), ok("[1, 2]")):
            self.assertEqual(self.error(r).kind, "respuesta")

    def test_sin_clave_es_error_de_configuracion(self):
        with mock.patch.object(ia, "API_KEY", None):
            with self.assertRaises(ia.GeminiError) as cm:
                ia._llamar("s", "c", 0.1)
        self.assertEqual(cm.exception.kind, "config")

    def test_el_log_nunca_muestra_la_clave(self):
        with mock.patch("builtins.print") as imprimir:
            self.error(Resp(404, "clave-falsa no existe"))
        self.assertNotIn("clave-falsa", str(imprimir.call_args_list))


class TestInterpretar(unittest.TestCase):
    def interpretar(self, salida_llm, propuesta=None):
        with mock.patch.object(ia, "_llamar", return_value=salida_llm):
            return ia.interpretar_dueno("msg", ["Ashly"], [], [], propuesta)

    def test_limpia_tareas_vacias_y_textos_null(self):
        r = self.interpretar({"accion": "nueva", "tipo": "tareas", "tareas": [
            {"responsable": "Ashly", "actividad": " Bañar ", "detalles": "null", "horario": ""},
            {"responsable": "Ashly", "actividad": "   "},
            "basura",
        ]})
        self.assertEqual(r["datos"]["tareas"],
                         [{"responsable": "Ashly", "actividad": "Bañar", "detalles": None, "horario": None}])

    def test_sin_tareas_utiles_no_hay_accion(self):
        r = self.interpretar({"accion": "nueva", "tipo": "tareas", "tareas": []})
        self.assertEqual((r["accion"], r["tipo"], r["datos"]), ("ninguna", "ninguno", None))

    def test_tareas_que_no_son_lista_es_error(self):
        with self.assertRaises(ia.GeminiError):
            self.interpretar({"accion": "nueva", "tipo": "tareas", "tareas": "no"})

    def test_accion_o_tipo_invalidos_son_error(self):
        for malo in ({"accion": "hacer", "tipo": "tareas"}, {"accion": "nueva", "tipo": "pizza"}):
            with self.assertRaises(ia.GeminiError):
                self.interpretar(malo)

    def test_fecha_mal_formada_se_convierte_en_none(self):
        r = self.interpretar({"accion": "nueva", "tipo": "cita",
                              "cita": {"cliente": "Pedro", "fecha_hora": "mañana a las 10"}})
        self.assertIsNone(r["datos"]["fecha_hora"])

    def test_fecha_valida_se_conserva(self):
        r = self.interpretar({"accion": "nueva", "tipo": "recordatorio",
                              "recordatorio": {"cliente": "Pedro", "fecha_hora": "2030-01-02 10:00"}})
        self.assertEqual(r["datos"]["fecha_hora"], "2030-01-02 10:00")

    def test_charla_devuelve_respuesta(self):
        r = self.interpretar({"accion": "ninguna", "tipo": "ninguno", "respuesta": "¡Hola!"})
        self.assertEqual((r["accion"], r["respuesta"]), ("ninguna", "¡Hola!"))

    def test_el_contexto_incluye_propuesta_y_trabajadores(self):
        with mock.patch.object(ia, "_llamar", return_value={"accion": "ninguna", "tipo": "ninguno"}) as ll:
            ia.interpretar_dueno("hola", ["Ashly", "Mafer"], ["Lucía"],
                                 [{"rol": "usuario", "texto": "antes"}],
                                 {"tipo": "tareas", "datos": {"tareas": []}})
        contexto = ll.call_args[0][1]
        for esperado in ("Ashly, Mafer", "Lucía", "Dueño: antes", "PROPUESTA PENDIENTE", "hola"):
            self.assertIn(esperado, contexto)


if __name__ == "__main__":
    unittest.main()
