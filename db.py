"""PatiCare - base de datos (SQLite).

Para cargar a las personas: copia personas.example.json a personas.json,
edítalo con los datos reales y ejecuta  python db.py
"""
import json
import os
import sqlite3

from dotenv import load_dotenv

AQUI = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(AQUI, ".env"))  # no pisa variables ya definidas en la terminal

# Cada negocio tiene su propia carpeta de datos (base, sesión de WhatsApp, personas, negocio.json).
# Se elige con la variable PATI_DATA_DIR; si no existe, se usa la carpeta del proyecto.
DATA_DIR = os.path.join(AQUI, os.getenv("PATI_DATA_DIR") or "")
os.makedirs(DATA_DIR, exist_ok=True)
DB = os.path.join(DATA_DIR, "pati.db")
PERSONAS_JSON = os.path.join(DATA_DIR, "personas.json")  # datos reales; NO se sube a git


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
                rol TEXT NOT NULL CHECK(rol IN ('maestro','trabajador','cliente')),
                extras TEXT NOT NULL DEFAULT ''  -- roles adicionales separados por coma: atencion, trabajador
            );
            CREATE TABLE IF NOT EXISTS tareas(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                responsable_id INTEGER NOT NULL REFERENCES personas(id),
                actividad TEXT NOT NULL,
                detalles TEXT,
                horario TEXT,
                estado TEXT NOT NULL DEFAULT 'pendiente',
                creada_en TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                completada_en TEXT,
                vence_en TEXT,                              -- plazo "YYYY-MM-DD HH:MM" (hora de Lima), si el dueño dio uno
                rec_previo INTEGER NOT NULL DEFAULT 0,      -- ya se le recordó al trabajador antes del plazo
                rec_vencida_en TEXT,                        -- cuándo se le avisó que el plazo pasó
                aviso_dueno INTEGER NOT NULL DEFAULT 0      -- ya se avisó al dueño que venció sin completarse
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
                creada_en TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                duracion_min INTEGER NOT NULL DEFAULT 30,
                estado TEXT NOT NULL DEFAULT 'confirmada',  -- confirmada | cancelada
                origen TEXT NOT NULL DEFAULT 'dueno',       -- dueno | cliente (la agendó el propio cliente con Pati)
                cliente_id INTEGER
            );
            -- Mascotas de cada cliente (solo para rubros como veterinaria)
            CREATE TABLE IF NOT EXISTS mascotas(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cliente_id INTEGER NOT NULL REFERENCES personas(id),
                nombre TEXT NOT NULL COLLATE NOCASE,
                creada_en TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                UNIQUE(cliente_id, nombre)
            );
            -- Encuestas de WhatsApp al equipo: la pregunta, a quién se envió (un mensaje por persona) y los votos
            CREATE TABLE IF NOT EXISTS encuestas(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pregunta TEXT NOT NULL,
                opciones TEXT NOT NULL,                     -- lista JSON
                tipo TEXT NOT NULL DEFAULT 'equipo',        -- 'equipo' (pregunta libre) o 'checkin' (¿cómo va tu día?)
                creada_en TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                creador_id INTEGER,
                recordatorio_enviado INTEGER NOT NULL DEFAULT 0,
                resumen_enviado INTEGER NOT NULL DEFAULT 0,
                datos TEXT                                  -- JSON extra (en los selectores: qué tareas hay detrás de cada opción)
            );
            CREATE TABLE IF NOT EXISTS encuesta_envios(
                msg_id TEXT PRIMARY KEY,                    -- id del mensaje de la encuesta en WhatsApp
                encuesta_id INTEGER NOT NULL REFERENCES encuestas(id),
                persona_id INTEGER NOT NULL REFERENCES personas(id)
            );
            CREATE TABLE IF NOT EXISTS votos(
                encuesta_id INTEGER NOT NULL REFERENCES encuestas(id),
                persona_id INTEGER NOT NULL REFERENCES personas(id),
                opcion TEXT NOT NULL,
                fecha TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                detalle TEXT,                               -- lo que contó quien marcó "tengo un problema"
                PRIMARY KEY(encuesta_id, persona_id)
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
                creado_en TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                linea TEXT NOT NULL DEFAULT 'central'  -- por qué línea de WhatsApp se habló
            );
            -- Chats donde una persona del negocio escribió: Pati se calla hasta "hasta"
            CREATE TABLE IF NOT EXISTS pausas(
                chat TEXT PRIMARY KEY,
                hasta TEXT NOT NULL
            );
            -- IDs de mensajes de WhatsApp ya atendidos (evita duplicar por reentrega)
            CREATE TABLE IF NOT EXISTS mensajes_procesados(
                id TEXT PRIMARY KEY,
                creado_en TEXT NOT NULL DEFAULT (datetime('now','localtime'))
            );
            """
        )
        # Migraciones para bases creadas antes de estas columnas
        if "extras" not in {r["name"] for r in c.execute("PRAGMA table_info(personas)")}:
            c.execute("ALTER TABLE personas ADD COLUMN extras TEXT NOT NULL DEFAULT ''")
        if "linea" not in {r["name"] for r in c.execute("PRAGMA table_info(mensajes)")}:
            c.execute("ALTER TABLE mensajes ADD COLUMN linea TEXT NOT NULL DEFAULT 'central'")
        cols_tareas = {r["name"] for r in c.execute("PRAGMA table_info(tareas)")}
        for nombre, ddl in (("vence_en", "TEXT"), ("rec_previo", "INTEGER NOT NULL DEFAULT 0"),
                            ("rec_vencida_en", "TEXT"), ("aviso_dueno", "INTEGER NOT NULL DEFAULT 0")):
            if nombre not in cols_tareas:
                c.execute(f"ALTER TABLE tareas ADD COLUMN {nombre} {ddl}")
        if "detalle" not in {r["name"] for r in c.execute("PRAGMA table_info(votos)")}:
            c.execute("ALTER TABLE votos ADD COLUMN detalle TEXT")
        if "datos" not in {r["name"] for r in c.execute("PRAGMA table_info(encuestas)")}:
            c.execute("ALTER TABLE encuestas ADD COLUMN datos TEXT")
        cols_citas = {r["name"] for r in c.execute("PRAGMA table_info(citas)")}
        for nombre, ddl in (("duracion_min", "INTEGER NOT NULL DEFAULT 30"),
                            ("estado", "TEXT NOT NULL DEFAULT 'confirmada'"),
                            ("origen", "TEXT NOT NULL DEFAULT 'dueno'"), ("cliente_id", "INTEGER")):
            if nombre not in cols_citas:
                c.execute(f"ALTER TABLE citas ADD COLUMN {nombre} {ddl}")
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
                """INSERT INTO personas(telefono, lid, nombre, rol, extras) VALUES(?,?,?,?,?)
                   ON CONFLICT(telefono) DO UPDATE SET
                   lid=COALESCE(excluded.lid, personas.lid), nombre=excluded.nombre, rol=excluded.rol,
                   extras=excluded.extras""",
                (p["telefono"], p.get("lid"), p["nombre"], p["rol"], ",".join(p.get("tambien", []))),
            )


def persona_por_id(ident):
    with conn() as c:
        return c.execute(
            "SELECT * FROM personas WHERE telefono=? OR lid=?", (ident, ident)
        ).fetchone()


def persona_por_pk(persona_id):
    with conn() as c:
        return c.execute("SELECT * FROM personas WHERE id=?", (persona_id,)).fetchone()


_TIENE = "(',' || extras || ',') LIKE ?"


def trabajadores():
    with conn() as c:
        return c.execute("SELECT * FROM personas WHERE rol='trabajador'").fetchall()


def asignables():
    """Quienes pueden recibir tareas: los trabajadores y quien tenga el rol adicional "trabajador" (ej. el dueño)."""
    with conn() as c:
        return c.execute(
            f"SELECT * FROM personas WHERE rol='trabajador' OR {_TIENE} ORDER BY id", ("%,trabajador,%",)
        ).fetchall()


def personas_con_extra(extra):
    """Personas con un rol adicional, por ejemplo "atencion" (quien atiende a los clientes)."""
    with conn() as c:
        return c.execute(f"SELECT * FROM personas WHERE {_TIENE} ORDER BY id", (f"%,{extra},%",)).fetchall()


def duenos():
    with conn() as c:
        return c.execute("SELECT * FROM personas WHERE rol='maestro'").fetchall()


def actualizar_nombre(persona_id, nombre):
    with conn() as c:
        c.execute("UPDATE personas SET nombre=? WHERE id=?", (nombre, persona_id))


def mascotas_de(cliente_id):
    with conn() as c:
        return c.execute("SELECT * FROM mascotas WHERE cliente_id=? ORDER BY id", (cliente_id,)).fetchall()


def agregar_mascota(cliente_id, nombre):
    """Guarda la mascota de un cliente (si ya la tenía, no se duplica)."""
    with conn() as c:
        c.execute("INSERT OR IGNORE INTO mascotas(cliente_id, nombre) VALUES(?,?)", (cliente_id, nombre))


def crear_cliente(telefono, lid, nombre):
    """Registra un cliente nuevo (alguien que escribió por primera vez) y lo devuelve."""
    with conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO personas(telefono, lid, nombre, rol) VALUES(?,?,?,'cliente')",
            (telefono, lid, (nombre or "").strip() or "Cliente"),
        )
        return c.execute("SELECT * FROM personas WHERE telefono=?", (telefono,)).fetchone()


def guardar_lid(persona_id, lid):
    """Aprende el ID interno de WhatsApp de alguien ya registrado por teléfono."""
    with conn() as c:
        c.execute("UPDATE personas SET lid=? WHERE id=? AND (lid IS NULL OR lid='')", (lid, persona_id))


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


def log_mensaje(persona_id, rol, texto, linea="central"):
    with conn() as c:
        c.execute(
            "INSERT INTO mensajes(persona_id, rol, texto, linea) VALUES(?,?,?,?)",
            (persona_id, rol, texto, linea),
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


def pausar_chat(chat, hasta_iso):
    with conn() as c:
        c.execute(
            """INSERT INTO pausas(chat, hasta) VALUES(?,?)
               ON CONFLICT(chat) DO UPDATE SET hasta=excluded.hasta""",
            (chat, hasta_iso),
        )


def chat_pausado(chat, ahora_iso):
    with conn() as c:
        fila = c.execute("SELECT hasta FROM pausas WHERE chat=?", (chat,)).fetchone()
    return bool(fila) and fila["hasta"] > ahora_iso


def respuestas_recientes(persona_id, minutos, linea=None):
    """Cuántas veces le respondió Pati a esta persona en los últimos minutos (anti-bucle), opcionalmente por línea."""
    with conn() as c:
        return c.execute(
            """SELECT COUNT(*) AS n FROM mensajes
               WHERE persona_id=? AND rol='pati' AND creado_en >= datetime('now','localtime',?)
               AND (? IS NULL OR linea=?)""",
            (persona_id, f"-{int(minutos)} minutes", linea, linea),
        ).fetchone()["n"]


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
                    f"SELECT id, nombre, telefono FROM personas WHERE lower(nombre)=lower(?) "
                    f"AND (rol='trabajador' OR {_TIENE})",
                    (t["responsable"], "%,trabajador,%"),
                ).fetchone()
                if w is None:
                    raise ValueError(f"responsable no registrado: {t['responsable']}")
                cur = c.execute(
                    "INSERT INTO tareas(responsable_id, actividad, detalles, horario, vence_en) VALUES(?,?,?,?,?)",
                    (w["id"], t["actividad"], t.get("detalles"), t.get("horario"), t.get("vence")),
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

def crear_tarea(responsable_id, actividad, detalles, horario, vence_en=None):
    with conn() as c:
        cur = c.execute(
            "INSERT INTO tareas(responsable_id, actividad, detalles, horario, vence_en) VALUES(?,?,?,?,?)",
            (responsable_id, actividad, detalles, horario, vence_en),
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


def tarea_abierta_de(tarea_id, persona_id):
    """La tarea si está abierta Y es de esa persona (para los botones); si no, None."""
    with conn() as c:
        return c.execute(
            "SELECT * FROM tareas WHERE id=? AND responsable_id=? AND estado NOT IN ('completada','cancelada')",
            (tarea_id, persona_id),
        ).fetchone()


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
            f"SELECT id, nombre FROM personas WHERE rol='trabajador' OR {_TIENE} ORDER BY id", ("%,trabajador,%",)
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


def tareas_con_plazo():
    """Tareas pendientes (sin incidencia) que tienen plazo, con los datos de su responsable, para el seguimiento."""
    with conn() as c:
        return c.execute(
            """SELECT t.*, p.nombre AS resp_nombre, p.telefono AS resp_telefono, p.id AS resp_id, p.rol AS resp_rol
               FROM tareas t JOIN personas p ON p.id=t.responsable_id
               WHERE t.vence_en IS NOT NULL AND t.estado='pendiente' ORDER BY t.vence_en, t.id"""
        ).fetchall()


def marcar_seguimiento(tarea_id, campo, valor=1):
    """Anota qué aviso de seguimiento ya se envió (para no repetirlo)."""
    if campo not in ("rec_previo", "rec_vencida_en", "aviso_dueno"):
        raise ValueError(campo)
    with conn() as c:
        c.execute(f"UPDATE tareas SET {campo}=? WHERE id=?", (valor, tarea_id))


def cambiar_vence(tarea_id, persona_id, nuevo_iso):
    """Nuevo plazo de una tarea abierta de esa persona; se reinician los avisos. True si se cambió."""
    with conn() as c:
        cur = c.execute(
            """UPDATE tareas SET vence_en=?, rec_previo=0, rec_vencida_en=NULL, aviso_dueno=0
               WHERE id=? AND responsable_id=? AND estado NOT IN ('completada','cancelada')""",
            (nuevo_iso, tarea_id, persona_id),
        )
        return cur.rowcount == 1


def tareas_por_ids(ids):
    """Tareas abiertas entre esos ids, con los datos de su responsable."""
    if not ids:
        return []
    with conn() as c:
        return c.execute(
            f"""SELECT t.*, p.nombre AS resp_nombre, p.telefono AS resp_telefono
                FROM tareas t JOIN personas p ON p.id=t.responsable_id
                WHERE t.id IN ({','.join('?' * len(ids))}) AND t.estado NOT IN ('completada','cancelada')
                ORDER BY t.id""",
            list(ids),
        ).fetchall()


def cancelar_tarea(tarea_id):
    """Cancela una tarea abierta. True si se canceló."""
    with conn() as c:
        cur = c.execute(
            "UPDATE tareas SET estado='cancelada' WHERE id=? AND estado NOT IN ('completada','cancelada')",
            (tarea_id,),
        )
        if cur.rowcount != 1:
            return False
        c.execute("INSERT INTO eventos(tarea_id, tipo, detalle) VALUES(?,?,?)", (tarea_id, "cancelada", None))
        return True


def reasignar_tarea(tarea_id, nuevo_id):
    """Pasa una tarea abierta a otra persona y reinicia sus avisos. True si se pasó."""
    with conn() as c:
        viejo = c.execute("SELECT p.nombre FROM tareas t JOIN personas p ON p.id=t.responsable_id WHERE t.id=?",
                          (tarea_id,)).fetchone()
        cur = c.execute(
            """UPDATE tareas SET responsable_id=?, estado='pendiente', rec_previo=0, rec_vencida_en=NULL, aviso_dueno=0
               WHERE id=? AND estado NOT IN ('completada','cancelada')""",
            (nuevo_id, tarea_id),
        )
        if cur.rowcount != 1:
            return False
        c.execute("INSERT INTO eventos(tarea_id, tipo, detalle) VALUES(?,?,?)",
                  (tarea_id, "reasignada", f"antes: {viejo['nombre']}" if viejo else None))
        return True


def registrar_evento(tarea_id, tipo, detalle=None):
    with conn() as c:
        c.execute("INSERT INTO eventos(tarea_id, tipo, detalle) VALUES(?,?,?)", (tarea_id, tipo, detalle))


def historial_tareas(persona_id, limite=3):
    """Las últimas tareas de una persona (cualquier estado) con su línea de tiempo de eventos."""
    with conn() as c:
        tareas = c.execute("SELECT * FROM tareas WHERE responsable_id=? ORDER BY id DESC LIMIT ?",
                           (persona_id, limite)).fetchall()
        return [{"tarea": t, "eventos": c.execute(
            "SELECT tipo, detalle, fecha FROM eventos WHERE tarea_id=? ORDER BY id", (t["id"],)).fetchall()}
            for t in tareas]


def tareas_abiertas_de_todos():
    """Todas las tareas abiertas (pendientes y con incidencia) con los datos de su responsable."""
    with conn() as c:
        return c.execute(
            """SELECT t.*, p.nombre AS resp_nombre, p.telefono AS resp_telefono, p.rol AS resp_rol
               FROM tareas t JOIN personas p ON p.id=t.responsable_id
               WHERE t.estado NOT IN ('completada','cancelada') ORDER BY p.id, t.id""").fetchall()


def resolver_incidencia(tarea_id):
    """La tarea con incidencia vuelve a pendiente (el problema ya se resolvió). True si se pudo."""
    with conn() as c:
        cur = c.execute("UPDATE tareas SET estado='pendiente' WHERE id=? AND estado='con_incidencia'", (tarea_id,))
        if cur.rowcount != 1:
            return False
        c.execute("INSERT INTO eventos(tarea_id, tipo, detalle) VALUES(?,?,?)", (tarea_id, "resuelta", None))
        return True


def estado_tareas(persona_id=None):
    """Tareas de hoy y las que siguen abiertas, con el último detalle de incidencia (para reportes)."""
    with conn() as c:
        return c.execute(
            """SELECT t.id, t.actividad, t.estado, t.creada_en, t.completada_en, t.vence_en, p.nombre,
                      (SELECT e.detalle FROM eventos e WHERE e.tarea_id=t.id AND e.tipo='incidencia'
                       ORDER BY e.id DESC LIMIT 1) AS incidencia
               FROM tareas t JOIN personas p ON p.id=t.responsable_id
               WHERE t.estado != 'cancelada'
                 AND (date(t.creada_en)=date('now','localtime')
                      OR date(t.completada_en)=date('now','localtime')
                      OR t.estado != 'completada')
                 AND (? IS NULL OR t.responsable_id=?)
               ORDER BY p.id, t.id""",
            (persona_id, persona_id),
        ).fetchall()


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


# ── Encuestas al equipo ──────────────────────────────────────────────────────

def crear_encuesta(pregunta, opciones, tipo="equipo", creador_id=None, creada_en=None, datos=None):
    """`creada_en` ("YYYY-MM-DD HH:MM:SS", hora de Lima): lo pone la lógica, con el mismo reloj con que luego mide el tiempo."""
    with conn() as c:
        return c.execute(
            "INSERT INTO encuestas(pregunta, opciones, tipo, creador_id, creada_en, datos) "
            "VALUES(?,?,?,?,COALESCE(?, datetime('now','localtime')),?)",
            (pregunta, json.dumps(list(opciones), ensure_ascii=False), tipo, creador_id, creada_en,
             json.dumps(datos, ensure_ascii=False) if datos is not None else None)).lastrowid


def registrar_envio_encuesta(msg_id, encuesta_id, persona_id):
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO encuesta_envios(msg_id, encuesta_id, persona_id) VALUES(?,?,?)",
                  (str(msg_id), encuesta_id, persona_id))


def envio_por_msg(msg_id):
    """A qué encuesta y a quién se envió ese mensaje de WhatsApp (None si no es una encuesta de Pati)."""
    with conn() as c:
        return c.execute("SELECT * FROM encuesta_envios WHERE msg_id=?", (str(msg_id),)).fetchone()


def encuesta(encuesta_id):
    with conn() as c:
        f = c.execute("SELECT * FROM encuestas WHERE id=?", (encuesta_id,)).fetchone()
    if f is None:
        return None
    d = dict(f)
    d["opciones"] = json.loads(d["opciones"])
    d["datos"] = json.loads(d["datos"]) if d.get("datos") else None
    return d


def ultima_encuesta(tipo=None):
    """La última encuesta al equipo (los selectores del propio dueño, tipo "selector_*", no cuentan)."""
    with conn() as c:
        f = c.execute("SELECT id FROM encuestas WHERE (? IS NULL OR tipo=?) AND tipo NOT LIKE 'selector%' "
                      "ORDER BY id DESC LIMIT 1", (tipo, tipo)).fetchone()
    return encuesta(f["id"]) if f else None


def guardar_voto(encuesta_id, persona_id, opcion):
    """Guarda (o cambia) el voto de esa persona. Devuelve la opción anterior, o None si votaba por primera vez."""
    with conn() as c:
        previo = c.execute("SELECT opcion FROM votos WHERE encuesta_id=? AND persona_id=?", (encuesta_id, persona_id)).fetchone()
        c.execute("INSERT OR REPLACE INTO votos(encuesta_id, persona_id, opcion) VALUES(?,?,?)",
                  (encuesta_id, persona_id, opcion))
        return previo["opcion"] if previo else None


def destinatarios_encuesta(encuesta_id):
    """Las personas a las que se envió la encuesta."""
    with conn() as c:
        return c.execute("""SELECT DISTINCT p.* FROM encuesta_envios e JOIN personas p ON p.id=e.persona_id
                            WHERE e.encuesta_id=? ORDER BY p.id""", (encuesta_id,)).fetchall()


def guardar_detalle_voto(encuesta_id, persona_id, detalle):
    """Lo que la persona contó al marcar "tengo un problema" (se ve en los resultados)."""
    with conn() as c:
        c.execute("UPDATE votos SET detalle=? WHERE encuesta_id=? AND persona_id=?", (detalle, encuesta_id, persona_id))


def votos_de(encuesta_id):
    with conn() as c:
        return c.execute("""SELECT v.opcion, v.fecha, v.detalle, p.id AS persona_id, p.nombre FROM votos v
                            JOIN personas p ON p.id=v.persona_id WHERE v.encuesta_id=? ORDER BY v.fecha, p.id""",
                         (encuesta_id,)).fetchall()


def encuestas_sin_cerrar():
    """Encuestas a las que aún no se les envió el resumen al dueño."""
    with conn() as c:
        return [dict(f) for f in c.execute(
            "SELECT id, creada_en, recordatorio_enviado, tipo FROM encuestas "
            "WHERE resumen_enviado=0 AND tipo NOT LIKE 'selector%'")]


def marcar_encuesta(encuesta_id, campo):
    if campo not in ("recordatorio_enviado", "resumen_enviado"):
        raise ValueError(campo)
    with conn() as c:
        c.execute(f"UPDATE encuestas SET {campo}=1 WHERE id=?", (encuesta_id,))


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


def crear_cita(cliente_nombre, telefono, mascota, servicio, fecha_hora, duracion_min=30, origen="dueno",
               cliente_id=None):
    with conn() as c:
        cur = c.execute(
            """INSERT INTO citas(cliente_nombre, telefono, mascota, servicio, fecha_hora, duracion_min, origen, cliente_id)
               VALUES(?,?,?,?,?,?,?,?)""",
            (cliente_nombre, telefono, mascota, servicio, fecha_hora, duracion_min, origen, cliente_id),
        )
        return cur.lastrowid


_FIN_CITA = "strftime('%Y-%m-%d %H:%M', datetime(fecha_hora, '+' || duracion_min || ' minutes'))"


def citas_ocupadas(dia_iso):
    """[(inicio_min, fin_min)] de las citas confirmadas de ese día (para calcular horarios libres)."""
    with conn() as c:
        filas = c.execute(
            "SELECT fecha_hora, duracion_min FROM citas WHERE estado='confirmada' AND substr(fecha_hora,1,10)=?",
            (dia_iso,),
        ).fetchall()
    salida = []
    for f in filas:
        inicio = int(f["fecha_hora"][11:13]) * 60 + int(f["fecha_hora"][14:16])
        salida.append((inicio, inicio + f["duracion_min"]))
    return salida


def crear_cita_si_libre(cliente_nombre, telefono, mascota, servicio, fecha_hora, duracion_min, simultaneas,
                        cliente_id=None):
    """Agenda la cita solo si el horario sigue libre (comprobación y guardado en una transacción).

    Devuelve el id de la cita, o None si otra persona ya ocupó ese horario.
    """
    c = conn()
    try:
        c.execute("BEGIN IMMEDIATE")
        fin = c.execute("SELECT strftime('%Y-%m-%d %H:%M', datetime(?, '+' || ? || ' minutes'))",
                        (fecha_hora, duracion_min)).fetchone()[0]
        ocupadas = c.execute(
            f"SELECT COUNT(*) FROM citas WHERE estado='confirmada' AND fecha_hora < ? AND {_FIN_CITA} > ?",
            (fin, fecha_hora),
        ).fetchone()[0]
        if ocupadas >= simultaneas:
            c.rollback()
            return None
        cur = c.execute(
            """INSERT INTO citas(cliente_nombre, telefono, mascota, servicio, fecha_hora, duracion_min, origen, cliente_id)
               VALUES(?,?,?,?,?,?,'cliente',?)""",
            (cliente_nombre, telefono, mascota, servicio, fecha_hora, duracion_min, cliente_id),
        )
        c.commit()
        return cur.lastrowid
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def citas_en_rango(desde_dia, hasta_dia):
    """Citas confirmadas entre dos días (YYYY-MM-DD, ambos incluidos), ordenadas por fecha y hora."""
    with conn() as c:
        return c.execute(
            """SELECT * FROM citas WHERE estado='confirmada'
               AND substr(fecha_hora,1,10) >= ? AND substr(fecha_hora,1,10) <= ? ORDER BY fecha_hora, id""",
            (desde_dia, hasta_dia),
        ).fetchall()


def citas_futuras_de(telefono, ahora_iso):
    """Citas confirmadas de ese teléfono que aún no pasan (ahora_iso: YYYY-MM-DD HH:MM)."""
    with conn() as c:
        return c.execute(
            "SELECT * FROM citas WHERE telefono=? AND estado='confirmada' AND fecha_hora >= ? ORDER BY fecha_hora",
            (telefono, ahora_iso),
        ).fetchall()


def cancelar_cita(cita_id, telefono):
    """Cancela la cita solo si es de ese teléfono y sigue confirmada. Devuelve True si se canceló."""
    with conn() as c:
        cur = c.execute(
            "UPDATE citas SET estado='cancelada' WHERE id=? AND telefono=? AND estado='confirmada'",
            (cita_id, telefono),
        )
        return cur.rowcount == 1


def citas_proximas():
    """Citas cuyo recordatorio aún no se envió."""
    with conn() as c:
        return c.execute(
            "SELECT * FROM citas WHERE recordatorio_enviado=0 AND estado='confirmada' ORDER BY fecha_hora"
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
