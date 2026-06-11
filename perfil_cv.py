"""
MODULO: Gestion de Perfiles de Candidatos
==========================================
Maneja multiples perfiles desde la carpeta perfiles/.
Al iniciar, pregunta que usuario busca empleo y carga su perfil JSON.
"""

import os
import json
import re
from pathlib import Path

import anthropic
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel

load_dotenv()

console          = Console()
CARPETA_PERFILES = "perfiles"


def listar_perfiles() -> list[dict]:
    carpeta = Path(CARPETA_PERFILES)
    if not carpeta.exists():
        return []
    perfiles = []
    for archivo in sorted(carpeta.glob("perfil_*.json")):
        try:
            with open(archivo, "r", encoding="utf-8") as f:
                datos = json.load(f)
            perfiles.append({
                "archivo": str(archivo),
                "nombre":  datos.get("nombre", archivo.stem),
                "rol":     datos.get("rol", ""),
                "datos":   datos,
            })
        except Exception:
            continue
    return perfiles


def seleccionar_perfil(api_key: str = "") -> dict:
    perfiles = listar_perfiles()

    console.print("\n[bold]Para quien buscamos empleo?[/bold]")
    for i, p in enumerate(perfiles, 1):
        console.print(f"  [cyan]{i}[/cyan] -- {p['nombre']} [dim]({p['rol'][:50]})[/dim]")

    nuevo_key = str(len(perfiles) + 1)
    console.print(f"  [cyan]{nuevo_key}[/cyan] -- [green]Crear nuevo perfil desde CV[/green]")

    opcion = console.input("[cyan]> [/cyan]").strip()

    if opcion == nuevo_key:
        key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
        if not key:
            console.print("[red]No se encontro ANTHROPIC_API_KEY.[/red]")
            return _perfil_default()
        nuevo = crear_perfil_desde_cv(key)
        return nuevo if nuevo else _perfil_default()

    if perfiles:
        if opcion.isdigit() and 1 <= int(opcion) <= len(perfiles):
            elegido = perfiles[int(opcion) - 1]
        else:
            elegido = perfiles[0]
        console.print(f"  Perfil cargado: [bold cyan]{elegido['nombre']}[/bold cyan]\n")
        return elegido["datos"]

    console.print("[yellow]No hay perfiles. Usando perfil por defecto.[/yellow]")
    return _perfil_default()


def cargar_perfil_auto(nombre: str | None = None) -> dict:
    """
    Carga un perfil SIN interacción — para modo --auto (n8n, tareas programadas).
    POR QUÉ: un orquestador no puede responder menús; necesita que el perfil
    se resuelva solo. Si se pasa nombre, busca coincidencia parcial;
    si no, usa el primer perfil disponible.
    """
    perfiles = listar_perfiles()
    if not perfiles:
        return _perfil_default()
    if nombre:
        for p in perfiles:
            if nombre.lower() in p["nombre"].lower():
                return p["datos"]
    return perfiles[0]["datos"]


def _leer_cv_terminal() -> str:
    console.print(Panel.fit(
        "[bold]Pega el texto de tu CV[/bold]\n"
        "[dim]Copia desde Word, PDF, LinkedIn o cualquier texto plano.\n"
        "Cuando termines escribe [bold white]FIN[/bold white] en una linea nueva y presiona Enter.[/dim]",
        border_style="cyan"
    ))
    lineas = []
    while True:
        try:
            linea = input()
        except EOFError:
            break
        if linea.strip().upper() == "FIN":
            break
        lineas.append(linea)
    return "\n".join(lineas).strip()


def _extraer_perfil_con_claude(cv_texto: str, nombre: str, api_key: str) -> dict:
    client = anthropic.Anthropic(api_key=api_key)
    prompt = f"""Analiza el siguiente CV y extrae la informacion en formato JSON.

NOMBRE DEL CANDIDATO: {nombre}

CV:
{cv_texto}

Devuelve UNICAMENTE un JSON valido con esta estructura, sin texto adicional:
{{
  "nombre": "{nombre}",
  "rol": "titulo profesional principal",
  "seniority": "Junior | Semi-Senior | Senior",
  "tecnologias": ["lenguajes", "frameworks"],
  "herramientas": ["git", "otras herramientas"],
  "experiencia_laboral": [
    {{"puesto": "nombre", "empresa": "empresa", "descripcion": "tareas breves"}}
  ],
  "educacion": [
    {{"titulo": "carrera o titulo", "institucion": "institucion"}}
  ]
}}
Si un campo no aparece en el CV usa lista vacia [] o string vacio "".
"""
    try:
        respuesta = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1000,
            system="Responde EXCLUSIVAMENTE con JSON valido, sin texto adicional.",
            messages=[
                {"role": "user",      "content": prompt},
                {"role": "assistant", "content": "{"},
            ],
        )
        raw    = respuesta.content[0].text if respuesta.content else ""
        limpio = ("{" + raw).replace("```json", "").replace("```", "").strip()
        return json.loads(limpio)
    except Exception as e:
        console.print(f"[red]Error al procesar el CV con Claude: {e}[/red]")
        return {}


