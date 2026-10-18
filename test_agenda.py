"""Pruebas de agenda.py: horarios libres, fechas y horas escritas por clientes. Lógica pura, sin WhatsApp ni base."""
import unittest
from datetime import date, datetime

import agenda

LUNES = date(2030, 1, 7)  # 7 de enero de 2030 es lunes
BASE = {"dias": ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado"], "desde": "09:00", "hasta": "18:00",
        "paso_min": 30, "simultaneas": 1, "anticipacion_horas": 2, "dias_adelante": 14}


def cfg(**cambios):
    return agenda.configurar({**BASE, **cambios})


def ahora(h=8, m=0, dia=LUNES):
    return datetime(dia.year, dia.month, dia.day, h, m)


class TestConfigurar(unittest.TestCase):
    def test_configuracion_valida(self):
        c = cfg()
        self.assertEqual((c["dias"], c["desde"], c["hasta"], c["paso"]), ([0, 1, 2, 3, 4, 5], 540, 1080, 30))
        self.assertEqual((c["simultaneas"], c["anticipacion_h"], c["dias_adelante"]), (1, 2.0, 14))

    def test_acepta_dias_con_o_sin_tildes_y_mayusculas(self):
        self.assertEqual(cfg(dias=["MIERCOLES", "sábado", "Lunes"])["dias"], [0, 2, 5])

    def test_configuraciones_invalidas_devuelven_none(self):
        for malo in (None, "texto", {}, {"dias": ["lunes"]}, {"desde": "09:00", "hasta": "18:00"},
                     {**BASE, "dias": []}, {**BASE, "dias": ["funday"]}, {**BASE, "desde": "18:00", "hasta": "09:00"},
                     {**BASE, "desde": "9 de la mañana"}, {**BASE, "desde": "25:99"}):
            with self.subTest(malo=malo):
                self.assertIsNone(agenda.configurar(malo))

    def test_valores_extremos_se_acotan(self):
        c = cfg(paso_min=1, simultaneas=0, dias_adelante=999, anticipacion_horas=-5)
        self.assertEqual((c["paso"], c["simultaneas"], c["dias_adelante"], c["anticipacion_h"]), (5, 1, 60, 0.0))

    def test_dias_cerrados(self):
        self.assertEqual(cfg(cerrado=["2030-12-25"])["cerrado"], {"2030-12-25"})


class TestHuecos(unittest.TestCase):
    def test_dia_normal_con_anticipacion(self):
        libres = agenda.huecos(cfg(), LUNES, 30, [], ahora(8, 0))  # 8:00 + 2 h => desde las 10:00
        self.assertEqual(libres[0], "10:00")
        self.assertEqual(libres[-1], "17:30")

    def test_sin_anticipacion_desde_la_apertura(self):
        self.assertEqual(agenda.huecos(cfg(), LUNES, 30, [], ahora(5, 0))[0], "09:00")

    def test_la_duracion_no_puede_pasarse_de_la_hora_de_cierre(self):
        libres = agenda.huecos(cfg(), LUNES, 60, [], ahora(5, 0))
        self.assertEqual(libres[-1], "17:00")  # 17:00 + 60 min = 18:00
        self.assertNotIn("17:30", libres)

    def test_dia_no_laborable_o_cerrado_no_tiene_horarios(self):
        self.assertEqual(agenda.huecos(cfg(), date(2030, 1, 13), 30, [], ahora()), [])  # domingo
        self.assertEqual(agenda.huecos(cfg(cerrado=["2030-01-08"]), date(2030, 1, 8), 30, [], ahora()), [])

    def test_una_cita_ocupa_su_horario_y_el_siguiente_si_dura_mas(self):
        ocupadas = [(600, 660)]  # 10:00 a 11:00
        libres = agenda.huecos(cfg(), LUNES, 30, ocupadas, ahora(5, 0))
        self.assertNotIn("10:00", libres)
        self.assertNotIn("10:30", libres)
        self.assertIn("09:30", libres)  # termina a las 10:00 justo cuando empieza la otra: permitido
        self.assertIn("11:00", libres)

    def test_un_servicio_largo_no_puede_empezar_encima_de_otra_cita(self):
        libres = agenda.huecos(cfg(), LUNES, 60, [(600, 630)], ahora(5, 0))
        self.assertNotIn("09:30", libres)  # 9:30 a 10:30 chocaría
        self.assertNotIn("10:00", libres)
        self.assertIn("09:00", libres)
        self.assertIn("10:30", libres)

    def test_con_dos_simultaneas_una_cita_no_bloquea(self):
        libres = agenda.huecos(cfg(simultaneas=2), LUNES, 30, [(600, 630)], ahora(5, 0))
        self.assertIn("10:00", libres)
        libres = agenda.huecos(cfg(simultaneas=2), LUNES, 30, [(600, 630), (600, 630)], ahora(5, 0))
        self.assertNotIn("10:00", libres)

    def test_hoy_ya_pasada_la_hora_no_ofrece_horarios_viejos(self):
        self.assertEqual(agenda.huecos(cfg(), LUNES, 30, [], ahora(15, 0)), ["17:00", "17:30"])  # 15:00 + 2 h
        self.assertEqual(agenda.huecos(cfg(), LUNES, 30, [], ahora(16, 0)), [])  # ya no queda tiempo hoy
        self.assertEqual(agenda.huecos(cfg(), LUNES, 30, [], ahora(20, 0)), [])


