"""Comandos del dueño (mensajes que empiezan con punto) y los casos del chat que fallaban."""
from datetime import datetime, timedelta

import comandos
import db
import logica
from test_encuestas import BaseEnc
from test_flujos import tareas
from test_seguimiento import AHORA, ASHLY, DUENO, MAFER, hora


class TestReconocerComandos(BaseEnc):
    def test_solo_es_comando_si_el_punto_va_al_inicio(self):
        self.llm.poner({"accion": "ninguna", "tipo": "ninguno", "datos": {}, "pregunta": None, "respuesta": "ia"})
        self.dice(self.dueno, "mafer limpia. ashly baña .estado")
        self.assertEqual(self.ultima, "ia")  # el punto en medio del texto no cuenta

    def test_no_son_comandos_numeros_ni_puntos_suspensivos(self):
        for texto in (".5 kilos", "...", ". ", "..ayuda", "1.ayuda", "ayuda"):
            with self.subTest(texto=texto):
                self.assertIsNone(comandos.COMANDO_RE.match(texto))

    def test_acepta_espacios_mayusculas_y_tildes(self):
        for texto in (".AYUDA", "  .ayuda", ". ayuda", ".Ayuda  ", ".ayúda"):
            with self.subTest(texto=texto):
                self.s.respuestas.clear()
                self.dice(self.dueno, texto)
                self.assertIn("Comandos", self.ultima)

    def test_comando_desconocido_sugiere_el_parecido(self):
        self.dice(self.dueno, ".estdo")
        self.assertIn("¿Quisiste decir *.estado*?", self.ultima)
        self.dice(self.dueno, ".zzzzzz")
        self.assertIn(".ayuda", self.ultima)
        self.assertNotIn("Quisiste", self.ultima)
        self.assertEqual(self.llm.llamadas, [])

    def test_ningun_comando_gasta_ia(self):
        for c in (".ayuda", ".estado", ".resultados", ".historial mafer", ".agenda hoy", ".recordar", ".resolver"):
            self.dice(self.dueno, c)
        self.assertEqual(self.llm.llamadas, [])

    def test_los_trabajadores_no_pueden_usar_comandos(self):
        a = self.tarea(self.ashly, "Caja")
        self.dice(self.mafer, ".cancelar ashly")
        self.assertEqual(self.fila(a)["estado"], "pendiente")
        self.assertNotIn("Comandos", self.ultima)

    def test_alias(self):
        self.dice(self.dueno, ".comandos")
        self.assertIn("Comandos", self.ultima)
        self.dice(self.dueno, ".check-in")
        self.assertIn("¿Cómo va tu día?", self.ultima)


class TestTarea(BaseEnc):
    def test_tarea_con_plazo(self):
        self.dice(self.dueno, ".tarea mafer y ashly: bañar a firulais para las 5")
        r = self.ultima
        self.assertIn("1. Mafer: Bañar a firulais (⏰ hoy 17:00)", r)
        self.assertIn("2. Ashly: Bañar a firulais (⏰ hoy 17:00)", r)
        self.assertNotIn("para las 5", r.split("(")[0])  # el plazo salió del texto de la tarea
        self.assertEqual(self.s.enviados, [])  # nada sale sin el sí
        self.assertEqual(self.llm.llamadas, [])

    def test_el_si_envia_las_tareas_con_el_plazo(self):
        self.dice(self.dueno, ".tarea mafer: limpiar el quirófano en 30 minutos")
        self.dice(self.dueno, "sí")
        t = db.tareas_pendientes(self.mafer["id"])[0]
        self.assertEqual(t["actividad"], "Limpiar el quirófano")
        self.assertEqual(t["vence_en"], hora(30))
        self.assertIn("Limpiar el quirófano", self.avisos_a(MAFER)[0])

    def test_sin_plazo(self):
        self.dice(self.dueno, ".tarea ashly: atender la caja")
        self.assertIn("1. Ashly: Atender la caja", self.ultima)
        self.assertNotIn("⏰", self.ultima)

    def test_todos(self):
        self.dice(self.dueno, ".tarea todos: revisar el inventario mañana a las 10")
        self.assertIn("Olenka", self.ultima)
        self.assertIn("mañana 10:00", self.ultima)
        self.assertNotIn("Andrés:", self.ultima)

    def test_la_hora_dentro_del_texto_no_se_confunde(self):
        self.dice(self.dueno, ".tarea mafer: preparar la sala 3 y llamar al cliente")
        self.assertIn("Preparar la sala 3 y llamar al cliente", self.ultima)
        self.assertNotIn("⏰", self.ultima)

    def test_persona_que_no_existe(self):
        self.dice(self.dueno, ".tarea juancho: barrer")
        self.assertIn("No encuentro a *juancho*", self.ultima)
        self.assertEqual(db.get_estado(self.dueno["id"]).get("propuesta"), None)

    def test_sin_dos_puntos(self):
        self.dice(self.dueno, ".tarea mafer limpiar")
        self.assertIn("separar las personas", self.ultima)

    def test_sin_actividad(self):
        self.dice(self.dueno, ".tarea mafer:")
        self.assertIn("¿Qué deben hacer?", self.ultima)

    def test_con_una_propuesta_pendiente_pide_resolverla_primero(self):
        self.dice(self.dueno, ".tarea mafer: limpiar")
        self.dice(self.dueno, ".tarea ashly: barrer")
        self.assertIn("Primero responde *SÍ* o *NO*", self.ultima)
        self.dice(self.dueno, "no")
        self.dice(self.dueno, ".tarea ashly: barrer")
        self.assertIn("Ashly: Barrer", self.ultima)


