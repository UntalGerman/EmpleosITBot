"""
AGENTE DE BÚSQUEDA DE EMPLEO — Interfaz CLI v3.3
==================================================
Novedades v3.3 (calidad de match — triage + análisis profundo):
  - El análisis ahora tiene DOS fases: triage barato de todas las ofertas
    (título + empresa) y análisis profundo de las TOP_DETALLE mejores con
    la descripción REAL scrapeada de la página de cada oferta
  - scrape_detalles: páginas de detalle en paralelo (máx 5 simultáneas),
    selectores por portal + fallback genérico, sesión reutilizada por sitio
  - En la tabla, "*" marca los matches calculados con descripción completa

Novedades v3.2 (velocidad máxima):
  - Esperas fijas eliminadas: wait_for_selector espera la primera tarjeta
    y resuelve apenas aparece (antes: hasta 8s fijos por portal)
  - domcontentloaded para todos los portales (networkidle/load eran más lentos)
  - Timeout de carga 40s → 25s: un portal colgado ya no frena toda la ronda
  - Lotes de análisis EN PARALELO (Semaphore(4)): antes secuenciales

Novedades v3.1 (optimización de rendimiento y costo):
  - Scraping EN PARALELO: un solo Chromium compartido, un context por portal,
    asyncio.gather → tiempo total = portal más lento, no la suma de todos
  - Bloqueo de imágenes/fuentes/media → carga 50-70% más rápida
  - Análisis POR LOTES: 10 ofertas por llamada a la API (antes 1 por oferta)
    + prompt caching del perfil + asyncio.to_thread para no bloquear el loop
  - try/finally en scraper: la sesión siempre se guarda, el context siempre cierra

Novedades v3.0:
  - Persistencia real con SQLite (db.py): reemplaza historial.csv
    → deduplicación por URL entre sesiones y entre distintos términos de búsqueda
    → mismas columnas del Registro de Postulaciones (Excel)
  - Alertas por Telegram (notifier.py): si configurás TELEGRAM_TOKEN y
    TELEGRAM_CHAT_ID en el .env, el agente te manda un mensaje al celular
    con cada oferta que supera el umbral de match
  - Portales actualizados: GetOnBrd y ZonaJobs reemplazados por Workana y Bumeran
    → Workana: plataforma freelance/remoto, gran volumen tech para Argentina
    → Bumeran: 2do portal más grande de Argentina, HTML server-rendered estable
  - Detección de selectores rotos: si un portal devuelve 0 resultados muestra
    un aviso específico para que sepas qué portal revisar

Ejecutar con:
    python main.py
"""

import os
import sys
import argparse
import asyncio
import json
import webbrowser
from datetime import datetime
from urllib.parse import quote_plus

import anthropic
from dotenv import load_dotenv
from playwright.async_api import async_playwright

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn

from perfil_cv import seleccionar_perfil, perfil_a_texto, cargar_perfil_auto
import db
import notifier

# ─── CONFIG ──────────────────────────────────────────────────────────────────

VERSION      = "3.4"  # única fuente de verdad de la versión (banner la usa)
load_dotenv()
API_KEY      = os.getenv("ANTHROPIC_API_KEY")
MODELO       = "claude-haiku-4-5-20251001"
SESSIONS_DIR = "sessions"

# Un solo lugar para el user agent (antes estaba duplicado por función)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Análisis en dos fases: triage barato sobre TODAS las ofertas (solo título y
# empresa) y análisis profundo SOLO sobre las top-N, con la descripción real
# scrapeada de la página de detalle. Así el tiempo/costo caro se invierte
# únicamente donde puede cambiar una decisión.
TOP_DETALLE         = 10  # cuántas ofertas top reciben análisis profundo
MAX_PAGINAS_DETALLE = 5   # páginas de detalle abiertas a la vez

os.makedirs(SESSIONS_DIR, exist_ok=True)
db.init_db()

console = Console()