def _guardar_perfil(perfil: dict) -> str:
    Path(CARPETA_PERFILES).mkdir(exist_ok=True)
    slug    = re.sub(r"[^a-z0-9]", "_", perfil["nombre"].lower()).strip("_")
    archivo = Path(CARPETA_PERFILES) / f"perfil_{slug}.json"
    with open(archivo, "w", encoding="utf-8") as f:
        json.dump(perfil, f, ensure_ascii=False, indent=2)
    return str(archivo)


def crear_perfil_desde_cv(api_key: str) -> dict | None:
    console.print("\n[bold cyan]--- Crear nuevo perfil ---[/bold cyan]")

    # Nombre: no avanza hasta que no este completo
    nombre = ""
    while not nombre:
        console.print("\n[bold]Nombre completo del candidato:[/bold]")
        nombre = console.input("[cyan]> [/cyan]").strip()
        if not nombre:
            console.print("[red]El nombre no puede estar vacio. Intentalo de nuevo.[/red]")

    # CV: no avanza hasta que no se pegue algo
    cv_texto = ""
    while not cv_texto:
        cv_texto = _leer_cv_terminal()
        if not cv_texto:
            console.print("[red]No se recibio texto del CV. Intentalo de nuevo.[/red]")

    console.print("\n[dim]Analizando CV con Claude...[/dim]")
    perfil = _extraer_perfil_con_claude(cv_texto, nombre, api_key)

    if not perfil:
        return None

    console.print(Panel(
        f"[bold]Nombre:[/bold]       {perfil.get('nombre', '')}\n"
        f"[bold]Rol:[/bold]          {perfil.get('rol', '')}\n"
        f"[bold]Seniority:[/bold]    {perfil.get('seniority', '')}\n"
        f"[bold]Tecnologias:[/bold]  {', '.join(perfil.get('tecnologias', []))}\n"
        f"[bold]Herramientas:[/bold] {', '.join(perfil.get('herramientas', []))}\n"
        f"[bold]Experiencia:[/bold]  {len(perfil.get('experiencia_laboral', []))} entrada(s)\n"
        f"[bold]Educacion:[/bold]    {len(perfil.get('educacion', []))} entrada(s)",
        title="[cyan]Perfil extraido[/cyan]",
        border_style="cyan",
    ))

    console.print("\n[bold]Guardar este perfil? (s/n)[/bold]")
    resp = console.input("[cyan]> [/cyan]").strip().lower()

    if resp.startswith("s"):
        ruta = _guardar_perfil(perfil)
        console.print(f"  [green]Perfil guardado en {ruta}[/green]\n")
        return perfil

    console.print("[yellow]Perfil descartado.[/yellow]")
    return None


def perfil_a_texto(perfil: dict) -> str:
    techs   = ", ".join(perfil.get("tecnologias", []))
    tools   = ", ".join(perfil.get("herramientas", []))
    exp     = perfil.get("experiencia_laboral", [])
    exp_txt = " | ".join(
        f"{e.get('puesto')} en {e.get('empresa')}: {e.get('descripcion', '')[:80]}"
        for e in exp[:2]
    ) if exp else ""

    return (
        f"Candidato: {perfil.get('nombre', '')}\n"
        f"Rol: {perfil.get('rol', '')}\n"
        f"Tecnologias: {techs}\n"
        f"Herramientas: {tools}\n"
        f"Seniority: {perfil.get('seniority', '')}\n"
        f"Experiencia: {exp_txt}\n"
        f"Educacion: {perfil.get('educacion', [{}])[0].get('titulo', '') if perfil.get('educacion') else ''}"
    )


def _perfil_default() -> dict:
    return {
        "nombre":              "Candidato",
        "rol":                 "Desarrollador",
        "seniority":           "Junior",
        "tecnologias":         [],
        "herramientas":        [],
        "experiencia_laboral": [],
        "educacion":           [],
    }
# v3.4: cargar_perfil_auto para modo no interactivo