class TestComandosDeLectura(BaseEnc):
    def test_estado_general_y_de_una_persona(self):
        self.tarea(self.mafer, "Limpiar")
        self.tarea(self.ashly, "Caja")
        self.dice(self.dueno, ".estado")
        self.assertIn("Mafer: Limpiar", self.ultima)
        self.assertIn("Ashly: Caja", self.ultima)
        self.dice(self.dueno, ".estado mafer")
        self.assertIn("Mafer: Limpiar", self.ultima)
        self.assertNotIn("Ashly", self.ultima)

    def test_estado_de_todos_o_del_equipo_es_el_reporte_general(self):
        self.tarea(self.mafer, "Limpiar")
        self.tarea(self.ashly, "Caja")
        for args in ("todos", "equipo", "todo el equipo", "TODAS"):
            with self.subTest(args=args):
                self.dice(self.dueno, f".estado {args}")
                self.assertIn("Mafer: Limpiar", self.ultima)
                self.assertIn("Ashly: Caja", self.ultima)
                self.assertNotIn("No encuentro", self.ultima)

    def test_estado_de_alguien_que_no_existe(self):
        self.dice(self.dueno, ".estado juancho")
        self.assertIn("No encuentro a *juancho*", self.ultima)

    def test_resultados(self):
        self.enviar_encuesta()
        self.vota(self.mafer, "Yo")
        self.dice(self.dueno, ".resultados")
        self.assertIn("Yo: *1* (Mafer)", self.ultima)
        self.assertIn("Sin responder (2)", self.ultima)

    def test_resultados_sin_encuestas(self):
        self.dice(self.dueno, ".resultados")
        self.assertIn("Todavía no he enviado ninguna encuesta", self.ultima)

    def test_historial(self):
        a = self.tarea(self.mafer, "Limpiar")
        db.completar_tarea(a, self.mafer["id"])
        self.dice(self.dueno, ".historial mafer")
        self.assertIn("completada", self.ultima)

    def test_lectura_sigue_funcionando_con_una_propuesta_pendiente(self):
        self.dice(self.dueno, ".tarea mafer: limpiar")
        self.dice(self.dueno, ".estado")
        self.assertNotIn("Primero responde", self.ultima)
        self.dice(self.dueno, ".ayuda")
        self.assertIn("Comandos", self.ultima)
        self.dice(self.dueno, "sí")  # y la propuesta seguía ahí
        self.assertEqual(len(db.tareas_pendientes(self.mafer["id"])), 1)


class TestComandosDeEncuesta(BaseEnc):
    def test_encuesta_con_opciones(self):
        self.dice(self.dueno, ".encuesta ¿Quién cubre el sábado? opciones: yo, no puedo")
        self.assertIn("¿Quién cubre el sábado?", self.ultima)
        self.assertIn("¿La envío?", self.ultima)
        self.dice(self.dueno, "sí")
        self.assertEqual(self.s.encuestas[0][2], ["Yo", "No puedo"])

    def test_encuesta_sin_opciones_es_si_no(self):
        self.dice(self.dueno, ".encuesta ¿Vienen mañana?")
        self.assertIn("Sí", self.ultima)

    def test_encuesta_sin_texto_explica(self):
        self.dice(self.dueno, ".encuesta")
        self.assertIn("Por ejemplo", self.ultima)

    def test_el_caso_exacto_del_chat_con_opciones_desordenadas(self):
        self.dice(self.dueno, ".encuesta ¿estan libres a la 1 para ir al veterinario? "
                              "opciones: yo sí  xq soi chevere, yo no srry amistad , tengo un problema")
        self.assertIn("Yo sí xq soi chevere", self.ultima)
        self.assertIn("Yo no srry amistad", self.ultima)
        self.assertIn("Tengo un problema", self.ultima)

    def test_checkin(self):
        self.dice(self.dueno, ".checkin")
        self.dice(self.dueno, "sí")
        self.assertEqual(db.ultima_encuesta()["tipo"], "checkin")


