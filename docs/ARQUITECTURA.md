# Arquitectura de PatiCare – fase piloto

Estado: vigente para el piloto. Lo marcado con **(por verificar)** no está comprobado.

**Objetivo del piloto:** que personas reales prueben la plataforma y nos digan qué les parece.
No es todavía una operación comercial.

## 1. Decisiones tomadas

| Tema | Decisión |
|---|---|
| Dónde corre | PC de Andrés, 24 h, **un proceso de Pati por negocio** (cada uno con su carpeta de datos y su número). |
| Número de WhatsApp | El número de WhatsApp Business del negocio. Pati se vincula como dispositivo (neonize); el dueño sigue usando la app en su celular. |
| Clientes nuevos | Pati atiende a **cualquier persona que escriba**: menú estándar (1 servicios, 2 agendar cita, 3 hablar con una persona). Lo que necesita una persona se deriva al dueño. |
| Pasar con un humano | Cuando alguien del negocio escribe en un chat, Pati **se calla en ese chat hasta 5 minutos después de su último mensaje** (cada mensaje nuevo extiende la pausa). Comprobado: neonize recibe los mensajes enviados desde el celular. |
| Dos líneas | Central (dueño y equipo) + corporativa (clientes). En la corporativa **todos son clientes**; en la central manda el rol. Los avisos internos salen por la central y lo dirigido a clientes por la corporativa, siempre dentro del programa (nunca por mensajes entre líneas). |
| Atención | El aviso de "quiero hablar con una persona" va a quien tenga el rol adicional `atencion`; puede ser el dueño en un negocio muy pequeño. Sin nadie con ese rol, va al dueño. |
| Dueño que trabaja | Con el rol adicional `trabajador`, el dueño puede asignarse tareas y responder "listo"/"problema". |
| Grupos de WhatsApp | Más adelante, **por la línea corporativa** (el encargado ya está en el grupo interno del negocio). Pati se activaría solo en grupos autorizados, solo respondería si la etiquetan y resumiría con "@Pati resumen". Primero se asegura lo básico. |
| Rubros | El bot es el mismo; cada negocio tiene un perfil (veterinaria, dentista, genérico) que cambia el vocabulario (mascota/paciente). Para un rubro nuevo se agrega una entrada en `negocio.py`. |
| Servicios y horario | Los pone el negocio en `negocio.json`. Si no hay, Pati **no inventa**: deriva a una persona. |
| IA | Gemini por REST, clave de Andrés, plan gratis, con modelos de respaldo (sección 6). |
| Datos | SQLite, una base por negocio. |
| Consentimiento | Los negocios aceptan participar en el piloto. |
| Quién confirma | **Todo lo que escribe a otras personas pide el SÍ del dueño** (tareas, avisos, encuestas, cancelaciones, recordatorios). Pati no inventa datos: si falta algo, pregunta. Cada trabajador ve y toca solo lo suyo. |
| Comandos del dueño | Mensajes que **empiezan con punto** (`.tarea`, `.encuesta`, `.estado`, `.resultados`, `.cancelar`...). Son reglas fijas, sin IA: siempre hacen lo mismo, no gastan cupo y reducen malentendidos. El texto normal sigue yendo a la IA. Ver sección 13. |
| Reconocer a las personas | WhatsApp a veces manda un **ID interno (LID)** sin el teléfono. Pati lo traduce leyendo (solo lectura) la tabla `whatsmeow_lid_map` de la sesión de WhatsApp y aprende el ID sola; ya no se copian IDs a mano. |
| Botones y encuestas | Los avisos al equipo llevan **botones de respuesta rápida** (✅ Listo · ⚠️ Problema · ⏳ Más tiempo) cuyo id lleva el número de la tarea, así nunca hay que preguntar "¿cuál?". Las **encuestas de WhatsApp** sirven para preguntarle algo al equipo y para ELEGIR (selectores). Probado con WhatsApp real: los toques llegan como `templateButtonReplyMessage`; neonize 0.5.2 falla al leer votos y se resolvió en `main._descifrar_voto`. Se apagan con `"botones": false`. |
| Lo que Pati no entiende | Un trabajador que escribe un pedido o una duda recibe "Se lo pasé a Andrés" y **el dueño recibe el mensaje** (tope de 5 por hora; los "ok"/emojis no se pasan). |

