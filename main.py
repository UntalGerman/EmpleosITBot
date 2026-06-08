"""
AGENTE DE BÚSQUEDA DE EMPLEO — Interfaz CLI v3.0
==================================================
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

from perfil_cv import seleccionar_perfil, perfil_a_texto
import db
import notifier

# ─── CONFIG ──────────────────────────────────────────────────────────────────

load_dotenv()
API_KEY      = os.getenv("ANTHROPIC_API_KEY")
MODELO       = "claude-haiku-4-5-20251001"
SESSIONS_DIR = "sessions"

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
        # Indeed: SPA con React. NO usar networkidle — Indeed hace requests
        # en background constantemente y nunca llega a ese estado → timeout.
        # Usar "load" + wait_extra para que el listado termine de renderizarse.
        # Selector más confiable: [data-jk] es el atributo job-key que Indeed
        # agrega a cada tarjeta, independientemente de los nombres de clase CSS.
        "nombre": "Indeed",
        "url_fn": lambda puesto, dias: (
            f"https://ar.indeed.com/jobs?q={quote_plus(puesto)}&l=Argentina&fromage={dias}"
        ),
        "base_url": "https://ar.indeed.com",
        "wait_until": "load",
        "wait_extra": 6,
        "selector_tarjetas": [
            "[data-jk]",
            ".job_seen_beacon",
            "[data-testid='slider_item']",
            "li[class*='css-']",
        ],
        "selector_titulo":  ["h2.jobTitle span", "h2[class*='jobTitle'] span", "h2 span", ".jobTitle"],
        "selector_empresa": ["[data-testid='company-name']", ".companyName", "[class*='company']"],
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
        "wait_extra": 4,
    },
    "6": {
        # Bumeran: SPA React (styled-components). Necesita networkidle y espera
        # extra para que el listado de ofertas termine de renderizarse.
        # URL alternativa más estable: /empleos.html con query params.
        "nombre": "Bumeran",
        "url_fn": lambda puesto, dias: (
            f"https://www.bumeran.com.ar/empleos.html"
            f"?palabras={quote_plus(puesto)}&publicacion={dias}"
        ),
        "base_url": "https://www.bumeran.com.ar",
        "wait_until": "networkidle",
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
    },
}

# ─── SESIONES ────────────────────────────────────────────────────────────────

def _session_file(nombre_portal: str) -> str:
    slug = nombre_portal.lower().replace(" ", "_")
    return os.path.join(SESSIONS_DIR, f"{slug}_session.json")

# ─── SCRAPER ─────────────────────────────────────────────────────────────────

async def scrape_portal(portal: dict, puesto: str, dias: int, urls_vistas: set) -> list[dict]:
    """Abre el portal con Playwright y extrae ofertas nuevas (deduplica por URL).

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

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        context_kwargs = {
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        }
        if os.path.exists(session_path):
            context_kwargs["storage_state"] = session_path

        context = await browser.new_context(**context_kwargs)
        page    = await context.new_page()

        pagina_cargada = False
        wait_until = portal.get("wait_until", "domcontentloaded")
        try:
            await page.goto(url, wait_until=wait_until, timeout=40000)
            await asyncio.sleep(portal.get("wait_extra", 3))
            pagina_cargada = True
        except Exception as e:
            console.print(f"  [red]Error al cargar {portal['nombre']}: {e}[/red]")
            await context.storage_state(path=session_path)
            await browser.close()
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

        await context.storage_state(path=session_path)
        await browser.close()

    return ofertas

# ─── ANÁLISIS CON CLAUDE ─────────────────────────────────────────────────────

