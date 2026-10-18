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
| `ia.py` | Cliente REST de Gemini (errores diferenciados, reintento, cadena de modelos de respaldo) e interpretación del dueño |
| `negocio.py` | Perfil del negocio: rubro, servicios, horario (`negocio.json`) |
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

### Varios negocios en la misma PC

Cada negocio tiene su carpeta `datos\<nombre>\` (base, sesión de WhatsApp, personas y configuración) y su propio
proceso, en una terminal distinta. La carpeta se elige con `PATI_DATA_DIR`:

```powershell
mkdir datos\clinica1
copy personas.example.json datos\clinica1\personas.json     # y edítalo
copy negocio.example.json datos\clinica1\negocio.json       # nombre, rubro, servicios, horario
$env:PATI_DATA_DIR="datos\clinica1"; venv\Scripts\python.exe db.py
$env:PYTHONUTF8=1; $env:PATI_DATA_DIR="datos\clinica1"; venv\Scripts\python.exe main.py   # escanea el QR de ESE número
```

`negocio.json`: `rubro` (`veterinaria`, `dentista` o `generico`; cambia el vocabulario), `servicios`, `horario`,
`atender_desconocidos` (por defecto `true`), `ignorar` (números a los que Pati nunca responde),
`avisar_completadas` (por defecto `true`: el dueño recibe un aviso apenas alguien completa una tarea) y
`horas_reporte` (ej. `["13:00", "17:00"]`: horas en que Pati le envía al dueño el estado de las tareas, además del
resumen diario de las 7 p. m.; no envía nada si no hay tareas). El dueño también puede preguntar cuando quiera
("¿cómo va todo?", "¿quiénes ya terminaron?", "¿cómo va Mafer?") y Pati responde con los datos guardados. Si no hay servicios
cargados, Pati no inventa: deriva a una persona. Sin `PATI_DATA_DIR` se usa la carpeta del proyecto.

### Dos líneas de WhatsApp: central y corporativa

Un solo programa maneja dos números a la vez:

| Línea | Archivo de sesión | Quién escribe |
|---|---|---|
| **Central** (el número de Pati) | `pati_sesion.sqlite3` | El dueño y el equipo |
| **Corporativa** (el número del negocio, vinculada como dispositivo) | `sesion_corporativa.sqlite3` | Los clientes |

Se activa poniendo los números en `negocio.json`: `"lineas": {"central": "51900000100", "corporativa": "51900000200"}`.
Cada línea pide su QR la primera vez; después queda guardada. Sin `lineas.corporativa` funciona solo con la central.

- **En la línea corporativa todos son clientes**, incluso el equipo y el dueño (sirve para probar haciéndose pasar por cliente).
- **En la línea central** manda el rol: el dueño da órdenes, el equipo recibe tareas, y un cliente que escribe ahí
  recibe el aviso de que es un número interno. Un número no registrado recibe una sola respuesta informativa por hora.
- Los avisos internos (tareas, incidencias, derivaciones) salen por la central; los recordatorios y citas a clientes
  salen por la corporativa. Entre las dos líneas **no se escriben mensajes**: todo pasa dentro del programa.
- Reglas fijas: las líneas de Pati nunca se responden entre sí (evita bucles) y se ignoran los mensajes de más de
  10 minutos (historial al vincular, caídas).

**Roles flexibles** en `personas.json`: además de `rol` (`maestro`, `trabajador`), cada persona puede tener
`"tambien": ["atencion", "trabajador"]`.
- `atencion`: recibe los avisos cuando un cliente pide hablar con una persona (puede ser el propio dueño; si nadie
  tiene ese rol, el aviso va al dueño).
- `trabajador`: se le pueden asignar tareas aunque sea dueño ("yo reviso la caja"); responde "listo" o "problema" como el equipo.

### Plazos y seguimiento automático de tareas

Si el dueño dice una hora ("mafer limpia el quirófano para las 5"), la tarea queda con un **plazo** (la IA lo convierte
en fecha y hora reales; si no se dijo una hora, no se inventa). Pati vigila cada plazo una vez por minuto:

1. Unos minutos antes (por defecto 15) le recuerda a la persona.
2. Al vencer sin completarse le pregunta "¿ya la hiciste?" (*listo*, *problema* o *más tiempo*).
3. Pasada la gracia (por defecto 10 minutos) sin respuesta, le avisa **una sola vez** al dueño.

El trabajador puede escribir *más tiempo* y decir "6pm", "18:30", "en 30 minutos" o "mañana a las 10": el plazo se actualiza,
los avisos se reinician y el dueño recibe un aviso. Nunca se repite el mismo aviso y no se envían avisos de noche. Las tareas
atrasadas aparecen aparte en el reporte y en el resumen diario. Configurable en `negocio.json`:

```json
"seguimiento": {"activo": true, "recordar_min_antes": 15, "gracia_min": 10, "avisos_desde": "07:00", "avisos_hasta": "21:00"}
```

### Ficha del cliente y citas con Pati (línea corporativa)

Todo con reglas fijas (sin IA). Pati pregunta el nombre al primer contacto (se puede omitir) y, en veterinaria, el nombre
de la mascota al agendar; las mascotas quedan guardadas por cliente. Para que Pati **agende sola** el negocio debe
configurar su agenda en `negocio.json`; si no hay agenda, Pati no inventa horarios y deriva a una persona:

```json
"servicios": [{"nombre": "Consulta general", "duracion_min": 30}, {"nombre": "Baño y corte", "duracion_min": 60}],
"agenda": {"dias": ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado"], "desde": "09:00", "hasta": "18:00",
           "paso_min": 30, "simultaneas": 1, "anticipacion_horas": 2, "dias_adelante": 14, "cerrado": ["2030-12-25"]},
