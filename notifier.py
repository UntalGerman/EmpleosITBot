"""
Módulo de alertas por Telegram para el Agente de Búsqueda de Empleo.
=====================================================================
Configuración requerida en el .env:
    TELEGRAM_TOKEN   = el token de tu bot (BotFather te lo da)
    TELEGRAM_CHAT_ID = tu chat ID personal (lo obtenés con @userinfobot)

Si las variables no están configuradas, el módulo no hace nada
y el agente sigue funcionando sin alertas.

Cómo crear el bot (una sola vez):
  1. Buscá @BotFather en Telegram
  2. Enviá /newbot y seguí los pasos
  3. Copiá el token que te da y pegalo en .env
  4. Buscá @userinfobot en Telegram, enviá cualquier mensaje
  5. Copiá el "Id" que te devuelve y pegalo como TELEGRAM_CHAT_ID
"""

import os
import sys
import urllib.request
import urllib.parse
import json
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


def _configurado() -> bool:
    return bool(TELEGRAM_TOKEN and TELEGRAM_CHAT_ID)


def _enviar_mensaje(texto: str):
    """Envía un mensaje de texto al chat configurado."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = json.dumps({
        "chat_id":                  TELEGRAM_CHAT_ID,
        "text":                     texto,
        "parse_mode":               "Markdown",
        "disable_web_page_preview": True,
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        # A stderr: en modo --auto, stdout está reservado para el JSON que parsea n8n
        print(f"[Telegram] Error al enviar alerta: {e}", file=sys.stderr)
        return False


def enviar_alerta(resultados: list[dict], umbral: int):
    """
    Envía alerta con las ofertas que superan el umbral de match.
    Si Telegram no está configurado, no hace nada.
    """
    if not _configurado():
        return

    buenas = [
        r for r in resultados
        if r.get("match_porcentaje", 0) >= umbral and r.get("postularse")
    ]
    if not buenas:
        return

    lineas = [f"🔍 *{len(buenas)} oferta(s) con match ≥{umbral}%*\n"]
    for r in buenas[:5]:  # máximo 5 para no saturar el chat
        pct     = r.get("match_porcentaje", 0)
        titulo  = r.get("titulo", "Sin título")[:40]
        empresa = r.get("empresa", "?")[:25]
        portal  = r.get("portal", "?")
        url     = r.get("url", "")
        lineas.append(
            f"✅ *{pct}%* — {titulo}\n"
            f"   🏢 {empresa} | _{portal}_\n"
            f"   🔗 {url}\n"
        )

    if len(buenas) > 5:
        lineas.append(f"_...y {len(buenas) - 5} oferta(s) más en la terminal._")

    _enviar_mensaje("\n".join(lineas))