def analizar_oferta(client: anthropic.Anthropic, texto_oferta: str, perfil_texto: str) -> dict:
    prompt = f"""
Analiza la siguiente oferta laboral y comparala con el Perfil del Candidato.
[Perfil del Candidato]
{perfil_texto}

[Oferta]
{texto_oferta}

Responde UNICAMENTE en formato JSON plano, sin texto adicional:
{{
    "match_porcentaje": 85,
    "postularse": true,
    "motivo": "Explicacion breve de una linea"
}}
"""
    try:
        respuesta = client.messages.create(
            model=MODELO,
            max_tokens=300,
            system="Sos un asistente que responde EXCLUSIVAMENTE con un objeto JSON plano y valido.",
            messages=[
                {"role": "user",      "content": prompt},
                {"role": "assistant", "content": "{"},
            ],
        )
        raw    = respuesta.content[0].text if respuesta.content else ""
        limpio = ("{" + raw).replace("```json", "").replace("```", "").strip()
        return json.loads(limpio)
    except Exception as e:
        return {"match_porcentaje": 0, "postularse": False, "motivo": f"Error: {e}"}

# ─── INTERFAZ CLI ─────────────────────────────────────────────────────────────

def mostrar_banner():
    total = db.contar_postulaciones()
    console.print(Panel.fit(
        "[bold cyan]AGENTE DE BUSQUEDA DE EMPLEO  v3.0[/bold cyan]\n"
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
        tabla.add_row(
            str(i),
            f"[{color}]{pct}%[/{color}]",
            r["titulo"][:30],
            r["empresa"][:22],
            r["portal"],
            f"[green]{icono}[/green]" if sugerido else f"[dim]{icono}[/dim]",
        )

    console.print()
    console.print(tabla)


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

async def ejecutar_busqueda(client: anthropic.Anthropic, perfil_texto: str):
    """Una ronda de búsqueda completa."""
    puesto, portales_elegidos, dias, umbral = pedir_configuracion()

    # La base de datos es la única fuente de verdad para deduplicar
    urls_vistas = db.cargar_urls_vistas()

    todas = []
    with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as prog:
        for key in portales_elegidos:
            portal = PORTALES[key]
            t = prog.add_task(f"Scrapeando {portal['nombre']}...", total=None)
            ofertas = await scrape_portal(portal, puesto, dias, urls_vistas)
            prog.update(
                t,
                description=f"[green]{portal['nombre']}: {len(ofertas)} nuevas[/green]",
                completed=True,
            )
            todas.extend(ofertas)

    if not todas:
        console.print("\n[yellow]No se encontraron ofertas nuevas. Proba con otro puesto o portal.[/yellow]")
        return

    console.print(f"\n[bold]Total: {len(todas)} ofertas nuevas. Analizando con Claude...[/bold]\n")

    resultados = []
    with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as prog:
        t = prog.add_task("Analizando ofertas...", total=len(todas))
        for oferta in todas:
            analisis = analizar_oferta(client, oferta["descripcion"], perfil_texto)
            resultados.append({**oferta, **analisis})
            prog.advance(t)

    resultados.sort(key=lambda x: x.get("match_porcentaje", 0), reverse=True)

    mostrar_resultados(resultados, umbral)

    # Registrar URLs vistas (deduplicación futura) y enviar alerta Telegram
    db.registrar_urls_scrapeadas([r["url"] for r in resultados])
    notifier.enviar_alerta(resultados, umbral)

    # El guardado en postulaciones ocurre dentro de abrir_mejores,
    # solo para las ofertas que el usuario elige abrir
    abrir_mejores(resultados, umbral)


async def main():
    mostrar_banner()

    if not API_KEY:
        console.print("[red bold]ERROR: No se encontro ANTHROPIC_API_KEY en el .env[/red bold]")
        return

    client = anthropic.Anthropic(api_key=API_KEY)

    perfil       = seleccionar_perfil()
    perfil_texto = perfil_a_texto(perfil)

    while True:
        await ejecutar_busqueda(client, perfil_texto)

        console.print("\n[bold]Hacer otra busqueda? (s/n)[/bold]")
        resp = console.input("[cyan]> [/cyan]").strip().lower()
        if not resp.startswith("s"):
            break
        console.print("\n" + "─" * 60 + "\n")

    console.print("\n[dim]Sesion finalizada. Hasta la proxima![/dim]\n")


if __name__ == "__main__":
    asyncio.run(main())