PORTALES = {
    "1": {
        "nombre": "Computrabajo",
        "url_fn": lambda puesto, dias: (
            f"https://www.computrabajo.com.ar/empleos-de-{quote_plus(puesto.replace(' ', '-'))}?pubdate={dias}"
        ),
        "base_url": "https://www.computrabajo.com.ar",
        "selector_tarjetas": ["article.box_offer", "[data-qa='offer-list-item']"],
        "selector_titulo":   ["h2", "h3", "[data-qa='offer-title']"],
        "selector_empresa":  ["p.fs16", "[data-qa='company-name']", ".company"],
        "selector_descripcion": ["[class*='box_detail']", "[data-qa='offer-description']"],
    },
    "2": {
        "nombre": "EmpleosIT",
        "modo": "direct_links",
        "url_fn": lambda puesto, dias: (
            f"https://www.empleosit.com.ar/search-results-jobs/?action=search"
            f"&listing_type%5Bequal%5D=Job&keywords%5Ball_words%5D={quote_plus(puesto)}"
        ),
        "base_url": "https://www.empleosit.com.ar",
        "selector_links":   "a[href*='display-job']",
        "selector_empresa": [".listing-company", ".company", ".org", "td.company"],
    },
    "3": {
        # Indeed: SPA con React. wait_extra define el timeout máximo de espera
        # de tarjetas (_esperar_contenido resuelve apenas aparecen).
        # Selector más confiable: [data-jk] es el atributo job-key que Indeed
        # agrega a cada tarjeta, independientemente de los nombres de clase CSS.
        "nombre": "Indeed",
        "url_fn": lambda puesto, dias: (
            f"https://ar.indeed.com/jobs?q={quote_plus(puesto)}&l=Argentina&fromage={dias}"
        ),
        "base_url": "https://ar.indeed.com",
        "wait_extra": 6,
        "selector_tarjetas": [
            "[data-jk]",
            ".job_seen_beacon",
            "[data-testid='slider_item']",
            "li[class*='css-']",
        ],
        "selector_titulo":  ["h2.jobTitle span", "h2[class*='jobTitle'] span", "h2 span", ".jobTitle"],
        "selector_empresa": ["[data-testid='company-name']", ".companyName", "[class*='company']"],
        "selector_descripcion": ["#jobDescriptionText", "[class*='jobsearch-JobComponent-description']"],
    },
    "4": {
        "nombre": "LinkedIn",
        "url_fn": lambda puesto, dias: (
            f"https://www.linkedin.com/jobs/search/?keywords={quote_plus(puesto)}"
            f"&location=Argentina&f_TPR=r{dias * 86400}"
        ),
        "base_url": "https://www.linkedin.com",
        "selector_tarjetas": [
            ".jobs-search__results-list li",
            ".job-search-card",
            "[data-entity-urn]",
        ],
        "selector_titulo":  ["h3.base-search-card__title", "h3", ".base-search-card__title"],
        "selector_empresa": ["h4.base-search-card__subtitle", "h4", ".base-search-card__subtitle"],
        "selector_descripcion": [".show-more-less-html__markup", ".description__text", "[class*='description']"],
    },
    "5": {
        # Workana: plataforma freelance/remoto con gran volumen tech en Argentina.
        # Es una SPA parcialmente React → necesita wait_extra para renderizar.
        "nombre": "Workana",
        "url_fn": lambda puesto, dias: (
            f"https://www.workana.com/jobs?language=es&area=it-programming"
            f"&search={quote_plus(puesto)}&country_code=AR"
        ),
        "base_url": "https://www.workana.com",
        "selector_tarjetas": [
            ".project-item",
            "[id^='project-']",
            "article.project",
            "[class*='project-item']",
        ],
        "selector_titulo":  ["h2", ".title a", ".project-title", "a.title"],
        "selector_empresa": [".company-name", ".client-name", ".client-info"],
        "selector_descripcion": [".project-details", "[class*='expander']", "[class*='description']"],
        "wait_extra": 4,
    },
    "6": {
        # Bumeran: SPA React (styled-components) — el listado tarda en montar,
        # por eso wait_extra alto: es el timeout MÁXIMO de _esperar_contenido,
        # que resuelve apenas aparece la primera tarjeta.
        # URL alternativa más estable: /empleos.html con query params.
        "nombre": "Bumeran",
        "url_fn": lambda puesto, dias: (
            f"https://www.bumeran.com.ar/empleos.html"
            f"?palabras={quote_plus(puesto)}&publicacion={dias}"
        ),
        "base_url": "https://www.bumeran.com.ar",
        "wait_extra": 8,
        "selector_tarjetas": [
            "[class*='Posting']",
            "[class*='posting']",
            "[data-qa='postings-list-item']",
            "article",
            "[class*='CardJob']",
            "[class*='card-job']",
        ],
        "selector_titulo":  [
            "[data-qa='posting-name']",
            "[class*='PostingTitle']",
            "[class*='posting-title']",
            "h2", "h3",
        ],
        "selector_empresa": [
            "[data-qa='company-name']",
            "[class*='PostingCompany']",
            "[class*='company']",
            ".empresa",
        ],
        "selector_descripcion": ["[class*='Description']", "[data-qa='descripcion']"],
    },
}

# Fallback genérico cuando el portal no define selector o el suyo falla
SELECTORES_DESCRIPCION_DEFAULT = ["article", "main", "[class*='description']", "[class*='detalle']"]

# ─── SESIONES ────────────────────────────────────────────────────────────────

def _session_file(nombre_portal: str) -> str:
    slug = nombre_portal.lower().replace(" ", "_")
    return os.path.join(SESSIONS_DIR, f"{slug}_session.json")

# ─── SCRAPER ─────────────────────────────────────────────────────────────────

# Tipos de recurso que NO necesitamos para leer el DOM.
# Bloquearlos reduce 50-70% del tráfico y acelera la carga.
# OJO: no bloqueamos "stylesheet" ni "script" — las SPAs (Indeed, Bumeran)
# los necesitan para renderizar el listado de ofertas.
RECURSOS_BLOQUEADOS = {"image", "font", "media"}