## 2. Entornos de prueba pedidos

| Entorno | Quiénes | Observación |
|---|---|---|
| Prueba interna | Andrés y su grupo | Número de pruebas. |
| Docencia | El profesor y su asistente | Pueden escribirle a un número de demostración desde sus celulares como dueño, trabajador o cliente. |
| Empresas nuevas | Dos empresas que se contactarán | Ver pregunta abierta 1. |

Cada entorno = una carpeta `datos/<nombre>/` + un número de WhatsApp + un proceso.

## 3. Diagrama

```
Celular del dueño (WhatsApp Business) ──┐
Clientes, trabajadores, dueño ──────────┴── número del negocio ── neonize ┐
                                                                         ▼
PC de Andrés (24 h)                                    main.py   (uno por negocio, PATI_DATA_DIR distinto)
  datos/prueba/   (pati.db, sesión, personas.json, negocio.json)           │
  datos/docencia/ (idem)                                                   ▼
  datos/<otro>/                                                logica.py ── ia.py ── Gemini (modelos en cadena)
  │   └─ comandos.py (órdenes del dueño con punto)
  db.py ── SQLite del negocio
```

Cada negocio vive en su carpeta: base, sesión de WhatsApp, personas y configuración. Un proceso nunca lee la carpeta de otro.

## 4. Privacidad (el número ve TODOS los chats del negocio)

- Pati recibe también chats personales y de proveedores del número. Cualquier desconocido que escribe recibe el menú; para **no atender a alguien** (familia, proveedores) se agrega su número a `ignorar` en `negocio.json`, o se pone `"atender_desconocidos": false`.
- Lo que escriben los **clientes no se guarda** (solo se reenvía al dueño cuando piden una persona o una cita). Del resto, Pati guarda mensajes únicamente de personas registradas (dueño y trabajadores), para tener contexto.
- Cada cliente recibe como máximo 10 respuestas por hora (evita bucles entre bots y spam).
- La sesión de WhatsApp da acceso total al chat: nunca se sube a git ni sale de la PC.
- Se avisa a las personas que hablan con un asistente virtual (el menú lo dice).

## 5. Operación 24 h en una PC

- Windows no debe suspender ni reiniciarse solo (plan de energía y actualizaciones).
- Arranque automático y reinicio si el proceso se cae (Programador de tareas). **Pendiente.**
- Zona horaria de Windows en Lima (la base usa la hora local).
- Registro de errores en archivo y aviso a Andrés si Pati se cae. **Pendiente.**
- Copia diaria de `datos/` fuera de la PC. **Pendiente.**
- Cada proceso de neonize consume memoria: medir cuántos caben en la PC (15 GB de RAM, 5 GB de disco libre).

## 6. Gemini sin saldo (plan gratis)

Límites vistos en AI Studio (por modelo, por proyecto, por día):

| Modelo | Peticiones/min | Peticiones/día |
|---|---|---|
| Gemini 3.1 Flash Lite | 15 | **500** |
| Gemini 3.5 Flash Lite | 15 | **500** |
| Gemini 3.5 / 3.6 / 3.7 / 3.8 Flash, 3 Flash, 2.5 Flash | 5 | solo 20 |
| Gemini 3.5 Transcribe (audio) | 3 | 25 |
| Gemma 4 (26B / 31B) | 30 | 14.400, pero **no respeta JSON** (probado): no sirve para Pati |

- Solo los mensajes del **dueño** llaman a la IA; trabajadores y clientes usan reglas fijas.
- Cadena de modelos: `gemini-3.1-flash-lite` → `gemini-3.5-flash-lite` → lo que diga `GEMINI_MODEL`. Si uno se queda sin cupo (429) o está saturado (503), se pasa al siguiente. Cupo total aproximado: 1.000 peticiones/día para **todos** los negocios juntos.
- **Contador propio:** cada petición a Gemini (también las que fallan o se reintentan) se anota en `uso_gemini.json`. La terminal avisa al llegar al 80 % del límite diario de un modelo y se consulta con `venv\Scripts\python.exe ia.py`. En AI Studio se contrasta con la cifra oficial.
- Los audios gastarán de ese mismo cupo (o del de Transcribe, solo 25/día).
- **(por verificar)** Cuándo se reinicia el cupo diario (suele ser a medianoche de California, es decir de madrugada en Lima).
- **(por verificar)** En el plan gratis Google puede usar los datos para mejorar sus productos; incluirlo en el consentimiento.

