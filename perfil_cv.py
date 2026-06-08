"""
MÓDULO: Gestión de Perfiles de Candidatos
==========================================
Maneja múltiples perfiles desde la carpeta perfiles/.
Al iniciar, pregunta qué usuario busca empleo y carga su perfil JSON.

Flujo:
  1. Lista los perfiles disponibles en perfiles/*.json
  2. El usuario elige cuál usar
  3. Devuelve el perfil como dict para el análisis de Claude
"""

import os
import json
from pathlib import Path
from rich.console import Console

console = Console()

CARPETA_PERFILES = "perfiles"


def listar_perfiles() -> list[dict]:
    """Devuelve lista de {id, nombre, archivo} de los perfiles disponibles."""
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


def seleccionar_perfil() -> dict:
    """
    Muestra los perfiles disponibles y le pregunta al usuario cuál usar.
    Devuelve el dict del perfil elegido.
    """
    perfiles = listar_perfiles()

    if not perfiles:
        console.print("[yellow]No se encontraron perfiles en la carpeta 'perfiles/'.[/yellow]")
        console.print("[dim]Creá un archivo perfiles/perfil_tuNombre.json o usá el perfil por defecto.[/dim]")
        return _perfil_default()

    console.print("\n[bold]Para quién buscamos empleo?[/bold]")
    for i, p in enumerate(perfiles, 1):
        console.print(f"  [cyan]{i}[/cyan] — {p['nombre']} [dim]({p['rol'][:50]})[/dim]")

    opcion = console.input("[cyan]> [/cyan]").strip()

    if opcion.isdigit() and 1 <= int(opcion) <= len(perfiles):
        elegido = perfiles[int(opcion) - 1]
    else:
        elegido = perfiles[0]

    console.print(f"  Perfil cargado: [bold cyan]{elegido['nombre']}[/bold cyan]\n")
    return elegido["datos"]


def perfil_a_texto(perfil: dict) -> str:
    """Convierte el perfil JSON a texto para usar en el prompt de análisis."""
    techs   = ", ".join(perfil.get("tecnologias", []))
    tools   = ", ".join(perfil.get("herramientas", []))
    exp     = perfil.get("experiencia_laboral", [])
    exp_txt = " | ".join(
        f"{e.get('puesto')} en {e.get('empresa')}: {e.get('descripcion','')[:80]}"
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
        "nombre": "Candidato",
        "rol": "Desarrollador",
        "tecnologias": [],
        "herramientas": [],
        "seniority": "Junior",
        "experiencia_laboral": [],
        "educacion": []
    }
