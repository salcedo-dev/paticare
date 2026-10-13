# PatiCare – Pati, asistente de WhatsApp para veterinarias

Proyecto del curso Plan de Negocios (PUCP). Pati ayuda al dueño de una veterinaria pequeña a
**delegar y supervisar desde WhatsApp** sin perseguir a cada trabajador.

**Hipótesis a validar:** "el dueño puede delegar y supervisar su veterinaria desde WhatsApp, sin perseguir a cada trabajador".

## Qué hace

| Rol | Puede |
|---|---|
| **Dueño** (`maestro`) | Escribir instrucciones desordenadas ("ashly baña al perro y mafer limpia el quirofano pa las 5"). Pati propone, el dueño corrige en lenguaje natural y confirma con SÍ/NO. También crea recordatorios y citas para clientes. |
| **Trabajador** | Recibe solo sus tareas. Responde ✅ (completar) o ⚠️ (incidencia, avisa al dueño) y pregunta "¿qué tengo pendiente?". |
| **Cliente** | Pregunta por servicios y pide cita (se deriva al dueño). Recibe recordatorios. |
| **Automático** | Resumen diario al dueño a las 7:00 p. m. (hora Lima), recordatorios y avisos de citas programados. |

Reglas del producto: Pati **nunca inventa datos** (si falta algo, pregunta); toda acción que envía mensajes a otros
requiere confirmación del dueño; cada trabajador solo ve lo suyo; se ignoran grupos, mensajes propios y números
no registrados; mensajes en español, amables y cortos.

## Arquitectura

```
WhatsApp ⇄ main.py (neonize, pausa "humana", scheduler)
               │  mensaje entrante
               ▼
           logica.py   ← flujos dueño / trabajador / cliente (sin WhatsApp, 100 % probable)
            │      │
            ▼      ▼
         ia.py    db.py
      (Gemini    (SQLite: personas, tareas, eventos, recordatorios, citas,
       por REST)  conversaciones, mensajes, mensajes_procesados)
```

- **Un solo LLM + funciones Python controladas.** La IA solo *interpreta* (devuelve JSON); Python valida
  responsables, teléfonos y fechas, y es quien guarda y envía. La IA nunca escribe en la BD ni envía mensajes.
- **El estado de la conversación vive en SQLite** (propuesta pendiente, paso del trabajador): sobrevive a reinicios.
- **Confirmación atómica:** guardar la propuesta y borrarla es una sola transacción (un segundo "sí" no duplica).
- **Pausa aleatoria + "escribiendo…"** antes de cada envío, para no parecer un bot.

| Archivo | Para qué sirve |
|---|---|
| `main.py` | Conexión con WhatsApp (neonize), envío con pausa, scheduler |
| `logica.py` | Toda la conversación; recibe una "salida" falsa en las pruebas |
| `ia.py` | Cliente REST de Gemini (errores diferenciados, un reintento) e interpretación del dueño |
| `db.py` | Esquema SQLite y consultas |
| `test_flujos.py`, `test_ia.py` | Pruebas con aserciones, sin WhatsApp ni Gemini |

## Instalación (Windows, PowerShell)

Requiere **Python 3.11** (en la máquina puede haber otros; usa siempre el del venv).

```powershell
py -3.11 -m venv venv
venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env             # y pon tu GEMINI_API_KEY (gratis en aistudio.google.com/apikey)
copy personas.example.json personas.json   # y pon tus números reales
venv\Scripts\python.exe db.py      # crea la base y carga las personas
```

`personas.json`: `telefono` con código de país (sin `+`), `lid` (ver abajo) y `rol` = `maestro` | `trabajador` | `cliente`.
WhatsApp a veces identifica a una persona por un ID interno (`lid`) en vez del teléfono: si Pati ignora a alguien
registrado, mira la línea `[DEBUG] ID: ...` de la terminal, copia ese número al campo `lid` y vuelve a ejecutar `db.py`.

## Ejecutar

```powershell
$env:PYTHONUTF8=1; venv\Scripts\python.exe main.py
```

La primera vez muestra un QR: escanéalo con el WhatsApp de un **chip dedicado** (Dispositivos vinculados).
Opcional: `$env:PATI_DEBUG=1` imprime también el texto de los mensajes.

## Pruebas (no usan WhatsApp ni gastan cupo de Gemini)

```powershell
venv\Scripts\python.exe -m unittest -v
```

Úsalas antes de cada cambio. Cualquier cambio en los flujos debe venir con su prueba.

## Trabajo en equipo

- **Cada persona usa su propia `GEMINI_API_KEY`** (gratis) y, si prueba con WhatsApp real, **su propio número y
  su propia sesión** (`pati_sesion.sqlite3`). No se comparte una sesión entre dos computadoras.
- Para casi todo (lógica, prompts, base de datos) no hace falta WhatsApp: se desarrolla con las pruebas.
- Flujo sugerido: una rama por tarea (`git checkout -b feature/nombre`), pull request, que pasen las pruebas y una
  persona revisa antes de unir a `main`.
- **Nunca subir:** `.env`, `personas.json`, `*.db`, `*.sqlite3` (ya están en `.gitignore`). Si una clave se filtra, se regenera.

## Límites conocidos

- WhatsApp vía neonize **no es la API oficial**: existe riesgo de bloqueo del número. Mitigaciones: chip dedicado,
  pausas aleatorias, solo escribir a quien está registrado. Para producción real, evaluar la API oficial de WhatsApp Business.
- El plan gratis de Gemini tiene límites diarios y por minuto; los 503 de Google ocurren en horas de alta demanda.
  Pati distingue "sin cupo", "servicio saturado" y "error de configuración" y no ejecuta nada ante un fallo.
- Aún no soporta audios, Google Calendar, ni varios locales. Los recordatorios usan la hora del servidor (debe estar en Lima).