async def _bloquear_recursos(route):
    if route.request.resource_type in RECURSOS_BLOQUEADOS:
        await route.abort()
    else:
        await route.continue_()


async def _esperar_contenido(page, portal: dict):
    """Espera EXACTAMENTE hasta que aparece la primera tarjeta de oferta.

    POR QUÉ: un sleep fijo de N segundos cuesta N segundos SIEMPRE, aunque la
    página haya renderizado en 1. wait_for_selector resuelve apenas el elemento
    existe en el DOM: caso típico ~1s. Solo el caso "sin resultados" paga el
    timeout completo — y ahí sí vale la pena esperar, para no dar falsos ceros.
    La coma en CSS significa "cualquiera de estos selectores".
    """
    selectores = portal.get("selector_tarjetas") or [portal.get("selector_links", "a")]
    combinado  = ", ".join(selectores)
    timeout_ms = max(portal.get("wait_extra", 3), 6) * 1000
    try:
        await page.wait_for_selector(combinado, state="attached", timeout=timeout_ms)
        # micro-pausa: la primera tarjeta ya está; esto deja montar el resto
        await asyncio.sleep(0.5)
    except Exception:
        pass  # sin tarjetas: el caller distingue "sin resultados" de selector roto


async def scrape_portal(browser, portal: dict, puesto: str, dias: int, urls_vistas: set) -> list[dict]:
    """Extrae ofertas nuevas de un portal (deduplica por URL).

    Recibe el `browser` ya lanzado y crea SOLO un context propio:
    - El navegador es el recurso caro (~1-2s de arranque); los contexts son
      livianos y aislados entre sí (cookies separadas), así cada portal
      conserva su sesión sin pisar a los demás.
    - Esto permite scrapear todos los portales EN PARALELO con asyncio.gather
      compartiendo un único Chromium.

    Persistencia de sesión:
    - Si existe sessions/<portal>_session.json, lo carga al crear el contexto.
    - Al cerrar siempre guarda el estado actualizado.

    Detección de selectores rotos:
    - Si el portal cargó pero no encontró tarjetas, muestra aviso específico
      para que el usuario sepa qué portal revisar.
    """
    url          = portal["url_fn"](puesto, dias)
    ofertas      = []
    session_path = _session_file(portal["nombre"])

    context_kwargs = {"user_agent": USER_AGENT}
    if os.path.exists(session_path):
        context_kwargs["storage_state"] = session_path

    context = await browser.new_context(**context_kwargs)
    page    = await context.new_page()
    await page.route("**/*", _bloquear_recursos)

    # try/finally: pase lo que pase (error de carga, selector roto, excepción
    # inesperada) la sesión SIEMPRE se guarda y el context SIEMPRE se cierra.
    # Antes este cleanup estaba duplicado en cada camino de salida.
    try:
        pagina_cargada = False
        try:
            # domcontentloaded para TODOS los portales: es el evento más
            # temprano confiable. Antes Bumeran usaba networkidle (que en SPAs
            # puede no llegar nunca) e Indeed "load", ambos + sleep fijo.
            # Ahora _esperar_contenido espera la primera tarjeta y ni 1s más.
            await page.goto(url, wait_until="domcontentloaded", timeout=25000)
            await _esperar_contenido(page, portal)
            pagina_cargada = True
        except Exception as e:
            console.print(f"  [red]Error al cargar {portal['nombre']}: {e}[/red]")
            return []

        # ── Modo direct_links (ej: EmpleosIT) ────────────────────────────────
        if portal.get("modo") == "direct_links":
            links  = await page.query_selector_all(portal.get("selector_links", "a[href*='display-job']"))
            if pagina_cargada and not links:
                # Verificar si hay algún contenido en la página para distinguir
                # "sin resultados para este término" de "selector roto"
                body_text = await page.inner_text("body") if pagina_cargada else ""
                if len(body_text.strip()) < 200:
                    console.print(
                        f"  [yellow]⚠ {portal['nombre']}: página casi vacía — posible bloqueo o selector roto.[/yellow]"
                    )
                # Si hay contenido pero 0 links, probablemente no hay empleos para ese término
            vistos = set()
            for link in links[:20]:
                try:
                    titulo = (await link.inner_text()).strip()
                    if not titulo or titulo in vistos:
                        continue
                    vistos.add(titulo)
                    href = await link.get_attribute("href") or ""
                    url_oferta = (
                        f"{portal['base_url']}{href}" if href.startswith("/")
                        else href if href.startswith("http")
                        else url
                    )
                    if url_oferta in urls_vistas:
                        continue

                    empresa = "Empresa no especificada"
                    parent  = await link.evaluate_handle("el => el.closest('tr, li, div, article')")
                    if parent:
                        for sel in portal.get("selector_empresa", []):
                            try:
                                el = await parent.query_selector(sel)
                                if el:
                                    empresa = (await el.inner_text()).strip()
                                    break
                            except Exception:
                                continue

                    ofertas.append({
                        "titulo":         titulo,
                        "empresa":        empresa,
                        "url":            url_oferta,
                        "portal":         portal["nombre"],
                        "descripcion":    f"{titulo} en {empresa}. Portal: {portal['nombre']}.",
                        "puesto_buscado": puesto,
                    })
                except Exception:
                    continue

        else:
            # ── Modo estándar: tarjetas ───────────────────────────────────────
            tarjetas = []
            selector_usado = None
            for sel in portal.get("selector_tarjetas", []):
                tarjetas = await page.query_selector_all(sel)
                if tarjetas:
                    selector_usado = sel
                    break

            # Detección de selector roto vs sin resultados para el término
            if pagina_cargada and not tarjetas:
                body_text = await page.inner_text("body") if pagina_cargada else ""
                if len(body_text.strip()) < 200:
                    console.print(
                        f"  [yellow]⚠ {portal['nombre']}: página casi vacía — posible bloqueo o selector roto.[/yellow]"
                    )
                # Si hay contenido pero 0 tarjetas, probablemente no hay empleos
                # para ese término de búsqueda en este portal (comportamiento normal)

            for tarjeta in tarjetas[:15]:
                try:
                    titulo = "Sin titulo"
                    for sel in portal.get("selector_titulo", []):
                        el = await tarjeta.query_selector(sel)
                        if el:
                            titulo = (await el.inner_text()).strip()
                            break

                    empresa = "Empresa no especificada"
                    for sel in portal.get("selector_empresa", []):
                        el = await tarjeta.query_selector(sel)
                        if el:
                            empresa = (await el.inner_text()).strip()
                            break

                    link_el = await tarjeta.query_selector("a")
                    href    = await link_el.get_attribute("href") if link_el else ""
                    if href and href.startswith("/"):
                        url_oferta = f"{portal['base_url']}{href}"
                    elif href and href.startswith("http"):
                        url_oferta = href
                    else:
                        url_oferta = url

                    if url_oferta in urls_vistas:
                        continue

                    ofertas.append({
                        "titulo":         titulo,
                        "empresa":        empresa,
                        "url":            url_oferta,
                        "portal":         portal["nombre"],
                        "descripcion":    f"{titulo} en {empresa}. Portal: {portal['nombre']}.",
                        "puesto_buscado": puesto,
                    })
                except Exception:
                    continue

    finally:
        await context.storage_state(path=session_path)
        await context.close()

    return ofertas


