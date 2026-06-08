"""
Módulo de persistencia SQLite para el Agente de Búsqueda de Empleo.
====================================================================
Dos tablas con responsabilidades separadas:

  urls_vistas   → todas las URLs scrapeadas (solo para deduplicar).
                  El agente la llena automáticamente cada sesión.

  postulaciones → solo las ofertas que el usuario eligió abrir.
                  Es el equivalente al Registro de Postulaciones (Excel).
                  Columnas actualizables a mano: cv_enviado_por, respuesta,
                  entrevista, prueba_tecnica, motivo_rechazo, nota.
"""

import sqlite3
import os
from datetime import datetime

DB_PATH = "postulaciones.db"


def init_db():
    """Crea la base de datos y las dos tablas si no existen."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Tabla liviana solo para deduplicar — guarda TODAS las URLs vistas
    c.execute("""
        CREATE TABLE IF NOT EXISTS urls_vistas (
            url   TEXT PRIMARY KEY,
            fecha TEXT NOT NULL
        )
    """)

    # Tabla principal — solo lo que el usuario eligió abrir/postular
    c.execute("""
        CREATE TABLE IF NOT EXISTS postulaciones (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha            TEXT NOT NULL,
            puesto_buscado   TEXT DEFAULT '',
            titulo           TEXT DEFAULT '',
            empresa          TEXT DEFAULT '',
            portal           TEXT DEFAULT '',
            url              TEXT UNIQUE NOT NULL,
            match_porcentaje INTEGER DEFAULT 0,
            motivo           TEXT DEFAULT '',
            -- columnas del seguimiento manual (equivalente al Excel)
            cv_enviado_por      TEXT DEFAULT '',
            obtuvo_respuesta    INTEGER DEFAULT 0,
            accedio_entrevista  INTEGER DEFAULT 0,
            prueba_tecnica      INTEGER DEFAULT 0,
            motivo_rechazo      TEXT DEFAULT '',
            nota                TEXT DEFAULT ''
        )
    """)

    conn.commit()
    conn.close()


def cargar_urls_vistas() -> set:
    """Devuelve todas las URLs ya vistas para que el scraper no las repita."""
    if not os.path.exists(DB_PATH):
        init_db()
        return set()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # Une ambas tablas para deduplicar contra todo lo conocido
    c.execute("SELECT url FROM urls_vistas UNION SELECT url FROM postulaciones")
    urls = {row[0] for row in c.fetchall()}
    conn.close()
    return urls


def registrar_urls_scrapeadas(urls: list[str]):
    """Guarda en urls_vistas todas las URLs encontradas en esta sesión."""
    if not urls:
        return
    fecha = datetime.now().strftime("%Y-%m-%d %H:%M")
    conn  = sqlite3.connect(DB_PATH)
    c     = conn.cursor()
    c.executemany(
        "INSERT OR IGNORE INTO urls_vistas (url, fecha) VALUES (?, ?)",
        [(u, fecha) for u in urls if u],
    )
    conn.commit()
    conn.close()


def guardar_postulacion(oferta: dict):
    """Guarda UNA oferta en postulaciones (las que el usuario eligió abrir)."""
    conn = sqlite3.connect(DB_PATH)
    c    = conn.cursor()
    try:
        c.execute("""
            INSERT OR IGNORE INTO postulaciones
            (fecha, puesto_buscado, titulo, empresa, portal,
             url, match_porcentaje, motivo)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            oferta.get("puesto_buscado", ""),
            oferta.get("titulo", ""),
            oferta.get("empresa", ""),
            oferta.get("portal", ""),
            oferta.get("url", ""),
            int(oferta.get("match_porcentaje", 0)),
            oferta.get("motivo", ""),
        ))
    except Exception:
        pass
    conn.commit()
    conn.close()


def contar_postulaciones() -> int:
    """Devuelve el total de postulaciones reales guardadas."""
    if not os.path.exists(DB_PATH):
        return 0
    conn = sqlite3.connect(DB_PATH)
    c    = conn.cursor()
    c.execute("SELECT COUNT(*) FROM postulaciones")
    total = c.fetchone()[0]
    conn.close()
    return total
