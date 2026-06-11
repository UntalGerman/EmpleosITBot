# 🤖 Agente de Búsqueda de Empleo

Agente autónomo en Python que scrapea portales de empleo en tiempo real con **Playwright**, analiza cada oferta contra tu CV con **Claude Haiku (Anthropic)**, y te envía las que matchean directo a **WhatsApp** (vía n8n + Meta Cloud API) o **Telegram** — para que vos solo tengas que postularte.

```
Cron (n8n, cada 3hs) → Playwright (6 portales) → Claude (match % vs CV) → WhatsApp/Telegram
```

---

## ¿Qué hace?

- Navega automáticamente 6 portales de empleo con **Playwright** (en paralelo, un context por portal)
- Analiza cada oferta contra tu perfil técnico con IA en dos fases: triage barato de todas + análisis profundo de las mejores con la descripción real scrapeada
- Te asigna un porcentaje de match y decide si conviene postularse
- **Modo autónomo (`--auto`)**: orquestado por **n8n**, corre solo cada 3 horas y te manda cada oferta sugerida como mensaje de WhatsApp con plantilla aprobada
- Alertas por **Telegram** como canal de respaldo
- Deduplica con **SQLite**: nunca te muestra la misma oferta dos veces

---

## Portales soportados

| # | Portal | Tipo |
|---|--------|------|
| 1 | [Computrabajo](https://www.computrabajo.com.ar) | Empleo general AR |
| 2 | [EmpleosIT](https://www.empleosit.com.ar) | Tech especializado |
| 3 | [Indeed Argentina](https://ar.indeed.com) | Empleo general |
| 4 | [LinkedIn](https://www.linkedin.com/jobs) | Red profesional |
| 5 | [Workana](https://www.workana.com) | Freelance / remoto |
| 6 | [Bumeran](https://www.bumeran.com.ar) | Empleo general AR |

---

## Requisitos

- Python 3.10+
- Una API Key de [Anthropic](https://console.anthropic.com/)
- (Opcional) Un bot de Telegram para alertas
- (Opcional, para el modo autónomo) Node.js + [n8n](https://n8n.io) self-hosted y una app de [Meta for Developers](https://developers.facebook.com) con WhatsApp Cloud API

---

## Instalación

```bash
# 1. Clonar el repositorio
git clone https://github.com/tu-usuario/agente-empleo.git
cd agente-empleo

# 2. Instalar dependencias
pip install -r requirements.txt

# 3. Instalar los navegadores de Playwright
playwright install chromium
```

---

## Configuración

Creá un archivo `.env` en la raíz del proyecto:

```env
# Requerido
ANTHROPIC_API_KEY=sk-ant-...

# Opcional — alertas por Telegram
TELEGRAM_TOKEN=tu_token_de_botfather
TELEGRAM_CHAT_ID=tu_chat_id
```

### Cómo obtener el Chat ID de Telegram

1. Buscá `@BotFather` en Telegram → `/newbot` → copiá el token
2. Buscá `@userinfobot` → mandá cualquier mensaje → copiá tu `Id`
3. Buscá tu bot por su nombre y presioná **Start**

---

## Uso

### Modo interactivo

**Windows** — doble click en `Ejecutar.bat`, o por terminal:

```bash
python main.py
```

El agente te pide puesto, portal, antigüedad de las ofertas y match mínimo. Al final elegís qué ofertas abrir en el navegador — solo esas quedan registradas en tu base de postulaciones.

### Modo autónomo (`--auto`)

Pensado para orquestadores (n8n, Task Scheduler, cron). Sin menús: corre todos los portales y termina.

```bash
python main.py --auto --puesto "programador junior" --dias 1 --umbral 70 --perfil "German"
```

| Flag | Default | Descripción |
|------|---------|-------------|
| `--puesto` | `programador junior` | Término de búsqueda |
| `--dias` | `7` | Antigüedad máxima de las ofertas |
| `--umbral` | `70` | Match mínimo para sugerir postulación |
| `--perfil` | primer perfil | Nombre (parcial) del perfil a usar |
| `--json-out` | `resultados_auto.json` | Archivo de salida JSON |

**Contrato de salida** (diseñado para pipelines): los logs van a *stderr*; *stdout* emite una única línea JSON con los resultados. Exit code `0` = ok, `1` = error de configuración/API.

```json
{"timestamp": "...", "total_nuevas": 15, "sugeridas": 2, "ofertas": [{"titulo": "...", "match": 75, "sugerida": true, "url": "..."}]}
```

---

## Automatización con n8n + WhatsApp

El workflow `n8n_workflow_empleos.json` (importable desde n8n → Import from File) implementa:

```
Schedule Trigger (cron 0 0 9-21/3 * * *  →  9, 12, 15, 18 y 21 hs)
  → Execute Command (python main.py --auto ...)
  → Code (parsea el JSON de stdout, emite 1 item por oferta sugerida)
  → WhatsApp Send Template (1 mensaje por oferta, con match %, empresa, portal y link)
```

Puntos clave de la configuración:

- **n8n v2 bloquea Execute Command por defecto** — lanzar n8n con `NODES_EXCLUDE="[]"` (ver `iniciar_n8n.bat`, pensado para la carpeta de Inicio de Windows: `Win+R` → `shell:startup`)
- **WhatsApp Cloud API**: app tipo Business en Meta for Developers, número de prueba gratuito (hasta 5 destinatarios verificados), token permanente generado con un *usuario del sistema* en Business Manager
- **Plantilla aprobada** (`oferta_empleo`, 5 variables): permite enviar mensajes sin depender de la ventana de servicio de 24 hs
- El destinatario debe coincidir *exactamente* con el formato de la lista de permitidos de Meta (en AR: sin el `9` del prefijo móvil)

---

## Estructura del proyecto

```
├── main.py                    # Agente principal — scraping, análisis, CLI + modo --auto
├── db.py                      # Persistencia SQLite (urls vistas + postulaciones)
├── notifier.py                # Alertas por Telegram
├── perfil_cv.py               # Gestión de perfiles del candidato (interactivo y auto)
├── n8n_workflow_empleos.json  # Workflow de n8n listo para importar
├── Ejecutar.bat               # Lanzador del modo interactivo (Windows)
├── iniciar_n8n.bat            # Lanzador de n8n con NODES_EXCLUDE (para shell:startup)
├── requirements.txt
└── .env                       # Variables de entorno (no subir a Git)
```

---

## Base de datos

El archivo `postulaciones.db` contiene dos tablas:

- **`urls_vistas`** — todas las URLs scrapeadas (para no repetir ofertas)
- **`postulaciones`** — solo las ofertas que elegiste abrir, con columnas de seguimiento: cv enviado, respuesta, entrevista, prueba técnica, motivo de rechazo, nota

Podés abrirla con [DB Browser for SQLite](https://sqlitebrowser.org/) para ver y editar tu historial visualmente.

---

## Stack

- [Playwright](https://playwright.dev/python/) — automatización del navegador
- [Anthropic Claude Haiku](https://www.anthropic.com/) — análisis de ofertas con IA (lotes + prompt caching)
- [n8n](https://n8n.io) — orquestación self-hosted (cron, parseo, envío)
- [WhatsApp Business Cloud API](https://developers.facebook.com/docs/whatsapp/cloud-api) — notificaciones con plantilla
- [Rich](https://github.com/Textualize/rich) — interfaz de terminal
- SQLite — persistencia local sin servidor
- Telegram Bot API — canal de respaldo
