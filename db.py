"""PatiCare - base de datos (SQLite).

Para cargar a las personas: copia personas.example.json a personas.json,
edítalo con los datos reales y ejecuta  python db.py
"""
import json
import os
import sqlite3

AQUI = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(AQUI, "pati.db")  # ruta absoluta: no depende de la carpeta desde donde se ejecute
PERSONAS_JSON = os.path.join(AQUI, "personas.json")  # datos reales; NO se sube a git


def conn():
    c = sqlite3.connect(DB, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def init():
    with conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS personas(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telefono TEXT UNIQUE NOT NULL,
                lid TEXT,
                nombre TEXT NOT NULL,
                rol TEXT NOT NULL CHECK(rol IN ('maestro','trabajador','cliente'))
            );
            CREATE TABLE IF NOT EXISTS tareas(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                responsable_id INTEGER NOT NULL REFERENCES personas(id),
                actividad TEXT NOT NULL,
                detalles TEXT,
                horario TEXT,
                estado TEXT NOT NULL DEFAULT 'pendiente',
                creada_en TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                completada_en TEXT
            );
            CREATE TABLE IF NOT EXISTS eventos(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tarea_id INTEGER REFERENCES tareas(id),
                tipo TEXT NOT NULL,
                detalle TEXT,
                fecha TEXT NOT NULL DEFAULT (datetime('now','localtime'))
            );
            CREATE TABLE IF NOT EXISTS recordatorios(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cliente TEXT NOT NULL,
                telefono TEXT,
                mensaje TEXT NOT NULL,
                fecha_hora TEXT NOT NULL,
                enviado INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS citas(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cliente_nombre TEXT NOT NULL,
                telefono TEXT NOT NULL,
                mascota TEXT NOT NULL,
                servicio TEXT NOT NULL,
                fecha_hora TEXT NOT NULL,
                recordatorio_enviado INTEGER NOT NULL DEFAULT 0,
                creada_en TEXT NOT NULL DEFAULT (datetime('now','localtime'))
            );
            CREATE TABLE IF NOT EXISTS resumen_diario(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha TEXT UNIQUE NOT NULL  -- YYYY-MM-DD; evita doble envío
            );
            -- Estado de la conversación de cada persona (propuesta pendiente, paso del trabajador...)
            CREATE TABLE IF NOT EXISTS conversaciones(
                persona_id INTEGER PRIMARY KEY REFERENCES personas(id),
                estado_json TEXT NOT NULL DEFAULT '{}',
                actualizado_en TEXT NOT NULL DEFAULT (datetime('now','localtime'))
            );
            -- Historial reciente para darle contexto a la IA
            CREATE TABLE IF NOT EXISTS mensajes(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                persona_id INTEGER NOT NULL REFERENCES personas(id),
                rol TEXT NOT NULL CHECK(rol IN ('usuario','pati')),
                texto TEXT NOT NULL,
                creado_en TEXT NOT NULL DEFAULT (datetime('now','localtime'))
            );
            -- IDs de mensajes de WhatsApp ya atendidos (evita duplicar por reentrega)
            CREATE TABLE IF NOT EXISTS mensajes_procesados(
                id TEXT PRIMARY KEY,
                creado_en TEXT NOT NULL DEFAULT (datetime('now','localtime'))
            );
            """
        )
        c.execute("DELETE FROM mensajes_procesados WHERE creado_en < datetime('now','localtime','-7 days')")
        c.execute("DELETE FROM mensajes WHERE creado_en < datetime('now','localtime','-30 days')")


# ── Personas ──────────────────────────────────────────────────────────────────

def cargar_personas():
    """Carga/actualiza las personas desde personas.json (si existe)."""
    if not os.path.exists(PERSONAS_JSON):
        print("[AVISO] No hay personas.json; copia personas.example.json y complétalo.")
        return
    with open(PERSONAS_JSON, encoding="utf-8") as f:
        personas = json.load(f)
    with conn() as c:
        for p in personas:
            c.execute(
                """INSERT INTO personas(telefono, lid, nombre, rol) VALUES(?,?,?,?)
                   ON CONFLICT(telefono) DO UPDATE SET
                   lid=excluded.lid, nombre=excluded.nombre, rol=excluded.rol""",
                (p["telefono"], p.get("lid"), p["nombre"], p["rol"]),
            )


def persona_por_id(ident):
    with conn() as c:
        return c.execute(
            "SELECT * FROM personas WHERE telefono=? OR lid=?", (ident, ident)
        ).fetchone()


def trabajadores():
    with conn() as c:
        return c.execute("SELECT * FROM personas WHERE rol='trabajador'").fetchall()


def duenos():
    with conn() as c:
        return c.execute("SELECT * FROM personas WHERE rol='maestro'").fetchall()


def persona_por_nombre(nombre):
    """Busca un cliente por nombre (insensible a mayúsculas)."""
    with conn() as c:
        return c.execute(
            "SELECT * FROM personas WHERE lower(nombre)=lower(?) AND rol='cliente'",
            (nombre,),
        ).fetchone()


# ── Estado de conversación, historial y deduplicación ────────────────────────

def get_estado(persona_id):
    with conn() as c:
        row = c.execute(
            "SELECT estado_json FROM conversaciones WHERE persona_id=?", (persona_id,)
        ).fetchone()
    return json.loads(row["estado_json"]) if row else {}


def set_estado(persona_id, estado):
    with conn() as c:
        c.execute(
            """INSERT INTO conversaciones(persona_id, estado_json) VALUES(?,?)
               ON CONFLICT(persona_id) DO UPDATE SET
               estado_json=excluded.estado_json, actualizado_en=datetime('now','localtime')""",
            (persona_id, json.dumps(estado, ensure_ascii=False)),
        )


def log_mensaje(persona_id, rol, texto):
    with conn() as c:
        c.execute(
            "INSERT INTO mensajes(persona_id, rol, texto) VALUES(?,?,?)",
            (persona_id, rol, texto),
        )


def historial(persona_id, n=6, horas=12):
    """Últimos n mensajes (de la persona y de Pati) en orden cronológico."""
    with conn() as c:
        filas = c.execute(
            """SELECT rol, texto FROM mensajes
               WHERE persona_id=? AND creado_en >= datetime('now','localtime',?)
               ORDER BY id DESC LIMIT ?""",
            (persona_id, f"-{int(horas)} hours", n),
        ).fetchall()
    return [dict(f) for f in reversed(filas)]


def registrar_mensaje_id(msg_id):
    """True si el mensaje es nuevo; False si ya se había atendido."""
    if not msg_id:
        return True
    with conn() as c:
        cur = c.execute("INSERT OR IGNORE INTO mensajes_procesados(id) VALUES(?)", (msg_id,))
        return cur.rowcount == 1


# ── Confirmación de propuestas (atómica) ─────────────────────────────────────

def confirmar_propuesta(dueno_id):
    """Guarda la propuesta pendiente del dueño y la borra en UNA transacción.

    Devuelve None si no hay propuesta (por ejemplo un segundo "sí"), o
    {"tipo": ..., "items": [...]} con lo creado. Si algo falla, no cambia nada.
    """
    c = conn()
    try:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute(
            "SELECT estado_json FROM conversaciones WHERE persona_id=?", (dueno_id,)
        ).fetchone()
        estado = json.loads(row["estado_json"]) if row else {}
        prop = estado.get("propuesta")
        if not prop:
            c.rollback()
            return None
        tipo, datos = prop["tipo"], prop["datos"]
        items = []
        if tipo == "tareas":
            for t in datos["tareas"]:
                w = c.execute(
                    "SELECT id, nombre, telefono FROM personas WHERE lower(nombre)=lower(?) AND rol='trabajador'",
                    (t["responsable"],),
                ).fetchone()
                if w is None:
                    raise ValueError(f"responsable no registrado: {t['responsable']}")
                cur = c.execute(
                    "INSERT INTO tareas(responsable_id, actividad, detalles, horario) VALUES(?,?,?,?)",
                    (w["id"], t["actividad"], t.get("detalles"), t.get("horario")),
                )
                c.execute(
                    "INSERT INTO eventos(tarea_id, tipo, detalle) VALUES(?,?,?)",
                    (cur.lastrowid, "creada", t["actividad"]),
                )
                items.append({"tarea_id": cur.lastrowid, "trabajador": dict(w), "tarea": t})
        elif tipo == "recordatorio":
            cur = c.execute(
                "INSERT INTO recordatorios(cliente, telefono, mensaje, fecha_hora) VALUES(?,?,?,?)",
                (datos["cliente"], datos["telefono"], datos["mensaje_a_enviar"], datos["fecha_hora"]),
            )
            items.append({"recordatorio_id": cur.lastrowid, "datos": datos})
        elif tipo == "cita":
            cur = c.execute(
                """INSERT INTO citas(cliente_nombre, telefono, mascota, servicio, fecha_hora)
                   VALUES(?,?,?,?,?)""",
                (datos["cliente"], datos["telefono"], datos["mascota"], datos["servicio"], datos["fecha_hora"]),
            )
            items.append({"cita_id": cur.lastrowid, "datos": datos})
        else:
            raise ValueError(f"tipo de propuesta desconocido: {tipo}")
        estado.pop("propuesta", None)
        c.execute(
            """INSERT INTO conversaciones(persona_id, estado_json) VALUES(?,?)
               ON CONFLICT(persona_id) DO UPDATE SET estado_json=excluded.estado_json""",
            (dueno_id, json.dumps(estado, ensure_ascii=False)),
        )
        c.commit()
        return {"tipo": tipo, "items": items}
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def registrar_envio_fallido(tarea_id, detalle):
    with conn() as c:
        c.execute(
            "INSERT INTO eventos(tarea_id, tipo, detalle) VALUES(?,?,?)",
            (tarea_id, "envio_fallido", detalle),
        )


# ── Tareas de trabajadores ────────────────────────────────────────────────────

def crear_tarea(responsable_id, actividad, detalles, horario):
    with conn() as c:
        cur = c.execute(
            "INSERT INTO tareas(responsable_id, actividad, detalles, horario) VALUES(?,?,?,?)",
            (responsable_id, actividad, detalles, horario),
        )
        tarea_id = cur.lastrowid
        c.execute(
            "INSERT INTO eventos(tarea_id, tipo, detalle) VALUES(?,?,?)",
            (tarea_id, "creada", actividad),
        )
        return tarea_id


def tareas_pendientes(persona_id):
    """Tareas abiertas (pendiente / con_incidencia) de ESE trabajador."""
    with conn() as c:
        return c.execute(
            """SELECT * FROM tareas
               WHERE responsable_id=? AND estado NOT IN ('completada','cancelada')
               ORDER BY creada_en, id""",
            (persona_id,),
        ).fetchall()


def completar_tarea(tarea_id, persona_id):
    """Completa la tarea solo si es de esa persona. Devuelve True si se completó."""
    with conn() as c:
        cur = c.execute(
            """UPDATE tareas SET estado='completada',
               completada_en=datetime('now','localtime')
               WHERE id=? AND responsable_id=? AND estado NOT IN ('completada','cancelada')""",
            (tarea_id, persona_id),
        )
        if cur.rowcount != 1:
            return False
        c.execute(
            "INSERT INTO eventos(tarea_id, tipo, detalle) VALUES(?,?,?)",
            (tarea_id, "completada", None),
        )
        return True


def incidencia_tarea(tarea_id, detalle, persona_id):
    """Marca incidencia solo si la tarea es de esa persona. Devuelve True si se registró."""
    with conn() as c:
        cur = c.execute(
            """UPDATE tareas SET estado='con_incidencia'
               WHERE id=? AND responsable_id=? AND estado NOT IN ('completada','cancelada')""",
            (tarea_id, persona_id),
        )
        if cur.rowcount != 1:
            return False
        c.execute(
            "INSERT INTO eventos(tarea_id, tipo, detalle) VALUES(?,?,?)",
            (tarea_id, "incidencia", detalle),
        )
        return True


# ── Resumen diario ────────────────────────────────────────────────────────────

def resumen_diario():
    """Estado de las tareas de hoy por trabajador."""
    with conn() as c:
        trabajadores_rows = c.execute(
            "SELECT id, nombre FROM personas WHERE rol='trabajador'"
        ).fetchall()
        resultado = []
        for w in trabajadores_rows:
            filas = c.execute(
                """SELECT actividad, estado FROM tareas
                   WHERE responsable_id=?
                   AND date(creada_en)=date('now','localtime')""",
                (w["id"],),
            ).fetchall()
            resultado.append({
                "nombre": w["nombre"],
                "asignadas": len(filas),
                "completadas": sum(1 for f in filas if f["estado"] == "completada"),
                "pendientes": sum(1 for f in filas if f["estado"] == "pendiente"),
                "incidencias": [
                    f["actividad"] for f in filas if f["estado"] == "con_incidencia"
                ],
            })
        return resultado


def resumen_enviado_hoy():
    with conn() as c:
        row = c.execute(
            "SELECT 1 FROM resumen_diario WHERE fecha=date('now','localtime')"
        ).fetchone()
        return row is not None


def registrar_resumen_hoy():
    with conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO resumen_diario(fecha) VALUES(date('now','localtime'))"
        )


# ── Recordatorios y citas programados ────────────────────────────────────────

def crear_recordatorio(cliente, telefono, mensaje, fecha_hora):
    with conn() as c:
        cur = c.execute(
            """INSERT INTO recordatorios(cliente, telefono, mensaje, fecha_hora)
               VALUES(?,?,?,?)""",
            (cliente, telefono, mensaje, fecha_hora),
        )
        return cur.lastrowid


def recordatorios_pendientes():
    """Recordatorios no enviados cuya hora ya llegó."""
    with conn() as c:
        return c.execute(
            """SELECT * FROM recordatorios
               WHERE enviado=0
               AND fecha_hora <= datetime('now','localtime')"""
        ).fetchall()


def marcar_recordatorio_enviado(rid):
    with conn() as c:
        c.execute("UPDATE recordatorios SET enviado=1 WHERE id=?", (rid,))


def crear_cita(cliente_nombre, telefono, mascota, servicio, fecha_hora):
    with conn() as c:
        cur = c.execute(
            """INSERT INTO citas(cliente_nombre, telefono, mascota, servicio, fecha_hora)
               VALUES(?,?,?,?,?)""",
            (cliente_nombre, telefono, mascota, servicio, fecha_hora),
        )
        return cur.lastrowid


def citas_proximas():
    """Citas cuyo recordatorio aún no se envió."""
    with conn() as c:
        return c.execute(
            "SELECT * FROM citas WHERE recordatorio_enviado=0 ORDER BY fecha_hora"
        ).fetchall()


def marcar_cita_recordatorio_enviado(cid):
    with conn() as c:
        c.execute("UPDATE citas SET recordatorio_enviado=1 WHERE id=?", (cid,))


if __name__ == "__main__":
    init()
    cargar_personas()
    print("Base de datos lista. Personas cargadas:")
    with conn() as c:
        for p in c.execute("SELECT telefono, lid, nombre, rol FROM personas"):
            print(f"  {p['nombre']} ({p['rol']}) - tel {p['telefono']} - lid {p['lid']}")
