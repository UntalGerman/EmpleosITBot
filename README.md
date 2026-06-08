# 🤖 Agente de Búsqueda de Empleo

Agente activo en Python que scraping portales de empleo en tiempo real, analiza cada oferta contra tu perfil con **Claude Haiku (Anthropic)**, y te notifica por **Telegram** cuando encuentra un buen match.

---

## ¿Qué hace?

- Navega automáticamente 6 portales de empleo con **Playwright**
- Analiza cada oferta contra tu perfil técnico usando IA (Claude Haiku)
- Te asigna un porcentaje de match y te dice si conviene postularse
- Envía alertas a tu celular vía **Telegram** con las mejores ofertas
- Guarda un historial en **SQLite** — solo registra las ofertas que vos elegís abrir
- Deduplica automáticamente: nunca te muestra la misma oferta dos veces

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
- (Opcional) Un bot de Telegram para recibir alertas

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

**Windows** — doble click en `Ejecutar.bat`

**Terminal:**
```bash
python main.py
```

El agente te va a pedir:
1. El puesto que buscás (ej: `programador junior`, `react developer`)
2. En qué portal buscar (o todos)
3. Ofertas publicadas en los últimos N días
4. Match mínimo para considerar postularse (ej: `70`)

Al final podés elegir qué números abrir en el navegador — solo esas quedan registradas en tu base de datos de postulaciones.

---

## Estructura del proyecto

```
├── main.py          # Agente principal — scraping, análisis, CLI
├── db.py            # Persistencia SQLite (urls vistas + postulaciones)
├── notifier.py      # Alertas por Telegram
├── perfil_cv.py     # Gestión de perfiles del candidato
├── requirements.txt
├── Ejecutar.bat     # Lanzador para Windows
└── .env             # Variables de entorno (no subir a Git)
```

---

## Base de datos

El archivo `postulaciones.db` contiene dos tablas:

- **`urls_vistas`** — todas las URLs scrapeadas (para no repetir ofertas)
- **`postulaciones`** — solo las ofertas que elegiste abrir, con columnas de seguimiento: cv enviado, respuesta, entrevista, prueba técnica, motivo de rechazo, nota

Podés abrirla con [DB Browser for SQLite](https://sqlitebrowser.org/) para ver y editar tu historial visualmente.

---

## .gitignore recomendado

```
.env
postulaciones.db
historial.csv
sessions/
__pycache__/
*.pyc
perfiles/
perfil.json
```

---

## Stack

- [Playwright](https://playwright.dev/python/) — automatización del navegador
- [Anthropic Claude Haiku](https://www.anthropic.com/) — análisis de ofertas con IA
- [Rich](https://github.com/Textualize/rich) — interfaz de terminal
- SQLite — persistencia local sin servidor
- Telegram Bot API — notificaciones móviles