"avisar_citas": true
```

- `simultaneas`: cuántas citas pueden coincidir en el mismo horario (por ejemplo 2 si hay dos veterinarios).
- `anticipacion_horas`: no se ofrecen horarios que empiezan antes de esas horas desde ahora.
- Flujo del cliente: servicio → día → hora → mascota (solo si aplica) → confirmación SÍ/NO. Antes de guardar se comprueba
  otra vez que el horario siga libre (dos clientes no pueden agendar la misma hora).
- El cliente puede escribir *mis citas*, *cancelar cita* o *reprogramar mi cita*. En cualquier momento puede escribir
  *salir* o pedir una persona.
- Quien tiene el rol `atencion` recibe un aviso de cada cita nueva o cancelada (se apaga con `"avisar_citas": false`) y,
  igual que el dueño, puede preguntar "¿qué citas hay mañana?", "citas de hoy", "citas de esta semana".

### Ayuda para el equipo

Cualquiera puede preguntarle a Pati qué hace: "ayuda", "/help", "qué funciones tienes", "para qué sirves", "cómo funcionas".
La respuesta es un texto fijo (sin IA, así nunca inventa funciones) y depende del rol: el trabajador ve lo que puede
hacer con sus tareas, el dueño ve todo lo que Pati hace por él. También hay respuestas cortas para "cómo completo una
tarea", "cómo reporto un problema" y "cómo veo mis tareas". **Si se agrega una función nueva, hay que actualizar esos
textos** (`texto_ayuda_trabajador` y `texto_ayuda_dueno` en `logica.py`).

### Botones de WhatsApp para el equipo

Cada tarea nueva, cada recordatorio de plazo y cada aviso de "ya venció" llega con botones de respuesta rápida
(**✅ Listo · ⚠️ Problema · ⏳ Más tiempo**). El botón lleva el número de la tarea, así que con varias tareas nunca hay
que preguntar "¿cuál?". "Más tiempo" ofrece **15 min / 30 min / 1 hora** (o escribir una hora). Todo lo que se podía
escribir sigue funcionando.

- Se apagan con `"botones": false` en `negocio.json` (todo vuelve a ser texto, sin tocar código).
- Si WhatsApp rechaza un envío con botones, Pati lo manda como texto con las opciones al final.
- Un toque solo actúa sobre tareas **de esa persona** y abiertas; tocar dos veces no duplica nada.
- `prueba_botones.py` es la prueba suelta de botones y encuestas (con Pati apagada).

### Comandos del dueño (`.`)

Mensajes que **empiezan con un punto**. Siempre hacen lo mismo (sin IA), así se evitan los malentendidos. Solo es comando
si el punto va al **inicio**; todo lo demás se entiende como siempre. Lo que avisa a otras personas sigue pidiendo tu **SÍ**.

| Comando | Qué hace |
|---|---|
| `.tarea mafer y ashly: bañar a firulais para las 5` | Prepara las tareas (plazo: "para las 5", "en 30 minutos", "mañana a las 10"; `todos` = todo el equipo). |
| `.estado [persona]` | Cómo van las tareas. |
| `.encuesta ¿Quién cubre el sábado? opciones: yo, no puedo` | Encuesta al equipo (sin opciones: Sí / No). |
| `.resultados` | Cómo va la última encuesta. |
| `.checkin` | "¿Cómo va tu día?" al equipo. |
| `.recordar [personas]` | Les recuerdo a cada uno sus pendientes. |
| `.resolver [personas]` | Marca incidencias como resueltas y avisa. |
| `.cancelar mafer` · `.cancelar todas` | Cancela tareas de una persona, o todas. |
| `.cancelar probar los botones` | Cancela esa tarea **para todo el equipo** (también `.cancelar todos probar los botones`). |
| `.cancelar` (solo) | Te manda una **encuesta de varias opciones** con tus tareas pendientes (agrupadas por nombre): marcas las que quieres cancelar, tocas *➡️ Continuar* y después pides el SÍ. |
| `.cancelar .mafer` | La misma lista, solo con las tareas de ella (también `.cancelar .mafer .ashly` o `.cancelar .equipo`). Los nombres con punto sirven en todos los comandos. |
| `.reasignar mafer a ashly` | Pasa la tarea a otra persona. |
| `.plazo mafer 6pm` | Cambia el plazo. |
| `.aviso mañana abrimos tarde` | Aviso a todo el equipo. |
| `.historial mafer` · `.agenda hoy` · `.deshacer` | Historial, agenda de citas, deshacer el último envío. |
| `.ayuda` | La lista. |

Si te equivocas de comando, Pati sugiere el más parecido (`.estdo` → "¿Quisiste decir `.estado`?"). Mientras haya una
propuesta sin confirmar, los comandos que envían algo esperan tu SÍ o NO; los de lectura (`.estado`, `.resultados`,
`.historial`, `.agenda`, `.ayuda`) funcionan siempre. El código está en `comandos.py`.

### Elegir con una encuesta (en vez de escribir números)

Cuando alguien del equipo tiene **varias tareas** y escribe *listo*, *problema* o *más tiempo*, Pati ya no le pide "responde con el
número": le manda una encuesta con sus tareas. *Listo* es de **marcar varias** y tocar **➡️ Continuar**; *problema* y *más
tiempo* son de **tocar una**. Todas llevan **✖️ Cancelar** para volver atrás sin cambiar nada. Si no se puede enviar la
encuesta (o hay más de 10 tareas, o `"botones": false`), se usa la lista con números de siempre, y escribir "1 y 3" sigue
funcionando. El dueño usa el mismo mecanismo con `.cancelar`.

### Encuestas al equipo y check-in diario

Pensadas para el día a día (turnos, coberturas, confirmaciones, decisiones rápidas). El equipo solo toca una opción
en WhatsApp; el dueño no persigue a nadie. Sin IA (no gasta cupo).

| Dices | Pati hace |
|---|---|
| "encuesta ¿Quién cubre el sábado? opciones: yo, no puedo" | Te muestra la encuesta y, con tu **SÍ**, la envía a cada trabajador (no a ti). |
| "pregúntale al equipo si pueden quedarse hasta las 8" | Lo mismo, con opciones Sí / No. |
| "¿cómo va la encuesta?" / "resultados de la encuesta" | Quién votó qué y quién falta. |
| "haz el check-in" | Envía el check-in "¿Cómo va tu día?" (Todo bien / Tengo un problema / Necesito más tiempo). |

- **Resultados:** cuando quieras, escribe `.resultados` (o "cómo va la encuesta" / "avísame los resultados"). Si votan todos, te aviso solo, una vez. No hay resúmenes por tiempo: tú decides cuándo mirar.
- **Recordatorio:** a quien no votó se le recuerda **una sola vez**, a los 30 min (`"encuestas": {"recordar_min": 30}`; `0` lo apaga).
  Solo entre 07:00 y 21:00. Una encuesta de ayer ya no se recuerda.
- **Votos guardados:** se reconocen aunque Pati se reinicie. Se puede cambiar el voto (queda el último).
- **Problemas en cualquier encuesta:** si una opción dice *problema*, *incidencia*, *inconveniente* o *reportar* (ej.
  `opciones: todo bien, tengo un problema`), quien la marque recibe "Cuéntame qué pasa"; lo que escriba te llega
  **a ti** y queda en `.resultados` bajo esa opción (`↳ Mafer: se rompió la autoclave`). Puede escribir *no* para omitirlo; Pati
  espera el detalle 20 min. "Sin problemas" / "No tengo problemas" no cuentan.
- **Check-in:** quien marca *Tengo un problema* cuenta qué pasa y Pati se lo pasa al dueño; *Necesito más tiempo* también
  avisa. *Todo bien* no manda nada al dueño. Para que salga **solo** cada día (lunes a sábado), en `negocio.json`:
  `"checkin": {"hora": "17:30"}` (apagado si no se pone).
- Las encuestas son para **preguntas del equipo**; para actuar sobre una tarea (listo, problema, más tiempo) se usan los
  botones de cada tarea.

### Control del dueño sobre tareas ya enviadas

Sin IA (no gasta cupo). Todo lo que avisa a otras personas pide tu **SÍ** antes; la orden caduca a los 30 minutos.

| Dices | Pati hace |
|---|---|
| "cancela lo de Mafer" / "cancela lo del quirófano de Mafer" | Cancela la tarea y le avisa. Si hay varias, pregunta cuál (número o *todas*). |
| "pasa la tarea de Mafer a Ashly" | La reasigna; avisa a las dos y reinicia los avisos de plazo. |
| "cambia el plazo de Mafer a las 6pm" / "extiende el plazo de Mafer en 30 minutos" | Nuevo plazo y aviso a la persona. |
| "recuérdale a Mafer" | Le reenvía sus pendientes. |
| "avisa al equipo que mañana abrimos a las 8" | Envía el aviso a todo el equipo (menos a ti). |
| "historial de Mafer" | Línea de tiempo real de sus últimas tareas (no pide SÍ). |
| "deshacer" | Cancela las tareas del último envío (hasta 30 min después y si siguen pendientes). |

### Clientes nuevos y pasar con un humano

- Cualquiera que escriba al número recibe un menú (1 servicios, 2 agendar cita, 3 hablar con una persona).
- Cuando una persona del negocio escribe en un chat desde el celular, Pati se calla en ese chat hasta 5 minutos
  después de su último mensaje.

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
