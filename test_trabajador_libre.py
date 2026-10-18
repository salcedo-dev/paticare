"""Lo que un trabajador escribe libremente: Pati responde lo que sabe y el resto se lo pasa al dueño."""
from datetime import timedelta

import db
import logica
from test_seguimiento import AHORA, DUENO, MAFER, Base, hora


class TestMensajesLibresDelTrabajador(Base):
    def test_un_pedido_se_le_pasa_al_dueno_y_se_le_avisa_al_trabajador(self):
        self.dice(self.mafer, "me pueden re asignar las tareas?")  # la frase exacta del chat de Olenka
        aviso = self.avisos_a(DUENO)[0]
        self.assertIn("💬 *Mafer* te escribió", aviso)
        self.assertIn("me pueden re asignar las tareas?", aviso)
        self.assertEqual(self.ultima, "Se lo pasé a Andrés 👍")
        self.assertEqual(self.llm.llamadas, [])

    def test_un_pedido_sobre_tareas_no_se_confunde_con_ver_sus_pendientes(self):
        self.tarea(self.mafer, "Limpiar")
        self.dice(self.mafer, "me pueden reasignar mis tareas?")
        self.assertNotIn("Tus pendientes", self.ultima)
        self.assertTrue(self.avisos_a(DUENO))

    def test_cuanto_tiempo_tengo_responde_con_sus_plazos_reales(self):
        self.tarea(self.mafer, "Limpiar el quirófano", hora(90))
        self.tarea(self.mafer, "Bañar a Firulais")
        for frase in ("cuánto tiempo tengo para terminarlo?", "¿cuándo vence?", "hasta cuando tengo", "para cuándo es"):
            with self.subTest(frase=frase):
                self.dice(self.mafer, frase)
                r = self.ultima
                self.assertIn("1. Limpiar el quirófano · vence hoy 09:30", r)
                self.assertIn("2. Bañar a Firulais · sin plazo", r)
        self.assertEqual(self.avisos_a(DUENO), [])  # una pregunta que Pati sabe responder no molesta al dueño

    def test_plazos_sin_tareas(self):
        self.dice(self.mafer, "cuánto tiempo tengo?")
        self.assertIn("No tienes tareas pendientes", self.ultima)

    def test_texto_que_pati_no_entiende_tambien_se_pasa_al_dueno(self):
        self.dice(self.mafer, "el aire acondicionado hace un ruido raro")
        self.assertIn("el aire acondicionado hace un ruido raro", self.avisos_a(DUENO)[0])
        self.assertIn("Se lo pasé a Andrés", self.ultima)

    def test_los_ok_gracias_y_emojis_no_molestan_al_dueno(self):
        n = len(self.s.respuestas)
        for frase in ("ok", "gracias", "👍", "dale", "ya", "jajaja", "🙏🙏", "no", "si"):
            self.dice(self.mafer, frase)
        self.assertEqual(self.avisos_a(DUENO), [])
        self.assertEqual(len(self.s.respuestas), n)  # y Pati tampoco contesta a un "ok"

    def test_tope_por_hora(self):
        for i in range(logica.MAX_REENVIOS_HORA + 3):
            self.dice(self.mafer, f"necesito algo numero {i}")
        self.assertEqual(len(self.avisos_a(DUENO)), logica.MAX_REENVIOS_HORA)
        self.ahora = AHORA + timedelta(hours=1, minutes=1)
        self.dice(self.mafer, "necesito otra cosa distinta")
        self.assertEqual(len(self.avisos_a(DUENO)), logica.MAX_REENVIOS_HORA + 1)

    def test_el_mensaje_largo_se_recorta(self):
        self.dice(self.mafer, "necesito " + "x" * 900)
        self.assertLess(len(self.avisos_a(DUENO)[0]), 600)

    def test_sin_dueno_registrado_lo_dice(self):
        with db.conn() as c:
            c.execute("UPDATE personas SET rol='cliente' WHERE rol='maestro'")
        self.dice(self.mafer, "necesito ayuda con la caja")
        self.assertIn("no puedo pasarle tu mensaje", self.ultima)

    def test_lo_que_pati_si_entiende_no_se_reenvia(self):
        self.tarea(self.mafer, "Limpiar")
        for frase in ("hola", "qué tengo pendiente", "ayuda", "listo"):
            self.dice(self.mafer, frase)
        self.assertEqual([a for a in self.avisos_a(DUENO) if "te escribió" in a], [])

    def test_los_clientes_no_pasan_por_aqui(self):
        with db.conn() as c:
            c.execute("INSERT INTO personas(telefono, nombre, rol) VALUES('51999000333','Lucía','cliente')")
        lucia = db.persona_por_id("51999000333")
        self.dice(lucia, "necesito una cita")
        self.assertEqual([a for a in self.avisos_a(DUENO) if "te escribió" in a], [])

    def test_la_ayuda_del_trabajador_avisa_que_lo_demas_se_lo_pasa_al_dueno(self):
        self.dice(self.mafer, "ayuda")
        self.assertIn("se lo paso al dueño", self.ultima)
        self.assertLessEqual(len(self.ultima.splitlines()), 4)