class TestComandosDeControl(BaseEnc):
    def test_cancelar_una_persona_y_todas(self):
        a = self.tarea(self.mafer, "Limpiar")
        b = self.tarea(self.ashly, "Caja")
        self.dice(self.dueno, ".cancelar mafer")
        self.dice(self.dueno, "sí")
        self.assertEqual((self.fila(a)["estado"], self.fila(b)["estado"]), ("cancelada", "pendiente"))
        self.dice(self.dueno, ".cancelar todas")
        self.dice(self.dueno, "sí")
        self.assertEqual(self.fila(b)["estado"], "cancelada")

    def test_cancelar_una_tarea_para_todos_por_su_nombre(self):
        a = self.tarea(self.mafer, "Probar los botones")
        b = self.tarea(self.ashly, "Probar los botones")
        c = self.tarea(self.olenka, "Probar los botones")
        otra = self.tarea(self.mafer, "Limpiar el quirófano")
        for orden in (".cancelar probar los botones", ".cancelar todos probar los botones", ".cancelar equipo botones"):
            with self.subTest(orden=orden):
                self.dice(self.dueno, orden)
                self.assertIn("Probar los botones", self.ultima)
                self.assertNotIn("quirófano", self.ultima)  # solo esa tarea
                self.dice(self.dueno, "no")
        self.dice(self.dueno, ".cancelar probar los botones")
        self.dice(self.dueno, "sí")
        self.assertEqual({self.fila(x)["estado"] for x in (a, b, c)}, {"cancelada"})
        self.assertEqual(self.fila(otra)["estado"], "pendiente")
        for tel in (MAFER, ASHLY, "51900000009"):
            self.assertIn("se canceló", self.avisos_a(tel)[0])

    def test_cancelar_por_nombre_sin_coincidencias(self):
        self.tarea(self.mafer, "Limpiar")
        self.dice(self.dueno, ".cancelar fumigar la bodega")
        self.assertIn("No encuentro tareas pendientes con *fumigar la bodega*", self.ultima)

    def test_cancelar_a_una_persona_con_palabras_sigue_filtrando_solo_a_ella(self):
        a = self.tarea(self.mafer, "Probar los botones")
        b = self.tarea(self.ashly, "Probar los botones")
        self.dice(self.dueno, ".cancelar mafer botones")
        self.dice(self.dueno, "sí")
        self.assertEqual((self.fila(a)["estado"], self.fila(b)["estado"]), ("cancelada", "pendiente"))

    def test_reasignar(self):
        a = self.tarea(self.mafer, "Limpiar")
        self.dice(self.dueno, ".reasignar mafer a ashly")
        self.dice(self.dueno, "sí")
        self.assertEqual(self.fila(a)["responsable_id"], self.ashly["id"])

    def test_plazo(self):
        a = self.tarea(self.mafer, "Limpiar", hora(10))
        self.dice(self.dueno, ".plazo mafer 6pm")
        self.dice(self.dueno, "sí")
        self.assertEqual(self.fila(a)["vence_en"], AHORA.strftime("%Y-%m-%d") + " 18:00")

    def test_recordar_a_todos_y_a_algunos(self):
        self.tarea(self.mafer, "Limpiar")
        self.tarea(self.ashly, "Caja")
        self.tarea(self.olenka, "Vacunas")
        self.dice(self.dueno, ".recordar")
        self.assertIn("Mafer (1)", self.ultima)
        self.assertIn("Olenka (1)", self.ultima)
        self.dice(self.dueno, "no")
        self.dice(self.dueno, ".recordar mafer ashly")
        self.assertIn("Ashly (1)", self.ultima)
        self.assertNotIn("Olenka", self.ultima)

    def test_recordar_a_alguien_que_no_existe(self):
        self.dice(self.dueno, ".recordar juancho")
        self.assertIn("No encuentro a *juancho*", self.ultima)

    def test_resolver_incidencias(self):
        a = self.tarea(self.mafer, "Limpiar")
        db.incidencia_tarea(a, "se escapó la cabra", self.mafer["id"])
        self.dice(self.dueno, ".resolver")
        self.dice(self.dueno, "sí")
        self.assertEqual(self.fila(a)["estado"], "pendiente")

    def test_aviso_al_equipo(self):
        self.dice(self.dueno, ".aviso mañana abrimos a las 8")
        self.assertIn("Mañana abrimos a las 8", self.ultima)
        self.dice(self.dueno, "sí")
        self.assertIn("Mañana abrimos a las 8", self.avisos_a(MAFER)[0])

    def test_aviso_vacio(self):
        self.dice(self.dueno, ".aviso")
        self.assertIn("¿Qué aviso les mando?", self.ultima)

    def test_deshacer(self):
        self.llm.poner(tareas("nueva", ("Mafer", "Limpiar", None, None)))
        self.dice(self.dueno, "mafer limpia")
        self.dice(self.dueno, "sí")
        self.dice(self.dueno, ".deshacer")
        self.dice(self.dueno, "sí")
        self.assertEqual(db.tareas_pendientes(self.mafer["id"]), [])

    def test_los_comandos_que_avisan_a_otros_esperan_el_si(self):
        a = self.tarea(self.mafer, "Limpiar")
        for c in (".cancelar mafer", ".recordar", ".aviso hola", ".plazo mafer 6pm", ".reasignar mafer a ashly"):
            self.s.enviados.clear()
            self.dice(self.dueno, c)
            self.assertEqual(self.s.enviados, [], c)
            self.dice(self.dueno, "no")
        self.assertEqual(self.fila(a)["estado"], "pendiente")

    def test_un_comando_de_control_con_propuesta_pendiente_espera(self):
        self.dice(self.dueno, ".tarea mafer: limpiar")
        for c in (".cancelar mafer", ".recordar", ".encuesta ¿Vienes?", ".checkin", ".deshacer"):
            self.dice(self.dueno, c)
            self.assertIn("Primero responde *SÍ* o *NO*", self.ultima, c)


class TestAgenda(BaseEnc):
    def test_agenda_por_comando(self):
        self.dice(self.dueno, ".agenda hoy")
        self.assertTrue(self.ultima)
        self.assertEqual(self.llm.llamadas, [])