async def _extraer_descripcion(context, oferta: dict, selectores: list[str]):
    """Abre la página de UNA oferta y extrae el texto de la descripción.

    Estrategia de selectores: primero los específicos del portal, después los
    genéricos. Un match se considera válido solo si tiene >150 caracteres —
    eso filtra falsos positivos (un div "description" vacío o un breadcrumb).
    Último recurso: el body entero. Todo se recorta a ~1800 caracteres: más
    que suficiente para evaluar requisitos, y mantiene el costo de API acotado.

    Muta `oferta` in place (descripcion + detalle_ok) en vez de devolver:
    los dicts son compartidos con la lista de resultados del caller.
    """
    page = await context.new_page()
    try:
        await page.route("**/*", _bloquear_recursos)
        await page.goto(oferta["url"], wait_until="domcontentloaded", timeout=20000)
        texto = ""
        for sel in selectores:
            try:
                el = await page.query_selector(sel)
                if el:
                    candidato = (await el.inner_text()).strip()
                    if len(candidato) > 150:
                        texto = candidato
                        break
            except Exception:
                continue
        if not texto:
            try:
                texto = (await page.inner_text("body")).strip()
            except Exception:
                texto = ""
        texto = " ".join(texto.split())[:1800]
        if len(texto) > 150:
            oferta["descripcion"] = (
                f"{oferta['titulo']} en {oferta['empresa']} ({oferta['portal']}). "
                f"Descripcion: {texto}"
            )
            oferta["detalle_ok"] = True
    except Exception:
        pass  # sin detalle: la oferta conserva su descripción corta del triage
    finally:
        await page.close()


async def scrape_detalles(browser, ofertas: list[dict]):
    """Scrapea las páginas de detalle de varias ofertas EN PARALELO.

    Agrupa por portal para reusar un context por sitio (carga la sesión
    guardada — clave en LinkedIn). El Semaphore limita las páginas abiertas
    simultáneas: cada page consume RAM y demasiadas a la vez parecen un bot.
    """
    por_portal: dict[str, list[dict]] = {}
    for o in ofertas:
        por_portal.setdefault(o["portal"], []).append(o)

    cfg_por_nombre = {v["nombre"]: v for v in PORTALES.values()}
    sem            = asyncio.Semaphore(MAX_PAGINAS_DETALLE)

    async def _con_limite(context, oferta, selectores):
        async with sem:
            await _extraer_descripcion(context, oferta, selectores)

    contexts, tareas = [], []
    for nombre_portal, grupo in por_portal.items():
        cfg          = cfg_por_nombre.get(nombre_portal, {})
        session_path = _session_file(nombre_portal)
        kwargs       = {"user_agent": USER_AGENT}
        if os.path.exists(session_path):
            kwargs["storage_state"] = session_path
        context = await browser.new_context(**kwargs)
        contexts.append(context)
        selectores = cfg.get("selector_descripcion", []) + SELECTORES_DESCRIPCION_DEFAULT
        tareas += [_con_limite(context, o, selectores) for o in grupo]

    await asyncio.gather(*tareas)
    for c in contexts:
        await c.close()