## 7. Qué se guarda y dónde

| Dato | Lugar |
|---|---|
| Personas, tareas, citas, recordatorios, historial, pausas | `datos/<negocio>/pati.db` |
| Sesión de WhatsApp | `datos/<negocio>/pati_sesion.sqlite3` |
| Dueño y trabajadores (teléfonos reales) | `datos/<negocio>/personas.json` |
| Nombre, rubro, servicios, horario, números a ignorar | `datos/<negocio>/negocio.json` |
| Clave de Gemini y modelos | `.env` (una sola) |

Si no se define `PATI_DATA_DIR`, se usa la carpeta del proyecto (el comportamiento de siempre).

## 8. Desde 3 negocios en producción (invertir)

- Servidor siempre encendido (no la PC de Andrés).
- Base única (Postgres/Supabase) con `negocio_id` en cada tabla y reglas de seguridad por fila; panel web donde cada dueño ve solo lo suyo.
- API oficial de WhatsApp Business (sin QR ni sesiones que custodiar), con tarifas de Meta por conversación **(por verificar)**.
- Gemini de pago con consumo medido por negocio.
- Google Calendar compartido por negocio (OAuth o cuenta de servicio).

## 9. Preguntas abiertas

1. **¿Cuántos números?** Se pidieron un entorno interno, uno de docencia y uno para dos empresas nuevas: son 3 o 4 números, más que los 2 que fijamos para el piloto. Propuesta: usar **números de demostración** (un chip por entorno) a los que los invitados les escriben desde sus celulares, y vincular el número real de una empresa solo cuando decida seguir.
2. **Quién habla con Pati:** dueño y trabajadores le escriben al número del negocio desde sus números personales. Hoy se registran a mano en `personas.json`; se puede agregar el alta por WhatsApp o por código de invitación.
3. **Qué medir** para validar la hipótesis (ver abajo).

## 10. Cómo se valida la hipótesis

"El dueño puede delegar y supervisar desde WhatsApp sin perseguir a cada trabajador."
Métricas que ya salen de la tabla de eventos: tareas delegadas por semana, % completadas, tiempo hasta completar, incidencias reportadas; más una entrevista corta al final.

## 11. Orden de desarrollo

1. ✅ Configuración por negocio, perfiles por rubro, clientes nuevos con menú, pausa por humano, respaldo de modelos.
1b. ✅ Código del modelo de dos líneas (central + corporativa), roles flexibles (`atencion`, `trabajador` adicional), reglas anti-bucle y mensajes viejos. **Falta la prueba en vivo con WhatsApp real.**
1c. ✅ Consulta del estado de tareas, avisos al completar, reportes periódicos y ayuda por rol (probado con Gemini real; falta verlo en vivo).
1d. ✅ **Iteración 4 (código y pruebas):** ficha del cliente (nombre y mascotas), Pati agenda citas sola con horarios libres, cancelar y reprogramar, consulta de agenda para dueño y atención. Falta la prueba en vivo.
1e. Varios negocios compartiendo la línea central (registro teléfono → negocio).
2. Resiliencia: reinicio automático, registro en archivo, aviso si se cae, copias de seguridad, contador de uso de Gemini.
3. Consultar citas, agenda diaria y enlace "Agregar al calendario".
4. Exportar a Excel/PDF.
5. Audios (transcripción).
6. Alta de personas por WhatsApp o código de invitación.
7. Google Calendar compartido.

## 12. Primera entrega: solo el agente de equipo

La primera entrega es **el agente que coordina al equipo** (línea central: dueño y trabajadores). Lo de clientes (línea
corporativa, menú, citas) queda construido pero fuera de esta entrega: para entregarlo apagado basta quitar `corporativa`
de `lineas` en `negocio.json`; el programa funciona solo con la central.