class TestCasosDelChat(BaseEnc):
    """Frases que antes terminaban en tareas inventadas o en el reporte equivocado."""

    def crear(self):
        self.a = self.tarea(self.mafer, "Limpiar el quirófano")
        db.incidencia_tarea(self.a, "se escapó la cabra", self.mafer["id"])
        self.b = self.tarea(self.ashly, "Caja")

    def test_limpia_las_incidencias_y_recuerdales_sus_tareas_pendientes_y_atrasadas(self):
        self.crear()
        self.dice(self.dueno, "limpia las incidencias, y recuerdales sus tareas pendientes y atrasadas")
        self.assertIn("resueltas* 1 incidencia", self.ultima)
        self.assertIn("Recordarle a cada uno", self.ultima)
        self.assertEqual(self.llm.llamadas, [])  # ya no se la pasa a la IA (que inventaba "tareas de recordatorio")
        self.dice(self.dueno, "sí")
        self.assertEqual(self.fila(self.a)["estado"], "pendiente")
        self.assertIn("Limpiar el quirófano", self.avisos_a(MAFER)[0])

    def test_recordar_no_crea_tareas_de_recordatorio(self):
        self.crear()
        self.dice(self.dueno, "recuerdales sus tareas pendientes")
        self.dice(self.dueno, "sí")
        self.assertEqual(len(db.estado_tareas()), 2)  # las mismas de antes

    def test_sí_reemplaza_elige_la_propuesta_nueva(self):
        self.llm.poner(tareas("nueva", ("Mafer", "Limpiar", None, None)))
        self.dice(self.dueno, "mafer limpia")
        self.llm.poner(tareas("nueva", ("Ashly", "Caja", None, None)))
        self.dice(self.dueno, "ashly atiende la caja")  # otra cosa distinta: pregunta
        self.assertIn("reemplazo", self.ultima.lower())
        self.dice(self.dueno, "sí reemplaza")
        self.assertIn("Ashly: Caja", self.ultima)
        self.assertNotIn("Primero dime", self.ultima)

    def test_otras_formas_de_elegir(self):
        for frase, esperado in (("reemplázala", "Ashly"), ("la nueva por favor", "Ashly"), ("sigo con la anterior", "Mafer"),
                                ("seguir", "Mafer"), ("si, continuar con la anterior", "Mafer")):
            with self.subTest(frase=frase):
                db.set_estado(self.dueno["id"], {})
                self.llm.poner(tareas("nueva", ("Mafer", "Limpiar", None, None)))
                self.dice(self.dueno, "mafer limpia")
                self.llm.poner(tareas("nueva", ("Ashly", "Caja", None, None)))
                self.dice(self.dueno, "ashly atiende la caja")
                self.dice(self.dueno, frase)
                self.assertIn(esperado, self.ultima)

    def test_una_respuesta_ambigua_sigue_preguntando(self):
        self.llm.poner(tareas("nueva", ("Mafer", "Limpiar", None, None)))
        self.dice(self.dueno, "mafer limpia")
        self.llm.poner(tareas("nueva", ("Ashly", "Caja", None, None)))
        self.dice(self.dueno, "ashly atiende la caja")
        self.dice(self.dueno, "reemplazo o sigo")  # menciona las dos: no se decide por él
        self.assertIn("Primero dime", self.ultima)


