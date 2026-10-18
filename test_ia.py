"""Pruebas del cliente de Gemini con respuestas HTTP simuladas (no gastan cupo ni usan internet)."""
import json
import os
import tempfile
import unittest
from unittest import mock

import httpx

import ia

_USO_ORIGINAL = ia.USO_ARCHIVO
_tmp = None


def setUpModule():
    """Las pruebas nunca tocan el contador real (uso_gemini.json)."""
    global _tmp
    _tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    ia.USO_ARCHIVO = os.path.join(_tmp.name, "uso.json")


def tearDownModule():
    ia.USO_ARCHIVO = _USO_ORIGINAL
    _tmp.cleanup()


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
        for p in (mock.patch.object(ia.time, "sleep"), mock.patch.object(ia, "API_KEY", "clave-falsa"),
                  mock.patch.object(ia, "MODELOS", ["modelo-a"])):
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


class TestRespaldoDeModelos(unittest.TestCase):
    """Si un modelo se queda sin cupo o falla, se prueba el siguiente."""

    def setUp(self):
        for p in (mock.patch.object(ia.time, "sleep"), mock.patch.object(ia, "API_KEY", "clave-falsa"),
                  mock.patch.object(ia, "MODELOS", ["modelo-a", "modelo-b"])):
            p.start()
            self.addCleanup(p.stop)

    def llamar(self, *respuestas):
        urls = []

        def post(url, **kw):
            urls.append(url.split("/models/")[1].split(":")[0])
            r = respuestas[len(urls) - 1]
            if isinstance(r, Exception):
                raise r
            return r

        with mock.patch.object(ia.httpx, "post", side_effect=post):
            try:
                return ia._llamar("s", "c", 0.1), urls
            except ia.GeminiError as e:
                e.urls = urls
                raise

    def test_cuota_agotada_pasa_al_siguiente_modelo(self):
        r, urls = self.llamar(Resp(429, "cuota"), ok('{"a": 1}'))
        self.assertEqual((r, urls), ({"a": 1}, ["modelo-a", "modelo-b"]))

    def test_modelo_inexistente_pasa_al_siguiente(self):
        r, urls = self.llamar(Resp(404, "no existe"), ok("{}"))
        self.assertEqual(urls, ["modelo-a", "modelo-b"])

    def test_saturado_dos_veces_pasa_al_siguiente(self):
        r, urls = self.llamar(Resp(503, "x"), Resp(503, "x"), ok("{}"))
        self.assertEqual(urls, ["modelo-a", "modelo-a", "modelo-b"])

    def test_si_el_primero_responde_no_se_toca_el_segundo(self):
        r, urls = self.llamar(ok('{"a": 1}'))
        self.assertEqual(urls, ["modelo-a"])

    def test_si_todos_fallan_se_reporta_el_ultimo_error(self):
        with self.assertRaises(ia.GeminiError) as cm:
            self.llamar(Resp(429, "x"), Resp(429, "x"))
        self.assertEqual((cm.exception.kind, cm.exception.urls), ("cuota", ["modelo-a", "modelo-b"]))

    def test_sin_clave_no_prueba_mas_modelos(self):
        with mock.patch.object(ia, "API_KEY", None), mock.patch.object(ia.httpx, "post") as post:
            with self.assertRaises(ia.GeminiError):
                ia._llamar("s", "c", 0.1)
        post.assert_not_called()

    def test_lista_de_modelos_por_defecto_y_compatibilidad(self):
        with mock.patch.dict(ia.os.environ, {"GEMINI_MODELS": "", "GEMINI_MODEL": "gemini-3.5-flash"}):
            self.assertEqual(ia._lista_modelos(),
                             ["gemini-3.1-flash-lite", "gemini-3.5-flash-lite", "gemini-3.5-flash"])
        with mock.patch.dict(ia.os.environ, {"GEMINI_MODELS": "x, y", "GEMINI_MODEL": ""}):
            self.assertEqual(ia._lista_modelos(), ["x", "y"])