# ─── ANÁLISIS CON CLAUDE ─────────────────────────────────────────────────────

TAM_LOTE = 10  # ofertas por llamada a la API


def _system_con_cache(perfil_texto: str) -> list[dict]:
    """Bloque system con el perfil + cache_control.

    PROMPT CACHING: el perfil es idéntico en todas las llamadas de la sesión.
    cache_control le dice a la API que lo guarde: la primera llamada lo escribe
    en caché y las siguientes lo leen a ~10% del costo.
    NOTA: la API exige un mínimo de tokens para cachear (~2048 en Haiku); con
    el perfil resumido actual quizás no se active todavía, pero queda listo
    para cuando enriquezcamos el perfil/descripciones (paso 2 del plan).
    """
    return [{
        "type": "text",
        "text": (
            "Sos un analista de RRHH tech. Comparas ofertas laborales contra el "
            "perfil de un candidato y respondes EXCLUSIVAMENTE con JSON valido.\n\n"
            f"[Perfil del Candidato]\n{perfil_texto}"
        ),
        "cache_control": {"type": "ephemeral"},
    }]


def analizar_lote(client: anthropic.Anthropic, ofertas: list[dict], perfil_texto: str) -> list[dict]:
    """Analiza VARIAS ofertas en UNA llamada a la API.

    POR QUÉ por lotes: antes era 1 llamada por oferta, repitiendo el perfil
    completo cada vez. Con 60 ofertas eran 60 llamadas; ahora son 6.
    Menos latencia total, menos tokens repetidos, menos riesgo de rate limit.

    Devuelve una lista de análisis EN EL MISMO ORDEN que `ofertas`.
    Cada oferta lleva un índice "n" y se mapea por ese campo, no por posición:
    si el modelo omite o desordena una, las demás no se corrompen.
    """
    listado = "\n".join(f"{i}. {o['descripcion']}" for i, o in enumerate(ofertas, 1))
    prompt = f"""Analiza estas {len(ofertas)} ofertas laborales contra el Perfil del Candidato.

[Ofertas]
{listado}

Responde UNICAMENTE con un array JSON con UN objeto por oferta, mismo orden:
[
  {{"n": 1, "match_porcentaje": 85, "postularse": true, "motivo": "una linea"}}
]"""
    default = {"match_porcentaje": 0, "postularse": False, "motivo": "Sin analisis"}
    try:
        respuesta = client.messages.create(
            model=MODELO,
            max_tokens=120 * len(ofertas),  # ~120 tokens por análisis
            system=_system_con_cache(perfil_texto),
            messages=[
                {"role": "user",      "content": prompt},
                {"role": "assistant", "content": "["},  # prefill: fuerza array JSON
            ],
        )
        raw      = respuesta.content[0].text if respuesta.content else ""
        limpio   = ("[" + raw).replace("```json", "").replace("```", "").strip()
        analisis = json.loads(limpio)
    except Exception as e:
        console.print(f"  [red]Error analizando lote: {e}[/red]")
        return [{**default, "motivo": f"Error: {e}"} for _ in ofertas]

    por_n = {a.get("n"): a for a in analisis if isinstance(a, dict)}
    return [
        {k: v for k, v in por_n.get(i, default).items() if k != "n"}
        for i in range(1, len(ofertas) + 1)
    ]


def validar_api_key(client: anthropic.Anthropic) -> bool:
    """Llamada mínima (1 token) para verificar la key ANTES de scrapear.

    POR QUÉ: sin esto, una key inválida recién explota DESPUÉS de 20-30s de
    scraping, con un 401 por cada lote y una tabla llena de 0% engañosos.
    Fallar rápido con un mensaje claro ahorra tiempo y confusión.
    """
    try:
        client.messages.create(
            model=MODELO,
            max_tokens=1,
            messages=[{"role": "user", "content": "ping"}],
        )
        return True
    except anthropic.AuthenticationError:
        return False
    except Exception:
        return True  # otros errores (red, etc.) no son culpa de la key

# ─── INTERFAZ CLI ─────────────────────────────────────────────────────────────

def mostrar_banner():
    total = db.contar_postulaciones()
    console.print(Panel.fit(
        f"[bold cyan]AGENTE DE BUSQUEDA DE EMPLEO  v{VERSION}[/bold cyan]\n"
        f"[dim]Playwright + Claude Haiku | SQLite | Telegram | 6 portales | "
        f"{total} postulacion(es) registrada(s)[/dim]",
        border_style="cyan"
    ))