class TestCancelarPorEncuesta(BaseEnc):
    """".cancelar" sin decir cuál: llega una encuesta (varias opciones) con las tareas para marcar; luego el SÍ."""

    def crear(self):
        self.p1 = self.tarea(self.mafer, "Probar los botones")
        self.p2 = self.tarea(self.ashly, "Probar los botones")
        self.p3 = self.tarea(self.olenka, "Probar los botones")
        self.q = self.tarea(self.mafer, "Limpiar el quirófano")
        self.c = self.tarea(self.ashly, "Caja")
        self.dice(self.dueno, ".cancelar")
        self.enc = db.encuesta(1)

    def marcar(self, *etiquetas, continuar=True):
        opciones = list(etiquetas) + ([logica.CONTINUAR] if continuar else [])
        logica.procesar_seleccion(self.s, self.dueno, self.enc["id"], opciones)

    def test_manda_al_dueno_una_encuesta_de_seleccion_multiple_agrupada_por_tarea(self):
        self.crear()
        tel, pregunta, opciones = self.s.encuestas[0]
        self.assertEqual(tel, DUENO)  # la recibe el dueño, no el equipo
        self.assertTrue(self.s.encuestas_multiples[0])
        self.assertEqual(opciones[-2:], [logica.CONTINUAR, logica.CANCELAR_SEL])  # siempre se puede volver atrás
        self.assertEqual(len(opciones), 5)  # 3 tareas distintas + Continuar + Cancelar
        self.assertIn("Continuar", pregunta)
        self.assertTrue(any(o.startswith("Probar los botones · 3 personas") for o in opciones))
        self.assertTrue(any(o.startswith("Caja · Ashly") for o in opciones))
        self.assertIn("Te mandé la lista", self.ultima)

    def test_marcar_y_continuar_pide_confirmacion_y_no_cancela_todavia(self):
        self.crear()
        etiqueta = [o for o in self.s.encuestas[0][2] if o.startswith("Probar")][0]
        self.marcar(etiqueta)
        self.assertIn("Voy a cancelar", self.ultima)
        self.assertIn("Probar los botones", self.ultima)
        self.assertNotIn("Caja", self.ultima)
        self.assertEqual({self.fila(x)["estado"] for x in (self.p1, self.p2, self.p3)}, {"pendiente"})
        self.assertEqual(self.avisos_a(MAFER), [])  # nada sale sin el sí

    def test_con_el_si_se_cancelan_y_se_avisa(self):
        self.crear()
        etiqueta = [o for o in self.s.encuestas[0][2] if o.startswith("Probar")][0]
        self.marcar(etiqueta)
        self.dice(self.dueno, "sí")
        self.assertEqual({self.fila(x)["estado"] for x in (self.p1, self.p2, self.p3)}, {"cancelada"})
        self.assertEqual(self.fila(self.q)["estado"], "pendiente")
        self.assertIn("se canceló", self.avisos_a(MAFER)[0])

    def test_varias_marcadas(self):
        self.crear()
        a, b = [o for o in self.s.encuestas[0][2] if o.startswith(("Caja", "Limpiar"))]
        self.marcar(a, b)
        self.dice(self.dueno, "sí")
        self.assertEqual((self.fila(self.c)["estado"], self.fila(self.q)["estado"]), ("cancelada", "cancelada"))
        self.assertEqual(self.fila(self.p1)["estado"], "pendiente")

    def test_sin_continuar_no_pasa_nada_aunque_vote_varias_veces(self):
        self.crear()
        etiqueta = self.s.encuestas[0][2][0]
        n = len(self.s.respuestas)
        self.marcar(etiqueta, continuar=False)
        self.marcar(etiqueta, continuar=False)
        self.assertEqual(len(self.s.respuestas), n)
        self.assertEqual(db.get_estado(self.dueno["id"]).get("control"), None)

    def test_solo_continuar_sin_marcar_nada(self):
        self.crear()
        self.marcar()
        self.assertIn("No marcaste ninguna tarea", self.ultima)

    def test_una_tarea_completada_antes_de_continuar_no_se_cancela(self):
        self.crear()
        etiqueta = [o for o in self.s.encuestas[0][2] if o.startswith("Caja")][0]
        db.completar_tarea(self.c, self.ashly["id"])
        self.marcar(etiqueta)
        self.assertIn("No marcaste ninguna tarea", self.ultima)
        self.assertEqual(self.fila(self.c)["estado"], "completada")

    def test_se_usa_una_sola_vez(self):
        self.crear()
        etiqueta = [o for o in self.s.encuestas[0][2] if o.startswith("Caja")][0]
        self.marcar(etiqueta)
        self.dice(self.dueno, "no")
        n = len(self.s.respuestas)
        self.marcar(etiqueta)  # votar otra vez en la misma encuesta ya no hace nada
        self.assertEqual(len(self.s.respuestas), n)

    def test_con_una_propuesta_pendiente_pide_resolverla_primero(self):
        self.crear()
        etiqueta = self.s.encuestas[0][2][0]
        self.dice(self.dueno, ".tarea mafer: barrer")
        self.marcar(etiqueta)
        self.assertIn("Primero responde *SÍ* o *NO*", self.ultima)
        self.dice(self.dueno, "no")
        self.marcar(etiqueta)  # el selector sigue disponible
        self.assertIn("Voy a cancelar", self.ultima)

    def test_sin_tareas_pendientes(self):
        self.dice(self.dueno, ".cancelar")
        self.assertIn("No hay tareas pendientes que cancelar", self.ultima)
        self.assertEqual(self.s.encuestas, [])

    def test_el_selector_no_cuenta_como_encuesta_del_equipo(self):
        self.crear()
        self.dice(self.dueno, ".resultados")
        self.assertIn("Todavía no he enviado ninguna encuesta", self.ultima)
        self.correr_encuestas(60)
        self.assertEqual(self.avisos_a(DUENO), [])  # no se le "recuerda" votar a sí mismo

    def test_mas_de_10_tareas_distintas_avisa_que_hay_mas(self):
        for i in range(13):
            self.tarea(self.mafer, f"Tarea numero {i:02d} distinta")
        self.dice(self.dueno, ".cancelar")
        self.assertEqual(len(self.s.encuestas[0][2]), 12)  # 10 tareas + Continuar + Cancelar (el máximo de WhatsApp)
        self.assertIn("Hay 3 más", self.ultima)

    def test_etiquetas_repetidas_se_distinguen(self):
        self.tarea(self.mafer, "Caja")
        self.tarea(self.mafer, "caja")  # mismo nombre salvo mayúsculas: se agrupan en una sola opción
        self.tarea(self.ashly, "Caja!")
        self.dice(self.dueno, ".cancelar")
        opciones = self.s.encuestas[0][2]
        self.assertEqual(len(set(opciones)), len(opciones))


class TestCancelarTodoElEquipoConNombre(BaseEnc):
    def test_el_caso_del_chat(self):
        a = self.tarea(self.mafer, "Recordatorio de tareas pendientes")
        b = self.tarea(self.ashly, "Recordatorio de tareas pendientes")
        otra = self.tarea(self.ashly, "Caja")
        self.dice(self.dueno, ".cancelar todo el equipo Recordatorio de tareas pendientes")
        self.assertIn("Voy a cancelar", self.ultima)
        self.dice(self.dueno, "sí")
        self.assertEqual((self.fila(a)["estado"], self.fila(b)["estado"], self.fila(otra)["estado"]),
                         ("cancelada", "cancelada", "pendiente"))

    def test_otras_formas_de_decir_a_todos(self):
        self.tarea(self.mafer, "Probar los botones")
        for frase in ("a todos probar los botones", "para todos probar los botones", "el equipo probar los botones",
                      "todos probar los botones", "todo el equipo probar los botones"):
            with self.subTest(frase=frase):
                self.dice(self.dueno, ".cancelar " + frase)
                self.assertIn("Voy a cancelar", self.ultima)
                self.dice(self.dueno, "no")


class TestCancelarVolverAtras(BaseEnc):
    def test_cancelar_en_el_selector_del_dueno_no_cambia_nada(self):
        a = self.tarea(self.mafer, "Limpiar")
        self.dice(self.dueno, ".cancelar")
        etiqueta = self.s.encuestas[0][2][0]
        logica.procesar_seleccion(self.s, self.dueno, 1, [etiqueta, logica.CANCELAR_SEL])  # marcó una y luego "Cancelar"
        self.assertIn("no cambié nada", self.ultima)
        self.assertEqual(self.fila(a)["estado"], "pendiente")
        n = len(self.s.respuestas)
        logica.procesar_seleccion(self.s, self.dueno, 1, [etiqueta, logica.CONTINUAR])  # ya está cerrado
        self.assertEqual(len(self.s.respuestas), n)