class TestDiasConHuecos(unittest.TestCase):
    def test_salta_domingos_y_dias_llenos(self):
        lleno = {date(2030, 1, 8): [(540, 1080)]}  # el martes está completamente ocupado
        dias = agenda.dias_con_huecos(cfg(), 30, lambda d: lleno.get(d, []), ahora(8, 0), maximo=4)
        self.assertEqual(dias, [date(2030, 1, 7), date(2030, 1, 9), date(2030, 1, 10), date(2030, 1, 11)])

    def test_respeta_el_maximo_y_los_dias_adelante(self):
        self.assertEqual(len(agenda.dias_con_huecos(cfg(), 30, lambda d: [], ahora(), maximo=3)), 3)
        self.assertEqual(agenda.dias_con_huecos(cfg(dias_adelante=1), 30, lambda d: [(540, 1080)], ahora()), [])

    def test_sin_dias_libres_devuelve_lista_vacia(self):
        self.assertEqual(agenda.dias_con_huecos(cfg(), 30, lambda d: [(540, 1080)], ahora()), [])


class TestEtiquetas(unittest.TestCase):
    def test_hoy_manana_y_otros(self):
        self.assertEqual(agenda.etiqueta_dia(LUNES, LUNES), "Hoy (lun 07/01)")
        self.assertEqual(agenda.etiqueta_dia(date(2030, 1, 8), LUNES), "Mañana (mar 08/01)")
        self.assertEqual(agenda.etiqueta_dia(date(2030, 1, 10), LUNES), "jue 10/01")


class TestParseDia(unittest.TestCase):
    def test_palabras(self):
        self.assertEqual(agenda.parse_dia("hoy", LUNES), LUNES)
        self.assertEqual(agenda.parse_dia("Mañana", LUNES), date(2030, 1, 8))
        self.assertEqual(agenda.parse_dia("pasado mañana", LUNES), date(2030, 1, 9))

    def test_dias_de_la_semana_son_el_proximo(self):
        self.assertEqual(agenda.parse_dia("viernes", LUNES), date(2030, 1, 11))
        self.assertEqual(agenda.parse_dia("lunes", LUNES), LUNES)  # hoy mismo
        self.assertEqual(agenda.parse_dia("Miércoles", LUNES), date(2030, 1, 9))
        self.assertEqual(agenda.parse_dia("domingo", LUNES), date(2030, 1, 13))

    def test_fechas_numericas(self):
        self.assertEqual(agenda.parse_dia("14/01", LUNES), date(2030, 1, 14))
        self.assertEqual(agenda.parse_dia("14-1", LUNES), date(2030, 1, 14))
        self.assertEqual(agenda.parse_dia("14/01/2030", LUNES), date(2030, 1, 14))
        self.assertEqual(agenda.parse_dia("14/01/30", LUNES), date(2030, 1, 14))

    def test_fechas_invalidas_o_pasadas(self):
        for malo in ("31/02", "5/13", "01/01", "ayer", "asdf", "", "99"):
            with self.subTest(malo=malo):
                self.assertIsNone(agenda.parse_dia(malo, LUNES))


class TestParseHora(unittest.TestCase):
    def test_formatos_comunes(self):
        casos = {"15:30": "15:30", "3pm": "15:00", "3 pm": "15:00", "3 p.m.": "15:00", "9am": "09:00",
                 "9:30": "09:30", "12pm": "12:00", "12am": "00:00", "11:15 am": "11:15", "10": "10:00",
                 "3": "15:00", "7": "19:00", "14": "14:00", "08:00": "08:00"}
        for texto, esperado in casos.items():
            with self.subTest(texto=texto):
                self.assertEqual(agenda.parse_hora(texto), esperado)

    def test_horas_invalidas(self):
        for malo in ("25:00", "10:75", "abc", "", "tarde", "3:5x", "mañana a las 3"):
            with self.subTest(malo=malo):
                self.assertIsNone(agenda.parse_hora(malo))


if __name__ == "__main__":
    unittest.main()
