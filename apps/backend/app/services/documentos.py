"""Validación de los documentos PDF que sube el estudiante.

Dos comprobaciones deterministas (no interviene el LLM):

* el PDF no debe superar 5 MB;
* el texto del PDF debe contener el nombre o el documento del estudiante.

Si el PDF no tiene texto (por ejemplo, es un escaneo) no se rechaza: se marca
para revisión manual, porque no sería justo bloquear un documento legítimo.
"""

import io
import logging
import re
import unicodedata
from typing import Optional

from app.db.models import Usuario

logger = logging.getLogger(__name__)

MAX_PDF_BYTES = 5 * 1024 * 1024
MAX_PAGINAS_LECTURA = 12


def normalizar(texto: Optional[str]) -> str:
    """Minúsculas, sin tildes y con espacios simples."""
    limpio = unicodedata.normalize("NFKD", texto or "")
    limpio = "".join(c for c in limpio if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", limpio.lower()).strip()


def _solo_digitos(texto: Optional[str]) -> str:
    return re.sub(r"\D", "", texto or "")


def _tokens_nombre(texto: Optional[str]) -> list:
    stop = {"de", "del", "la", "las", "los", "y", "san", "santa", "da", "dos"}
    return [
        t
        for t in normalizar(texto).split()
        if len(t) >= 3 and t not in stop
    ]


def extraer_texto(pdf: bytes) -> str:
    """Extrae el texto de las primeras páginas del PDF, si es posible."""
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(pdf))
        partes = []
        for pagina in reader.pages[:MAX_PAGINAS_LECTURA]:
            partes.append(pagina.extract_text() or "")
        return "\n".join(partes).strip()
    except Exception as exc:  # PDF dañado o protegido
        logger.warning("No se pudo leer el texto del PDF: %s", exc)
        return ""


def _coincide_identidad(texto: str, user: Usuario) -> tuple:
    """Devuelve (coincide, motivo)."""
    plano = normalizar(texto)
    palabras = set(plano.split())

    documento = _solo_digitos(user.documento)
    if documento and len(documento) >= 5 and documento in _solo_digitos(texto):
        return True, "documento"

    apellidos = _tokens_nombre(user.apellidos)
    nombres = _tokens_nombre(user.nombres)

    apellidos_ok = bool(apellidos) and all(t in palabras for t in apellidos)
    nombres_ok = any(t in palabras for t in nombres) if nombres else True

    if apellidos_ok and nombres_ok:
        return True, "nombre"

    return False, ""


def verificar_documento(pdf: bytes, user: Usuario, tipo_documento: str) -> dict:
    """Verifica tamaño y pertenencia del documento.

    Retorna ``{"ok", "motivo", "identidad"}``. No usa el LLM.
    """
    if len(pdf) > MAX_PDF_BYTES:
        return {
            "ok": False,
            "motivo": "el archivo supera el máximo permitido de **5 MB**",
            "identidad": None,
        }

    if not pdf:
        return {
            "ok": False,
            "motivo": "el archivo llegó vacío",
            "identidad": None,
        }

    texto = extraer_texto(pdf)
    if len(normalizar(texto)) < 15:
        # Escaneo o PDF sin capa de texto: no se puede validar automáticamente.
        return {
            "ok": True,
            "motivo": "no se pudo leer texto del PDF; quedará para verificación manual",
            "identidad": "sin_texto",
        }

    coincide, identidad = _coincide_identidad(texto, user)
    if coincide:
        return {"ok": True, "motivo": "", "identidad": identidad}

    documento = _solo_digitos(user.documento)
    numeros = [
        n for n in re.findall(r"\b\d{7,12}\b", texto) if n != documento
    ]
    if numeros:
        detalle = (
            "el PDF contiene un número de documento que no coincide con el tuyo"
        )
    else:
        detalle = "no encontré tu nombre ni tu número de documento en el PDF"

    return {
        "ok": False,
        "motivo": (
            f"{detalle}. Puede que hayas adjuntado el archivo de otra persona "
            "o el documento equivocado"
        ),
        "identidad": None,
    }