class TestCancelarDeUnaPersona(BaseEnc):
    """".cancelar .mafer": la misma lista, pero solo con las tareas de esa persona."""

    def crear(self):
        self.a = self.tarea(self.mafer, "Limpiar el quirófano")
        self.b = self.tarea(self.mafer, "Bañar")
        self.c = self.tarea(self.ashly, "Caja")

    def test_solo_las_tareas_de_ella_y_sin_repetir_su_nombre(self):
        self.crear()
        self.dice(self.dueno, ".cancelar .mafer")
        _, pregunta, opciones = self.s.encuestas[0]
        self.assertIn("de Mafer", pregunta)
        self.assertEqual(opciones[:2], ["Limpiar el quirófano", "Bañar"])
        self.assertNotIn("Caja", " ".join(opciones))

    def test_varias_personas(self):
        self.crear()
        self.dice(self.dueno, ".cancelar .mafer .ashly")
        opciones = self.s.encuestas[0][2]
        self.assertTrue(any(o.startswith("Caja") for o in opciones))
        self.assertTrue(any(o.startswith("Bañar") for o in opciones))

    def test_con_espacio_despues_del_punto_y_equipo(self):
        self.crear()
        self.dice(self.dueno, ".cancelar . equipo")
        self.assertEqual(len(self.s.encuestas[0][2]), 5)  # 3 tareas + Continuar + Cancelar

    def test_dotequipo_y_dotodos_son_todo_el_equipo(self):
        self.crear()
        for frase in (".cancelar .equipo", ".cancelar .todos"):
            self.s.encuestas.clear()
            self.dice(self.dueno, frase)
            self.assertEqual(len(self.s.encuestas[0][2]), 5)

    def test_persona_que_no_existe(self):
        self.crear()
        self.dice(self.dueno, ".cancelar .juancho")
        self.assertIn("No encuentro a esa persona", self.ultima)
        self.assertEqual(self.s.encuestas, [])

    def test_persona_sin_tareas(self):
        self.crear()
        self.dice(self.dueno, ".cancelar .olenka")
        self.assertIn("No hay tareas pendientes que cancelar de Olenka", self.ultima)

    def test_marcar_y_continuar_solo_cancela_lo_de_ella(self):
        self.crear()
        self.dice(self.dueno, ".cancelar .mafer")
        logica.procesar_seleccion(self.s, self.dueno, 1, ["Bañar", logica.CONTINUAR])
        self.dice(self.dueno, "sí")
        self.assertEqual((self.fila(self.a)["estado"], self.fila(self.b)["estado"], self.fila(self.c)["estado"]),
                         ("pendiente", "cancelada", "pendiente"))

    def test_el_nombre_sin_punto_sigue_cancelando_directo_con_confirmacion(self):
        self.crear()
        self.dice(self.dueno, ".cancelar caja")  # una sola tarea con esa palabra: confirmación directa
        self.assertIn("Voy a cancelar", self.ultima)
        self.assertEqual(self.s.encuestas, [])
        self.dice(self.dueno, "no")
        self.dice(self.dueno, ".cancelar mafer")  # sin punto: la forma de siempre (con dos tareas, pregunta cuál)
        self.assertIn("Mafer tiene varias tareas", self.ultima)
        self.assertEqual(self.s.encuestas, [])

    def test_los_nombres_con_punto_funcionan_en_los_demas_comandos(self):
        self.crear()
        self.dice(self.dueno, ".estado .mafer")
        self.assertIn("Mafer: Bañar", self.ultima)
        self.assertNotIn("Ashly", self.ultima)
        self.dice(self.dueno, ".recordar .mafer .ashly")
        self.assertIn("Mafer (2)", self.ultima)
        self.assertIn("Ashly (1)", self.ultima)
        self.dice(self.dueno, "no")
        self.dice(self.dueno, ".tarea .mafer .ashly: barrer")
        self.assertIn("1. Mafer: Barrer", self.ultima)
        self.assertIn("2. Ashly: Barrer", self.ultima)


