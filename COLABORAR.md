# Cómo colaborar en PatiCare (guía para quien NO sabe programar)

PatiCare es un asistente de WhatsApp para veterinarias. Tú vas a modificarlo **pidiéndole a una IA**
(Claude Code, ChatGPT, Cursor, etc.) que haga los cambios en tu computadora. Esta guía te dice qué hacer, paso a paso.

Repositorio: https://github.com/salcedo-dev/paticare
Responsable del proyecto: Andrés (aprueba todos los cambios).

---

## Paso 0 – Conseguir acceso (una sola vez)

El repositorio es **privado**: si no estás invitado, GitHub te mostrará "404".

1. Crea una cuenta gratis en https://github.com (si ya tienes, úsala).
2. Envíale tu **nombre de usuario de GitHub** a Andrés.
3. Andrés te invita. Te llegará un correo de GitHub: abre el enlace y pulsa **Accept invitation**.
   (Si no llega, entra a https://github.com/salcedo-dev/paticare/invitations estando con tu sesión iniciada.)
4. Ya puedes ver el proyecto en https://github.com/salcedo-dev/paticare

## Paso 1 – Instalar lo necesario (una sola vez, Windows)

1. **Git**: https://git-scm.com/download/win (instalar con todo por defecto).
2. **Python 3.11**: https://www.python.org/downloads/release/python-3119/ (marca "Add python.exe to PATH").
3. Una **IA que edite archivos**. Recomendado: Claude Code (https://claude.com/claude-code). Sirve cualquier otra que pueda
   abrir una carpeta de tu computadora (Cursor, Copilot, etc.).

## Paso 2 – Descargar el proyecto (una sola vez)

Abre **PowerShell** y escribe (cambia la carpeta si quieres):

```powershell
cd $HOME\Desktop
git clone https://github.com/salcedo-dev/paticare.git
cd paticare
```

La primera vez se abrirá una ventana del navegador para iniciar sesión en GitHub: hazlo con tu cuenta invitada.

## Paso 3 – Preparar el proyecto (una sola vez)

```powershell
py -3.11 -m venv venv
venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
copy personas.example.json personas.json
```

Luego:
- Abre `.env` con el Bloc de notas y pega **tu propia** clave gratis de Gemini en `GEMINI_API_KEY=`
  (se saca en https://aistudio.google.com/apikey). **No uses la de otra persona.**
- No necesitas conectar WhatsApp para colaborar: las pruebas funcionan sin él.

Comprueba que todo está bien:

```powershell
venv\Scripts\python.exe -m unittest
```

Debe terminar con `OK`. Si dice `FAILED`, avísale a Andrés antes de tocar nada.

## Paso 4 – Cada vez que vayas a trabajar

1. Abre PowerShell dentro de la carpeta `paticare` y trae lo último:
   ```powershell
   git checkout main
   git pull
   ```
2. Crea **tu propia rama** (como una copia de trabajo; así no pisas a nadie). Ponle un nombre que diga qué harás:
   ```powershell
   git checkout -b mejora/recordatorio-de-vacunas
   ```
3. Abre tu IA **dentro de esa carpeta** (con Claude Code: escribe `claude` en PowerShell, en la carpeta `paticare`).
4. Pega el **prompt de la sección de abajo** y luego dile qué quieres cambiar, con tus palabras.
5. Cuando la IA termine, pídele que corra las pruebas y te muestre qué archivos cambió.
6. Sube tu rama (la IA puede hacerlo, o tú):
   ```powershell
   git add -A
   git commit -m "Describe en una frase qué cambiaste"
   git push -u origin mejora/recordatorio-de-vacunas
   ```
7. En https://github.com/salcedo-dev/paticare aparecerá un botón verde **Compare & pull request**.
   Púlsalo, escribe qué hiciste y pulsa **Create pull request**. Avísale a Andrés: él revisa y lo une al proyecto.

> Nunca trabajes directamente en `main` y nunca uses `git push --force`.

## Reglas de oro

1. **Nunca subir secretos.** `.env`, `personas.json` y los archivos `.db` / `.sqlite3` ya están ignorados por Git; no los fuerces.
   Si por error subes una clave, avisa a Andrés de inmediato para regenerarla.
2. **Pati nunca inventa datos** (horas, nombres de mascotas, direcciones, teléfonos). Si falta algo, pregunta.
3. **Toda acción que envíe mensajes a otras personas requiere confirmación del dueño.**
4. **Cada trabajador solo ve sus propias tareas.**
5. Mensajes de Pati: en español, amables y cortos.
6. No cambiar la tecnología (Python 3.11, neonize, Gemini por REST sin SDK de Google, SQLite, APScheduler).
7. Cada cambio de comportamiento lleva su prueba en `test_flujos.py` o `test_ia.py`, y todas deben pasar antes de subir.
8. Cambios pequeños: un pull request = una idea.

---

## Prompt para pegarle a tu IA (cópialo completo)

```
Vas a ayudarme a modificar el proyecto PatiCare, que está en esta carpeta. No sé programar: explícame todo en español
sencillo, sin jerga, y avísame antes de cada paso importante.

QUÉ ES: Pati es un asistente de WhatsApp para veterinarias pequeñas de Perú. El dueño le escribe instrucciones
desordenadas y Pati las convierte en tareas para los trabajadores, con confirmación SÍ/NO. Los trabajadores responden
✅ (completar) o ⚠️ (incidencia). También hay recordatorios y citas para clientes, y un resumen diario al dueño.

ANTES DE CAMBIAR NADA:
1. Lee README.md, COLABORAR.md y los archivos logica.py, ia.py, db.py y main.py.
2. Corre las pruebas: venv\Scripts\python.exe -m unittest   (deben dar OK).
3. Confirma que estoy en una rama distinta de main (git branch). Si estoy en main, dime que cree una rama primero.

CÓMO TRABAJAR:
- Haz cambios pequeños sobre el código existente; no reescribas el proyecto ni agregues frameworks.
- La lógica de conversación está en logica.py y se prueba sin WhatsApp. No hace falta conectar WhatsApp para desarrollar.
- Por cada cambio de comportamiento agrega o actualiza una prueba con aserciones reales en test_flujos.py o test_ia.py
  (usan un LLM falso; no gastan cupo de Gemini).
- Al terminar corre todas las pruebas y muéstrame el resultado real. No digas que algo funciona si no lo probaste.
- Si algo solo se puede probar con WhatsApp real, dime exactamente qué debo probar yo y cómo.

REGLAS QUE NO SE ROMPEN:
- Pati NUNCA inventa datos (hora, nombre de mascota, dirección, teléfono, instrucciones). Si falta algo, pregunta.
- Toda acción del dueño que envíe mensajes a otras personas requiere su confirmación explícita.
- Cada trabajador solo ve y modifica sus propias tareas.
- Se ignoran grupos, mensajes propios y números que no estén registrados.
- Mensajes de Pati en español, amables y cortos.
- Stack fijo: Python 3.11, neonize, Gemini por REST con httpx (SIN el SDK de Google), SQLite, APScheduler, zona America/Lima.
- NUNCA imprimas, copies ni subas el contenido de .env, personas.json, *.db ni *.sqlite3. No pongas claves en el código.
- Nunca uses git push --force, ni trabajes sobre main, ni hagas commit/push sin que yo te lo pida.

CUANDO TERMINES: dime qué archivos cambiaste y por qué, los resultados de las pruebas, y los comandos para hacer
git add / commit / push de mi rama y abrir el pull request en GitHub.

LO QUE QUIERO CAMBIAR: (aquí escribo mi idea con mis palabras)
```

---

## Si algo sale mal

| Problema | Qué hacer |
|---|---|
| GitHub muestra "404" al abrir el repositorio | Aún no aceptaste la invitación (Paso 0) o no iniciaste sesión. |
| `git push` pide usuario o falla por permisos | Inicia sesión en la ventana del navegador que se abre; si no, avísale a Andrés para confirmar tu invitación. |
| Las pruebas fallan antes de que toques nada | Ejecuta `git pull` en `main` y repite; si sigue, avisa a Andrés. |
| `git pull` dice que hay conflictos | No sigas: copia el mensaje y pídele a tu IA que te explique, o avisa a Andrés. |
| Se me subió algo que no debía | Avisa a Andrés **de inmediato**; si era una clave, se regenera. |
| La IA propone cambiar de tecnología o reescribir todo | Dile que no: las reglas piden cambios pequeños con el stack actual. |

Para entender el diseño completo y cómo ejecutar el bot con WhatsApp real, lee `README.md`.