def pedir_configuracion() -> tuple[str, list[str], int, int]:
    console.print("\n[bold]Que puesto buscas?[/bold] [dim](ej: programador junior, soporte IT)[/dim]")
    puesto = console.input("[cyan]> [/cyan]").strip() or "programador junior"

    console.print("\n[bold]En que portal?[/bold]")
    for k, v in PORTALES.items():
        console.print(f"  [cyan]{k}[/cyan] — {v['nombre']}")
    todos_key = str(len(PORTALES) + 1)
    console.print(f"  [cyan]{todos_key}[/cyan] — Todos")
    opcion = console.input("[cyan]> [/cyan]").strip()

    portales_elegidos = [opcion] if opcion in PORTALES else list(PORTALES.keys())

    console.print("\n[bold]Publicadas en los ultimos cuantos dias?[/bold] [dim](1 / 7 / 30)[/dim]")
    dias_input = console.input("[cyan]> [/cyan]").strip()
    dias = int(dias_input) if dias_input.isdigit() else 7

    console.print("\n[bold]Match minimo para postularse?[/bold] [dim](ej: 50 / 70 / 90)[/dim]")
    umbral_input = console.input("[cyan]> [/cyan]").strip()
    umbral = int(umbral_input) if umbral_input.isdigit() else 70

    return puesto, portales_elegidos, dias, umbral


def mostrar_resultados(resultados: list[dict], umbral: int):
    if not resultados:
        console.print("\n[yellow]No se encontraron ofertas nuevas.[/yellow]")
        return

    tabla = Table(
        title=f"Resultados (umbral de match: {umbral}%)",
        border_style="cyan",
        show_lines=True,
    )
    tabla.add_column("#",        style="dim",   width=4,  justify="center")
    tabla.add_column("Match",    style="bold",  width=7,  justify="center")
    tabla.add_column("Puesto",   style="white", width=30)
    tabla.add_column("Empresa",  style="dim",   width=22)
    tabla.add_column("Portal",   style="dim",   width=13)
    tabla.add_column("Sugerido", justify="center", width=9)

    for i, r in enumerate(resultados, 1):
        pct      = r.get("match_porcentaje", 0)
        sugerido = r.get("postularse", False) and pct >= umbral
        color    = "green" if pct >= umbral else "yellow" if pct >= 50 else "red"
        icono    = "SI" if sugerido else "no"
        # "*" = match calculado con la descripción REAL de la oferta
        marca    = "*" if r.get("profundo") else ""
        tabla.add_row(
            str(i),
            f"[{color}]{pct}%{marca}[/{color}]",
            r["titulo"][:30],
            r["empresa"][:22],
            r["portal"],
            f"[green]{icono}[/green]" if sugerido else f"[dim]{icono}[/dim]",
        )

    console.print()
    console.print(tabla)
    console.print("[dim]* = match calculado con la descripción completa de la oferta[/dim]")


def abrir_mejores(resultados: list[dict], umbral: int):
    if not resultados:
        return

    sugeridas = [i + 1 for i, r in enumerate(resultados)
                 if r.get("postularse") and r.get("match_porcentaje", 0) >= umbral]

    if sugeridas:
        console.print(
            f"\n[bold green]Sugeridas por el agente: {', '.join(str(n) for n in sugeridas)}[/bold green]"
        )
    else:
        console.print(
            f"\n[yellow]Ninguna supero el {umbral}% de match — pero podés abrir cualquiera igual.[/yellow]"
        )

    console.print("[bold]Que numeros queres abrir?[/bold] [dim](ej: 1,3,5 | 'todas' | Enter para saltar)[/dim]")
    resp = console.input("[cyan]> [/cyan]").strip().lower()

    if not resp:
        return

    if resp == "todas":
        indices = list(range(1, len(resultados) + 1))
    else:
        indices = []
        for parte in resp.replace(" ", "").split(","):
            if parte.isdigit():
                n = int(parte)
                if 1 <= n <= len(resultados):
                    indices.append(n)

    for n in indices:
        r = resultados[n - 1]
        console.print(f"  Abriendo [{n}]: [cyan]{r['titulo']}[/cyan] — {r['empresa']}")
        webbrowser.open(r["url"])
        db.guardar_postulacion(r)

    if indices:
        console.print(
            f"\n[dim]Guardado en postulaciones: {len(indices)} oferta(s). "
            f"Total acumulado: {db.contar_postulaciones()}[/dim]"
        )

# ─── MAIN ────────────────────────────────────────────────────────────────────