| # | Iteración | Qué aporta |
|---|---|---|
| 5 | ✅ Plazos y seguimiento automático | Recordatorio antes de vencer, aviso al vencer, aviso al dueño si no se completa, "más tiempo" |
| 6 | ✅ Control del dueño | Cancelar, reasignar y cambiar el plazo de una tarea (con confirmación); deshacer (~30 min); "recuérdale a Mafer"; anuncio al equipo; historial de una tarea |
| 6b | ✅ Botones, encuestas y comandos | Botones por tarea, encuestas al equipo (resultados, recordatorio único, check-in, "problema" con detalle), selectores para elegir (cancelar, completar, problema, más tiempo), comandos con punto, mensajes libres que se pasan al dueño |
| 7 | Operación confiable | Reinicio automático, registro de errores en archivo, aviso si Pati se cae o llega al 80 % del cupo de Gemini, copia diaria de la base |
| 8 | Rutina y métricas | Tareas recurrentes y listas de apertura/cierre, resumen semanal, exportar a Excel (cumplimiento, tiempos) para validar la hipótesis |
| 9 | Entrada natural | Notas de voz que se convierten en tareas; foto como evidencia al completar |
| 10 | Opcionales | Pati en el grupo del equipo (resumen y respuestas solo si la etiquetan); varios negocios compartiendo la línea central |

## 13. Cómo está construida la parte del equipo (estado actual)

**Módulos**

| Archivo | Para qué |
|---|---|
| `main.py` | Conecta con WhatsApp (neonize), envía con pausa "humana", reconoce botones, votos y personas, corre los trabajos programados. |
| `logica.py` | Toda la conversación sin WhatsApp: flujos del dueño, del trabajador y del cliente, seguimiento de plazos, encuestas, selectores. |
| `comandos.py` | Los comandos del dueño con punto; reutilizan la lógica de `logica.py`. |
| `ia.py` | Gemini (solo para entender lo que escribe el dueño en texto normal). |
| `db.py` / `negocio.py` / `agenda.py` | Datos (SQLite), configuración del negocio y horarios libres. |

**Qué usa IA y qué no.** Solo el texto normal del dueño pasa por Gemini. Trabajadores, clientes, botones, encuestas y comandos
usan reglas fijas: no gastan cupo y se pueden probar sin WhatsApp (622 pruebas con salida y reloj simulados).

**Comandos del dueño** (solo si el punto va al inicio; lo que avisa a otros pide SÍ):
`.tarea` · `.estado` · `.encuesta` · `.resultados` · `.checkin` · `.recordar` · `.resolver` · `.cancelar` (solo = lista para marcar;
`.cancelar .mafer` = solo las de ella; `.cancelar probar los botones` = esa tarea para todos) · `.reasignar` · `.plazo` · `.aviso` ·
`.historial` · `.equipo` · `.agenda` · `.deshacer` · `.ayuda`. Los nombres con punto (`.mafer`) valen en todos.

**Encuestas.** Se guardan en la base (`encuestas`, `encuesta_envios`, `votos`), así los votos se reconocen aunque Pati se reinicie.
Las órdenes del dueño que esperan su SÍ se pueden corregir ("sin Marco", "solo a Mafer"); lo que no es SÍ/NO no se pasa a la IA.
Quien marca una opción de problema cuenta qué pasa; le llega al dueño y queda en `.resultados`. Los **selectores** (tipo
`selector_*`) son encuestas para elegir, de una sola vez, con "✖️ Cancelar" para volver atrás; no cuentan como encuesta del equipo.

**Anti-bucle y seguridad.** Todo envío (incluidos botones y encuestas) registra su id en `_ids_pati`; si no, Pati confundiría su
propio mensaje con una persona escribiendo y pausaría el chat. Los toques solo actúan sobre tareas abiertas y de quien toca.
La bitácora `pruebas_equipo.log` anota qué llegó y cómo, **nunca el contenido** de los mensajes.

**Pendiente de la hoja de ruta:** operación confiable (registro de errores en archivo, aviso si se cae, copia diaria), métricas y
Excel, notas de voz, Pati en grupos, varios negocios en una línea.