class TestSelectoresDelTrabajador(BaseEnc):
    """Con varias tareas, en vez de pedir números (que confunden) el trabajador recibe una encuesta clara."""

    def setUp(self):
        super().setUp()
        self.neg["botones"] = True
        self.a = self.tarea(self.mafer, "Limpiar el quirófano", hora(30))
        self.b = self.tarea(self.mafer, "Bañar a Firulais")
        self.c = self.tarea(self.mafer, "Revisar vacunas")

    def encuesta_de(self, tipo):
        with db.conn() as c:
            return dict(c.execute("SELECT id, opciones FROM encuestas WHERE tipo=? ORDER BY id DESC", (tipo,)).fetchone())

    def test_listo_con_varias_tareas_manda_una_encuesta_de_marcar_varias(self):
        self.dice(self.mafer, "listo")
        tel, pregunta, opciones = self.s.encuestas[0]
        self.assertEqual(tel, MAFER)
        self.assertTrue(self.s.encuestas_multiples[0])
        self.assertIn("Marca las que ya hiciste", pregunta)
        self.assertIn("Continuar", pregunta)
        self.assertTrue(opciones[0].startswith("1. Limpiar el quirófano · hoy 08:30"))
        self.assertEqual(opciones[-2:], [logica.CONTINUAR, logica.CANCELAR_SEL])
        self.assertEqual(self.s.respuestas, [])  # un solo mensaje: la encuesta, no además una lista con números

    def test_marcar_dos_y_continuar_completa_solo_esas(self):
        self.dice(self.mafer, "listo")
        _, _, op = self.s.encuestas[0]
        logica.procesar_seleccion(self.s, self.mafer, 1, [op[0], op[2], logica.CONTINUAR])
        self.assertEqual((self.fila(self.a)["estado"], self.fila(self.b)["estado"], self.fila(self.c)["estado"]),
                         ("completada", "pendiente", "completada"))
        self.assertIn("Marqué como completadas", self.ultima)
        self.assertEqual(len(self.avisos_a(DUENO)), 2)  # el dueño se entera de cada una

    def test_mientras_marca_no_pasa_nada(self):
        self.dice(self.mafer, "listo")
        _, _, op = self.s.encuestas[0]
        logica.procesar_seleccion(self.s, self.mafer, 1, [op[0]])
        logica.procesar_seleccion(self.s, self.mafer, 1, [op[0], op[1]])
        self.assertEqual({self.fila(x)["estado"] for x in (self.a, self.b, self.c)}, {"pendiente"})

    def test_volver_atras(self):
        self.dice(self.mafer, "listo")
        _, _, op = self.s.encuestas[0]
        logica.procesar_seleccion(self.s, self.mafer, 1, [op[0], logica.CANCELAR_SEL])
        self.assertIn("no cambié nada", self.ultima)
        self.assertEqual({self.fila(x)["estado"] for x in (self.a, self.b, self.c)}, {"pendiente"})
        self.dice(self.mafer, "2")  # ya no espera un número: no marca nada por error
        self.assertEqual(self.fila(self.b)["estado"], "pendiente")

    def test_continuar_sin_marcar(self):
        self.dice(self.mafer, "listo")
        logica.procesar_seleccion(self.s, self.mafer, 1, [logica.CONTINUAR])
        self.assertIn("No marcaste ninguna tarea", self.ultima)

    def test_problema_con_varias_es_de_una_sola_opcion_y_sigue_con_el_detalle(self):
        self.dice(self.mafer, "problema")
        self.assertFalse(self.s.encuestas_multiples[0])
        _, pregunta, op = self.s.encuestas[0]
        self.assertIn("¿En cuál tarea hay un problema?", pregunta)
        self.assertEqual(op[-1], logica.CANCELAR_SEL)
        self.assertNotIn(logica.CONTINUAR, op)  # al tocar una ya vale
        logica.procesar_seleccion(self.s, self.mafer, 1, [op[1]])
        self.assertIn("¿Qué pasó con *Bañar a Firulais*?", self.ultima)
        self.dice(self.mafer, "no hay agua")
        self.assertEqual(self.fila(self.b)["estado"], "con_incidencia")
        self.assertIn("no hay agua", self.avisos_a(DUENO)[0])

    def test_mas_tiempo_con_varias_ofrece_15_30_y_1_hora(self):
        self.dice(self.mafer, "más tiempo")
        _, pregunta, op = self.s.encuestas[0]
        self.assertIn("más tiempo", pregunta)
        logica.procesar_seleccion(self.s, self.mafer, 1, [op[0]])
        self.assertEqual([e for e, _ in self.s.botones_respuestas[-1]], ["15 min", "30 min", "1 hora"])

    def test_la_encuesta_de_un_trabajador_no_es_una_encuesta_del_equipo(self):
        self.dice(self.mafer, "listo")
        self.dice(self.dueno, ".resultados")
        self.assertIn("Todavía no he enviado ninguna encuesta", self.ultima)

    def test_todavia_se_puede_responder_escribiendo_numeros(self):
        self.dice(self.mafer, "listo")
        self.dice(self.mafer, "1 y 3")
        self.assertEqual((self.fila(self.a)["estado"], self.fila(self.c)["estado"]), ("completada", "completada"))

    def test_con_los_botones_apagados_sigue_la_lista_de_texto(self):
        self.neg["botones"] = False
        self.dice(self.mafer, "listo")
        self.assertEqual(self.s.encuestas, [])
        self.assertIn("¿Cuál terminaste?", self.ultima)

    def test_si_la_encuesta_falla_cae_a_la_lista_de_texto(self):
        def falla(*a, **k):
            raise RuntimeError("no soportado")
        self.s.enviar_encuesta = falla
        self.dice(self.mafer, "listo")
        self.assertIn("¿Cuál terminaste?", self.ultima)
        self.dice(self.mafer, "2")
        self.assertEqual(self.fila(self.b)["estado"], "completada")

    def test_con_demasiadas_tareas_se_usa_la_lista_de_texto(self):
        for i in range(10):
            self.tarea(self.mafer, f"Extra {i}")
        self.dice(self.mafer, "listo")  # 13 tareas: no caben en una encuesta
        self.assertEqual(self.s.encuestas, [])
        self.assertIn("¿Cuál terminaste?", self.ultima)

    def test_con_una_sola_tarea_no_hay_encuesta(self):
        for x in (self.b, self.c):
            db.cancelar_tarea(x)
        self.dice(self.mafer, "listo")
        self.assertEqual(self.s.encuestas, [])
        self.assertEqual(self.fila(self.a)["estado"], "completada")

    def test_un_selector_solo_sirve_a_quien_se_lo_mandaron(self):
        self.dice(self.mafer, "listo")
        _, _, op = self.s.encuestas[0]
        # aunque otra persona intentara votarlo, las tareas se completan solo si son DE QUIEN VOTA
        logica.procesar_seleccion(self.s, self.ashly, 1, [op[0], logica.CONTINUAR])
        self.assertEqual(self.fila(self.a)["estado"], "pendiente")