async def ejecutar_busqueda(client: anthropic.Anthropic, perfil_texto: str,
                            config: dict | None = None) -> list[dict]:
    """
    Una ronda de búsqueda completa.
    config: si se pasa (modo --auto), evita todo input interactivo.
            Claves: puesto, portales, dias, umbral.
    Devuelve la lista de resultados analizados (vacía si no hubo ofertas).
    """
    if config:
        puesto           = config["puesto"]
        portales_elegidos = config["portales"]
        dias             = config["dias"]
        umbral           = config["umbral"]
    else:
        puesto, portales_elegidos, dias, umbral = pedir_configuracion()

    # La base de datos es la única fuente de verdad para deduplicar
    urls_vistas = db.cargar_urls_vistas()

    # ── FASE A: scraping de listados EN PARALELO ─────────────────────────
    # Un solo Chromium compartido; cada portal en su context aislado.
    # start()/stop() en vez de "async with": el navegador queda vivo para la
    # FASE C (detalles) y el finally garantiza su cierre pase lo que pase.
    nombres = [PORTALES[k]["nombre"] for k in portales_elegidos]
    todas   = []
    p       = await async_playwright().start()
    browser = await p.chromium.launch(headless=True)
    try:
        with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as prog:
            t = prog.add_task(f"Scrapeando en paralelo: {', '.join(nombres)}...", total=None)
            resultados_portales = await asyncio.gather(
                *(scrape_portal(browser, PORTALES[k], puesto, dias, urls_vistas)
                  for k in portales_elegidos),
                return_exceptions=True,  # un portal caído no aborta a los demás
            )
            prog.update(t, description="[green]Listados scrapeados[/green]", completed=True)

        for nombre, res in zip(nombres, resultados_portales):
            if isinstance(res, Exception):
                console.print(f"  [red]{nombre}: falló ({res})[/red]")
            else:
                console.print(f"  [green]{nombre}: {len(res)} nuevas[/green]")
                todas.extend(res)

        if not todas:
            console.print("\n[yellow]No se encontraron ofertas nuevas. Proba con otro puesto o portal.[/yellow]")
            return []

        console.print(f"\n[bold]Total: {len(todas)} ofertas nuevas. Triage con Claude...[/bold]\n")

        # ── FASE B: triage EN PARALELO (solo título + empresa) ───────────
        # Lotes concurrentes con Semaphore(4): paralelismo sin chocar rate
        # limits. to_thread porque el SDK es síncrono — cada llamada corre
        # en su hilo sin congelar el event loop.
        lotes      = [todas[i:i + TAM_LOTE] for i in range(0, len(todas), TAM_LOTE)]
        sem        = asyncio.Semaphore(4)
        resultados = []
        with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as prog:
            t = prog.add_task("Triage de ofertas...", total=len(todas))

            async def _analizar(lote: list[dict]) -> list[dict]:
                async with sem:
                    analisis = await asyncio.to_thread(analizar_lote, client, lote, perfil_texto)
                prog.advance(t, len(lote))
                return analisis

            analisis_lotes = await asyncio.gather(*(_analizar(l) for l in lotes))

        for lote, analisis in zip(lotes, analisis_lotes):
            resultados.extend({**o, **a} for o, a in zip(lote, analisis))

        resultados.sort(key=lambda x: x.get("match_porcentaje", 0), reverse=True)

        # ── FASE C: descripciones reales de las top-N ────────────────────
        # POR QUÉ solo las top: el detalle cuesta ~2-4s por oferta; hacerlo
        # para todas sería lento. El triage barato ordena; el scraping caro
        # se invierte solo donde puede cambiar una decisión de postulación.
        top = resultados[:TOP_DETALLE]
        with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as prog:
            t = prog.add_task(f"Leyendo descripciones de las top {len(top)}...", total=None)
            await scrape_detalles(browser, top)
            leidas = sum(1 for r in top if r.get("detalle_ok"))
            prog.update(t, description=f"[green]Descripciones leídas: {leidas}/{len(top)}[/green]", completed=True)
    finally:
        await browser.close()
        await p.stop()

    # ── FASE D: re-análisis profundo (ya sin navegador) ──────────────────
    # Ahora Claude ve los requisitos REALES de cada oferta, no solo el título.
    # Acá el prompt es grande → el prompt caching del perfil empieza a pagar.
    con_detalle = [r for r in top if r.get("detalle_ok")]
    if con_detalle:
        with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as prog:
            t = prog.add_task("Análisis profundo de las mejores...", total=len(con_detalle))
            for i in range(0, len(con_detalle), TAM_LOTE):
                lote     = con_detalle[i:i + TAM_LOTE]
                analisis = await asyncio.to_thread(analizar_lote, client, lote, perfil_texto)
                # update() in place: estos dicts SON los mismos objetos de
                # `resultados`, así el ranking final refleja el análisis nuevo.
                # Si el lote falló (API caída, 401), NO pisamos el triage:
                # mejor conservar el match anterior que mostrar un 0% falso.
                for o, a in zip(lote, analisis):
                    if str(a.get("motivo", "")).startswith("Error"):
                        continue
                    o.update(a)
                    o["profundo"] = True
                prog.advance(t, len(lote))
        resultados.sort(key=lambda x: x.get("match_porcentaje", 0), reverse=True)

    mostrar_resultados(resultados, umbral)

    # Registrar URLs vistas (deduplicación futura) y enviar alerta Telegram
    db.registrar_urls_scrapeadas([r["url"] for r in resultados])
    notifier.enviar_alerta(resultados, umbral)

    # El guardado en postulaciones ocurre dentro de abrir_mejores,
    # solo para las ofertas que el usuario elige abrir.
    # En modo --auto no hay usuario presente: se omite.
    if config is None:
        abrir_mejores(resultados, umbral)

    return resultados