class TestUso(unittest.TestCase):
    """El contador cuenta cada petición enviada (también las que fallan) y avisa al acercarse al límite."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.dir.cleanup)
        for p in (mock.patch.object(ia, "USO_ARCHIVO", os.path.join(self.dir.name, "uso.json")),
                  mock.patch.object(ia.time, "sleep"), mock.patch.object(ia, "API_KEY", "clave-falsa"),
                  mock.patch.object(ia, "MODELOS", ["gemini-3.1-flash-lite", "gemini-3.5-flash-lite"])):
            p.start()
            self.addCleanup(p.stop)

    def usos(self):
        return ia._leer_uso()["modelos"]

    def test_cada_peticion_suma_uno(self):
        with mock.patch.object(ia.httpx, "post", return_value=ok("{}")):
            ia._llamar("s", "c", 0.1)
            ia._llamar("s", "c", 0.1)
        self.assertEqual(self.usos(), {"gemini-3.1-flash-lite": 2})

    def test_los_reintentos_y_los_respaldos_tambien_gastan_cupo(self):
        respuestas = [Resp(503, "x"), Resp(503, "x"), ok("{}")]  # modelo 1 falla 2 veces, el 2 responde
        with mock.patch.object(ia.httpx, "post", side_effect=respuestas):
            ia._llamar("s", "c", 0.1)
        self.assertEqual(self.usos(), {"gemini-3.1-flash-lite": 2, "gemini-3.5-flash-lite": 1})

    def test_las_llamadas_sin_clave_no_cuentan(self):
        with mock.patch.object(ia, "API_KEY", None):
            with self.assertRaises(ia.GeminiError):
                ia._llamar("s", "c", 0.1)
        self.assertEqual(self.usos(), {})

    def test_al_cambiar_el_dia_el_contador_empieza_de_cero(self):
        with mock.patch.object(ia.httpx, "post", return_value=ok("{}")):
            with mock.patch.object(ia, "_dia_de_cupo", return_value="2030-01-01"):
                ia._llamar("s", "c", 0.1)
                self.assertEqual(self.usos(), {"gemini-3.1-flash-lite": 1})
            with mock.patch.object(ia, "_dia_de_cupo", return_value="2030-01-02"):
                self.assertEqual(self.usos(), {})
                ia._llamar("s", "c", 0.1)
                self.assertEqual(self.usos(), {"gemini-3.1-flash-lite": 1})

    def test_archivo_danado_no_rompe_nada(self):
        with open(ia.USO_ARCHIVO, "w", encoding="utf-8") as f:
            f.write("{esto no es json")
        with mock.patch.object(ia.httpx, "post", return_value=ok('{"a": 1}')):
            self.assertEqual(ia._llamar("s", "c", 0.1), {"a": 1})
        self.assertEqual(self.usos(), {"gemini-3.1-flash-lite": 1})

    def test_si_no_se_puede_escribir_el_bot_sigue_funcionando(self):
        with mock.patch.object(ia, "USO_ARCHIVO", os.path.join(self.dir.name, "no_existe", "uso.json")):
            with mock.patch.object(ia.httpx, "post", return_value=ok('{"a": 1}')):
                self.assertEqual(ia._llamar("s", "c", 0.1), {"a": 1})

    def test_avisa_al_llegar_al_80_por_ciento(self):
        with mock.patch.object(ia, "_log") as log:
            with mock.patch.object(ia.httpx, "post", return_value=ok("{}")):
                for _ in range(400):
                    ia._llamar("s", "c", 0.1)
        avisos = [c.args[0] for c in log.call_args_list if "lleva" in c.args[0]]
        self.assertEqual(avisos[0], "AVISO: gemini-3.1-flash-lite lleva 400/500 peticiones hoy")

    def test_no_avisa_antes_del_80_por_ciento(self):
        with mock.patch.object(ia, "_log") as log:
            with mock.patch.object(ia.httpx, "post", return_value=ok("{}")):
                for _ in range(399):
                    ia._llamar("s", "c", 0.1)
        self.assertFalse([c for c in log.call_args_list if "lleva" in c.args[0]])

    def test_resumen_muestra_modelos_con_su_limite(self):
        with mock.patch.object(ia.httpx, "post", return_value=ok("{}")):
            for _ in range(5):
                ia._llamar("s", "c", 0.1)
        texto = ia.resumen_uso()
        self.assertIn("gemini-3.1-flash-lite: 5/500 (1%)", texto)
        self.assertIn("gemini-3.5-flash-lite: 0/500 (0%)", texto)


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
                         [{"responsable": "Ashly", "actividad": "Bañar", "detalles": None, "horario": None, "vence": None}])

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

    def test_consulta_de_estado_devuelve_la_persona_o_none(self):
        r = self.interpretar({"accion": "consulta", "tipo": "ninguno", "consulta": {"persona": " Mafer "}})
        self.assertEqual((r["accion"], r["tipo"], r["datos"]), ("consulta", "ninguno", {"persona": "Mafer"}))
        r = self.interpretar({"accion": "consulta", "tipo": "ninguno", "consulta": {"persona": "null"}})
        self.assertIsNone(r["datos"]["persona"])
        r = self.interpretar({"accion": "consulta", "tipo": "ninguno"})  # sin objeto: igual es una consulta general
        self.assertEqual((r["accion"], r["datos"]), ("consulta", {"persona": None}))

    def test_el_prompt_explica_la_consulta_y_prohibe_inventar_el_estado(self):
        with mock.patch.object(ia, "_llamar", return_value={"accion": "ninguna", "tipo": "ninguno"}) as ll:
            ia.interpretar_dueno("hola", [], [], [], None)
        sistema = ll.call_args[0][0]
        self.assertIn('"consulta"', sistema)
        self.assertIn("NUNCA inventes el estado", sistema)

    def test_el_plazo_se_conserva_solo_si_tiene_el_formato_correcto(self):
        r = self.interpretar({"accion": "nueva", "tipo": "tareas", "tareas": [
            {"responsable": "Ashly", "actividad": "A", "vence": "2030-01-07 17:00"},
            {"responsable": "Ashly", "actividad": "B", "vence": "mañana a las 5"},
            {"responsable": "Ashly", "actividad": "C", "vence": "null"},
            {"responsable": "Ashly", "actividad": "D", "vence": "2030-13-45 99:99"},
            {"responsable": "Ashly", "actividad": "E"},
        ]})
        self.assertEqual([t["vence"] for t in r["datos"]["tareas"]], ["2030-01-07 17:00", None, None, None, None])

    def test_el_prompt_explica_el_plazo_y_prohibe_inventar_horas(self):
        with mock.patch.object(ia, "_llamar", return_value={"accion": "ninguna", "tipo": "ninguno"}) as ll:
            ia.interpretar_dueno("hola", [], [], [], None)
        sistema = ll.call_args[0][0]
        self.assertIn('"vence"', sistema)
        self.assertIn("NUNCA inventes una hora", sistema)

    def test_el_prompt_se_adapta_al_rubro(self):
        import negocio
        for rubro, esperado in (("dentista", "consultorio dental"), ("veterinaria", "veterinaria")):
            with mock.patch.object(ia, "_llamar", return_value={"accion": "ninguna", "tipo": "ninguno"}) as ll:
                ia.interpretar_dueno("hola", [], [], [], None, negocio=negocio.PERFILES[rubro])
            sistema = ll.call_args[0][0]
            self.assertIn(esperado, sistema)
            self.assertNotIn("{", sistema.split("Responde SOLO")[0])  # sin marcadores sin reemplazar
            self.assertNotIn("{sujeto}", sistema)
            self.assertNotIn("{del_sujeto}", sistema)

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