def hora(minutos):
    from test_seguimiento import hora as h
    return h(minutos)


class TestAjustarDestinatarios(BaseEnc):
    """El caso del chat: la encuesta espera el SÍ y el dueño dice "a marco no le envíes"."""

    def propuesta(self):
        self.dice(self.dueno, ".encuesta ¿Cómo salió la jornada? opciones: todo bien, tengo un problema, todo mal")

    def test_a_olenka_no_le_envies(self):
        self.propuesta()
        self.dice(self.dueno, "a olenka no le envíes")
        self.assertIn("Ashly, Mafer", self.ultima)
        self.assertNotIn("Olenka", self.ultima)
        self.assertIn("¿La envío?", self.ultima)  # vuelve a pedir el sí con la lista corregida
        self.assertEqual(self.llm.llamadas, [])
        self.dice(self.dueno, "sí")
        self.assertEqual({t for t, _, _ in self.s.encuestas}, {ASHLY, MAFER})
        self.assertEqual(len(db.destinatarios_encuesta(db.ultima_encuesta()["id"])), 2)

    def test_otras_formas_de_quitar(self):
        for frase in ("sin olenka", "excepto olenka", "menos olenka", "quita a olenka", "no se la mandes a olenka",
                      "olenka no", "excluye a olenka"):
            with self.subTest(frase=frase):
                db.set_estado(self.dueno["id"], {})
                self.propuesta()
                self.dice(self.dueno, frase)
                if frase == "olenka no":  # sin una palabra de quitar no se adivina
                    self.assertIn("esperando tu *SÍ* o *NO*", self.ultima)
                else:
                    self.assertNotIn("Olenka", self.ultima.split("📊")[0])

    def test_solo_a_algunas_personas(self):
        self.propuesta()
        self.dice(self.dueno, "solo a mafer")
        self.assertIn("encuesta a Mafer:", self.ultima)
        self.dice(self.dueno, "sí")
        self.assertEqual({t for t, _, _ in self.s.encuestas}, {MAFER})

    def test_quitar_a_todos_no_deja_a_nadie(self):
        self.propuesta()
        self.dice(self.dueno, "sin ashly")
        self.dice(self.dueno, "sin mafer")
        self.dice(self.dueno, "sin olenka")
        self.assertIn("Así no quedaría nadie", self.ultima)
        self.dice(self.dueno, "sí")  # la última lista válida (solo Olenka) sigue vigente
        self.assertEqual({t for t, _, _ in self.s.encuestas}, {"51900000009"})

    def test_quitar_a_alguien_que_ya_no_estaba_no_cambia_nada_y_sigue_esperando(self):
        self.propuesta()
        self.dice(self.dueno, "sin olenka")
        self.dice(self.dueno, "sin olenka")
        self.assertIn("Ashly, Mafer", self.ultima)

    def test_texto_sin_sentido_recuerda_que_hay_una_orden_pendiente(self):
        self.propuesta()
        self.dice(self.dueno, "mafer limpia el quirófano")
        self.assertIn("Tengo la encuesta esperando tu *SÍ* o *NO*", self.ultima)
        self.assertIn("sin Marco", self.ultima)
        self.assertEqual(self.s.encuestas, [])
        self.assertIsNone(db.ultima_encuesta())

    def test_el_no_sigue_descartando(self):
        self.propuesta()
        self.dice(self.dueno, "no")
        self.assertIn("lo dejé como estaba", self.ultima)

    def test_tambien_funciona_con_el_aviso_al_equipo(self):
        self.dice(self.dueno, ".aviso mañana abrimos tarde")
        self.dice(self.dueno, "sin olenka")
        self.assertIn("Ashly, Mafer", self.ultima)
        self.dice(self.dueno, "sí")
        self.assertTrue(self.avisos_a(MAFER))
        self.assertEqual(self.avisos_a("51900000009"), [])

    def test_los_comandos_siguen_funcionando_con_una_orden_pendiente(self):
        self.propuesta()
        self.dice(self.dueno, ".estado")
        self.assertNotIn("esperando tu", self.ultima)
        self.dice(self.dueno, ".ayuda")
        self.assertIn("Comandos", self.ultima)


class TestComandoEquipo(BaseEnc):
    def test_lista_el_equipo_con_sus_roles(self):
        self.dice(self.dueno, ".equipo")
        r = self.ultima
        self.assertIn("Andrés — dueño", r)
        self.assertIn("Ashly — trabajador · atiende a los clientes", r)
        self.assertIn("Mafer — trabajador", r)
        self.assertEqual(self.llm.llamadas, [])

    def test_el_dueno_que_tambien_trabaja(self):
        with db.conn() as c:
            c.execute("UPDATE personas SET extras='trabajador' WHERE rol='maestro'")
        self.dice(self.dueno, ".equipo")
        self.assertIn("Andrés — dueño · también recibe tareas", self.ultima)

    def test_no_muestra_a_los_clientes_ni_a_quien_ya_no_esta(self):
        with db.conn() as c:
            c.execute("INSERT INTO personas(telefono, nombre, rol) VALUES('51999000222','Lucía','cliente')")
            c.execute("UPDATE personas SET rol='cliente' WHERE nombre='Olenka'")
        self.dice(self.dueno, ".equipo")
        self.assertNotIn("Lucía", self.ultima)
        self.assertNotIn("Olenka", self.ultima)

    def test_funciona_con_una_propuesta_pendiente(self):
        self.dice(self.dueno, ".tarea mafer: limpiar")
        self.dice(self.dueno, ".equipo")
        self.assertIn("Tu equipo", self.ultima)