async def main():
    mostrar_banner()

    if not API_KEY:
        console.print("[red bold]ERROR: No se encontro ANTHROPIC_API_KEY en el .env[/red bold]")
        return

    client = anthropic.Anthropic(api_key=API_KEY)

    if not validar_api_key(client):
        console.print(
            "[red bold]ERROR: la API key del .env fue rechazada (401).[/red bold]\n"
            "[yellow]Revisa el archivo .env: debe tener una linea asi:[/yellow]\n"
            "  ANTHROPIC_API_KEY=sk-ant-api03-...\n"
            "[dim]Genera tu key en https://console.anthropic.com → API Keys.\n"
            "Sin comillas, sin espacios, sin corchetes.[/dim]"
        )
        return

    perfil       = seleccionar_perfil(api_key=API_KEY)
    perfil_texto = perfil_a_texto(perfil)

    while True:
        await ejecutar_busqueda(client, perfil_texto)

        console.print("\n[bold]Hacer otra busqueda? (s/n)[/bold]")
        resp = console.input("[cyan]> [/cyan]").strip().lower()
        if not resp.startswith("s"):
            break
        console.print("\n" + "─" * 60 + "\n")

    console.print("\n[dim]Sesion finalizada. Hasta la proxima![/dim]\n")



# ─── MODO AUTO (n8n / tareas programadas) ────────────────────────────────────
# CONTRATO DE SALIDA: los logs van a STDERR y el resultado JSON va a STDOUT.
# POR QUÉ: el nodo Execute Command de n8n captura stdout; si mezcláramos las
# tablas de Rich con el JSON, n8n no podría parsearlo. Separar canales es el
# patrón estándar Unix para que un proceso sea componible en pipelines.
# EXIT CODES: 0 = ok (incluso con 0 ofertas), 1 = error de configuración/API.

def parsear_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=f"Agente de Búsqueda de Empleo v{VERSION}")
    parser.add_argument("--auto",     action="store_true",
                        help="Modo no interactivo: corre todos los portales y emite JSON por stdout")
    parser.add_argument("--puesto",   default="programador junior", help="Término de búsqueda")
    parser.add_argument("--dias",     type=int, default=7,  help="Antigüedad máxima de las ofertas")
    parser.add_argument("--umbral",   type=int, default=70, help="Match mínimo para sugerir postulación")
    parser.add_argument("--perfil",   default=None, help="Nombre (parcial) del perfil a usar; default: el primero")
    parser.add_argument("--json-out", default="resultados_auto.json",
                        help="Archivo donde guardar el resultado JSON (además de stdout)")
    return parser.parse_args()


async def main_auto(args: argparse.Namespace) -> int:
    global console
    console = Console(stderr=True)  # todo el output visual a stderr; stdout queda puro

    if not API_KEY:
        console.print("[red bold]ERROR: No se encontro ANTHROPIC_API_KEY en el .env[/red bold]")
        return 1

    client = anthropic.Anthropic(api_key=API_KEY)
    if not validar_api_key(client):
        console.print("[red bold]ERROR: API key rechazada (401). Revisa el .env[/red bold]")
        return 1

    perfil       = cargar_perfil_auto(args.perfil)
    perfil_texto = perfil_a_texto(perfil)
    console.print(f"[dim]Modo auto | perfil: {perfil.get('nombre')} | puesto: {args.puesto} "
                  f"| dias: {args.dias} | umbral: {args.umbral}%[/dim]")

    config = {
        "puesto":   args.puesto,
        "portales": list(PORTALES.keys()),  # en auto siempre se barren todos
        "dias":     args.dias,
        "umbral":   args.umbral,
    }
    resultados = await ejecutar_busqueda(client, perfil_texto, config)

    sugeridas = [r for r in resultados
                 if r.get("postularse") and r.get("match_porcentaje", 0) >= args.umbral]
    salida = {
        "timestamp":    datetime.now().isoformat(timespec="seconds"),
        "perfil":       perfil.get("nombre", ""),
        "puesto":       args.puesto,
        "dias":         args.dias,
        "umbral":       args.umbral,
        "total_nuevas": len(resultados),
        "sugeridas":    len(sugeridas),
        "ofertas": [
            {
                "titulo":   r.get("titulo", ""),
                "empresa":  r.get("empresa", ""),
                "portal":   r.get("portal", ""),
                "url":      r.get("url", ""),
                "match":    r.get("match_porcentaje", 0),
                "sugerida": bool(r.get("postularse") and r.get("match_porcentaje", 0) >= args.umbral),
                "profundo": bool(r.get("profundo")),
                "motivo":   r.get("motivo", ""),
            }
            for r in resultados[:30]
        ],
    }

    with open(args.json_out, "w", encoding="utf-8") as f:
        json.dump(salida, f, ensure_ascii=False, indent=2)

    print(json.dumps(salida, ensure_ascii=False))  # ÚNICO print a stdout: lo que parsea n8n
    return 0


if __name__ == "__main__":
    args = parsear_args()
    if args.auto:
        sys.exit(asyncio.run(main_auto(args)))
    asyncio.run(main())
# v3.4: modo --auto para n8n / tareas programadas
