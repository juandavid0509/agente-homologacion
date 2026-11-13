"""Motor de reglas normativas.

Lee `conocimiento_normativo` y responde SIEMPRE con datos de la base:
- requisitos por tipo de homologación (interna / externa / general),
- nota mínima y demás condiciones numéricas,
- documentos exigidos,
- evaluación de si el estudiante cumple o no una condición.
"""

import logging
import re
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.db.models import ConocimientoNormativo

logger = logging.getLogger(__name__)

TIPOS_GENERALES = ("TODAS", "", None)


# ---------------------------------------------------------------------------
# Consulta de reglas
# ---------------------------------------------------------------------------
def reglas_activas(db: Session) -> List[ConocimientoNormativo]:
    return (
        db.query(ConocimientoNormativo)
        .filter(ConocimientoNormativo.estado == "ACTIVO")
        .order_by(ConocimientoNormativo.id.asc())
        .all()
    )


def reglas_para_tipo(db: Session, tipo: Optional[str]) -> List[ConocimientoNormativo]:
    """Reglas aplicables a un tipo (incluye las marcadas como TODAS)."""
    reglas = reglas_activas(db)
    if not tipo:
        return [r for r in reglas if r.tipo_homologacion in TIPOS_GENERALES]
    tipo_norm = str(tipo).upper().strip()
    return [
        r
        for r in reglas
        if r.tipo_homologacion in TIPOS_GENERALES
        or (r.tipo_homologacion or "").upper().strip() == tipo_norm
    ]


def documentos_requeridos(db: Session, tipo: Optional[str]) -> List[ConocimientoNormativo]:
    """Documentos que el estudiante debe adjuntar según la normativa."""
    return [
        r
        for r in reglas_para_tipo(db, tipo)
        if (r.tipo_regla or "").upper() == "REQUISITO"
        and (r.categoria or "").upper().startswith("DOCUMENT")
    ]


# ---------------------------------------------------------------------------
# Utilidades de valores
# ---------------------------------------------------------------------------
def _num(valor: Any) -> Optional[float]:
    if isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    if isinstance(valor, str):
        try:
            return float(valor.replace(",", "."))
        except ValueError:
            return None
    if isinstance(valor, dict):
        for key in ("valor", "value", "min", "max", "nota", "limite", "límite"):
            if key in valor:
                return _num(valor[key])
    return None


def _campo(regla: ConocimientoNormativo) -> Optional[str]:
    titulo = (regla.titulo or "").lower()
    desc = (regla.descripcion or "").lower()
    categoria = (regla.categoria or "").upper()

    if categoria == "NOTA" or "nota" in titulo:
        return "nota"
    if categoria == "SEMESTRE" or "semestre" in titulo:
        return "semestre"
    if categoria == "CREDITOS" or "crédito" in titulo or "credito" in titulo:
        if "porcentaje" in desc or "%" in desc:
            return "porcentaje_creditos"
        return "creditos"
    return None


def _comparar(actual: float, operador: str, referencia: float) -> Optional[bool]:
    op = (operador or "").strip()
    if op in (">=", "=>"):
        return actual >= referencia
    if op in ("<=", "=<"):
        return actual <= referencia
    if op == ">":
        return actual > referencia
    if op == "<":
        return actual < referencia
    if op in ("=", "=="):
        return actual == referencia
    if op in ("!=", "<>"):
        return actual != referencia
    return None


def _fuente(regla: ConocimientoNormativo) -> str:
    fuente = regla.documento_fuente or "registro interno"
    if regla.articulo:
        return f"{fuente}, {regla.articulo}"
    return fuente


# ---------------------------------------------------------------------------
# Condiciones concretas
# ---------------------------------------------------------------------------
def nota_minima(db: Session, tipo: Optional[str]) -> Optional[Dict]:
    """Devuelve la regla de nota mínima aplicable al tipo, si existe."""
    if not tipo:
        return None
    for regla in reglas_para_tipo(db, tipo):
        if _campo(regla) != "nota":
            continue
        valor = _num(regla.valor)
        if valor is None:
            continue
        return {
            "valor": valor,
            "operador": regla.operador or ">=",
            "regla": regla,
            "fuente": _fuente(regla),
        }
    return None


def regla_semestre(db: Session, tipo: Optional[str]) -> Optional[Dict]:
    for regla in reglas_para_tipo(db, tipo):
        if _campo(regla) != "semestre":
            continue
        valor = _num(regla.valor)
        if valor is None:
            continue
        return {"valor": valor, "operador": regla.operador or ">=", "regla": regla}
    return None


def regla_materia_aprobada(db: Session, tipo: Optional[str]) -> Optional[ConocimientoNormativo]:
    for regla in reglas_para_tipo(db, tipo):
        texto = f"{regla.titulo or ''} {regla.descripcion or ''}".lower()
        if "aprobad" in texto and (regla.tipo_regla or "").upper() == "REQUISITO":
            return regla
    return None


def evaluar_nota(db: Session, tipo: Optional[str], nota: float) -> Optional[Dict]:
    condicion = nota_minima(db, tipo)
    if not condicion:
        return None
    cumple = _comparar(nota, condicion["operador"], condicion["valor"])
    return {
        "campo": "nota",
        "actual": nota,
        "minimo": condicion["valor"],
        "operador": condicion["operador"],
        "cumple": bool(cumple),
        "fuente": condicion["fuente"],
        "regla": condicion["regla"],
    }


def evaluar_semestre(db: Session, tipo: Optional[str], semestre: int) -> Optional[Dict]:
    condicion = regla_semestre(db, tipo)
    if not condicion:
        return None
    cumple = _comparar(semestre, condicion["operador"], condicion["valor"])
    return {
        "campo": "semestre",
        "actual": semestre,
        "minimo": condicion["valor"],
        "operador": condicion["operador"],
        "cumple": bool(cumple),
        "fuente": _fuente(condicion["regla"]),
        "regla": condicion["regla"],
    }


def evaluar_condiciones(db: Session, tipo: Optional[str], datos: Dict[str, Any]) -> List[Dict]:
    """Evalúa todas las condiciones numéricas aplicables al tipo."""
    resultados: List[Dict] = []
    for regla in reglas_para_tipo(db, tipo):
        campo = _campo(regla)
        valor = _num(regla.valor)
        if not campo or valor is None:
            continue
        if campo == "porcentaje_creditos":
            resultados.append(
                {
                    "campo": campo,
                    "estado": "informativo",
                    "mensaje": regla.descripcion,
                    "regla": regla,
                }
            )
            continue
        actual = datos.get(campo)
        if actual is None:
            resultados.append(
                {
                    "campo": campo,
                    "estado": "pendiente",
                    "mensaje": f"Falta el dato '{campo}'.",
                    "regla": regla,
                }
            )
            continue
        cumple = _comparar(float(actual), regla.operador or "", valor)
        if cumple is None:
            continue
        resultados.append(
            {
                "campo": campo,
                "estado": "cumple" if cumple else "no_cumple",
                "actual": actual,
                "referencia": valor,
                "operador": regla.operador,
                "fuente": _fuente(regla),
                "regla": regla,
            }
        )
    return resultados


# ---------------------------------------------------------------------------
# Texto para el estudiante
# ---------------------------------------------------------------------------
SECCIONES = [
    ("NOTA", "🎯", "Condiciones académicas"),
    ("DOCUMENTACION", "📄", "Documentos que debes entregar"),
    ("PROCEDIMIENTO", "📝", "Procedimiento"),
    ("PLAZO", "⏰", "Plazo"),
    ("CREDITOS", "📊", "Límite de créditos"),
    ("LIMITE", "📊", "Límite de créditos"),
    ("EQUIVALENCIA", "🔁", "Evaluación de equivalencia"),
    ("INFORMATIVO", "ℹ️", "Ten en cuenta"),
]


def etiqueta_tipo(tipo: Optional[str]) -> str:
    if not tipo:
        return "reglas generales"
    return f"homologación {str(tipo).lower()}"


def _limpiar(texto: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (texto or "").strip())


def _frase(texto: Optional[str], capitalizar: bool = True) -> str:
    limpio = _limpiar(texto)
    if limpio and capitalizar:
        limpio = limpio[0].upper() + limpio[1:]
    return limpio


def _detalle_documento(regla: ConocimientoNormativo) -> str:
    """Convierte 'Debe presentar certificado...' en un texto breve y natural."""
    titulo = _limpiar(regla.titulo)
    detalle = _limpiar(regla.descripcion)
    detalle = re.sub(
        r"^(debe presentar|debe anexar|debe adjuntar|se debe presentar|presentar)\s+",
        "",
        detalle,
        flags=re.IGNORECASE,
    )
    if titulo and detalle.lower().startswith(titulo.lower()):
        detalle = detalle[len(titulo):].lstrip(" :,.-")
    return _frase(detalle, capitalizar=False)


def _linea_regla(regla: ConocimientoNormativo) -> str:
    categoria = (regla.categoria or "").upper()
    valor = _num(regla.valor)

    if categoria == "NOTA" and valor is not None:
        return (
            f"• **Nota mínima: {valor:g}** — necesitas al menos esta calificación "
            "para que tu solicitud sea viable."
        )

    descripcion = _frase(regla.descripcion)
    if not descripcion:
        return f"• {_frase(regla.titulo)}"

    if categoria in ("LIMITE", "CREDITOS"):
        if valor is not None and ("%" in descripcion or "porcentaje" in descripcion.lower()):
            return (
                f"• La suma de créditos que homologues o convalides no puede superar "
                f"el **{valor:g} %** de los créditos de tu plan de estudios."
            )
        descripcion = re.sub(
            r"(\d+(?:[.,]\d+)?)\s*%", r"**\1 %**", descripcion
        )

    if categoria in ("PROCEDIMIENTO", "EQUIVALENCIA", "INFORMATIVO") and regla.titulo:
        return f"• **{_limpiar(regla.titulo)}:** {descripcion}"
    return f"• {descripcion}"


def _bloques(reglas: List[ConocimientoNormativo]) -> List[str]:
    lineas: List[str] = []
    usadas = set()

    for categoria, emoji, titulo in SECCIONES:
        grupo = [r for r in reglas if (r.categoria or "").upper() == categoria]
        if not grupo:
            continue
        for regla in grupo:
            usadas.add(regla.id)

        if categoria == "DOCUMENTACION":
            lineas.append(f"{emoji} **{titulo} ({len(grupo)})**")
            for indice, regla in enumerate(grupo, 1):
                detalle = _detalle_documento(regla)
                linea = f"{indice}. **{_limpiar(regla.titulo)}**"
                if detalle:
                    linea += f" — {detalle}"
                lineas.append(linea)
        else:
            lineas.append(f"{emoji} **{titulo}**")
            lineas.extend(_linea_regla(regla) for regla in grupo)
        lineas.append("")

    otras = [r for r in reglas if r.id not in usadas]
    if otras:
        lineas.append("📌 **Otras reglas registradas**")
        lineas.extend(_linea_regla(regla) for regla in otras)
        lineas.append("")

    return lineas


def _fuente_final(reglas: List[ConocimientoNormativo]) -> str:
    fuentes: List[str] = []
    articulos: List[str] = []
    for regla in reglas:
        if regla.documento_fuente and regla.documento_fuente not in fuentes:
            fuentes.append(regla.documento_fuente)
        if regla.articulo and regla.articulo not in articulos:
            articulos.append(regla.articulo)

    fuente = ", ".join(fuentes) or "registro interno"
    detalle = f"{fuente} ({'; '.join(articulos)})" if articulos else fuente
    return (
        f"📚 **Fuente:** {detalle}\n"
        "⚠️ La información proviene de la normativa institucional. La decisión final "
        "de homologación corresponde a la universidad."
    )


def requisitos_texto(db: Session, tipo: Optional[str]) -> str:
    """Información estructurada y legible, tomada directamente de MySQL."""
    reglas = reglas_para_tipo(db, tipo)
    if not reglas:
        return (
            "No hay reglas normativas activas cargadas para este caso. Para no darte "
            "información incorrecta, no voy a inventar requisitos: la oficina de "
            "homologaciones debe confirmarlos."
        )

    if tipo:
        lineas = [
            f"📋 **Homologación {str(tipo).lower()}: lo que necesitas saber**",
            "",
        ]
    else:
        lineas = ["📋 **Requisitos generales de homologación**", ""]

    lineas.extend(_bloques(reglas))
    lineas.append(_fuente_final(reglas))
    return "\n".join(lineas).strip()


def requisitos_generales_texto(db: Session) -> str:
    """Panorama completo: qué cambia entre homologación interna y externa."""
    reglas = reglas_activas(db)
    if not reglas:
        return requisitos_texto(db, None)

    internas = [
        r for r in reglas if (r.tipo_homologacion or "").upper() == "INTERNA"
    ]
    externas = [
        r for r in reglas if (r.tipo_homologacion or "").upper() == "EXTERNA"
    ]
    comunes = [r for r in reglas if r.tipo_homologacion in TIPOS_GENERALES]

    lineas = [
        "📋 **Homologación: panorama general**",
        "",
        "La homologación puede ser de dos tipos. Esto es lo que registra la normativa:",
        "",
    ]

    if internas:
        lineas.append("🏫 **Homologación interna** (misma institución)")
        lineas.append("")
        lineas.extend(_bloques(internas))
    if externas:
        lineas.append("🌍 **Homologación externa** (otra institución)")
        lineas.append("")
        lineas.extend(_bloques(externas))
    if comunes:
        lineas.append("📌 **Aplica a ambos tipos**")
        lineas.append("")
        lineas.extend(_bloques(comunes))

    lineas.append(_fuente_final(reglas))
    return "\n".join(lineas).strip()


def resumen_tipo(db: Session, tipo: Optional[str]) -> str:
    """Resumen corto para continuar el trámite sin abrumar."""
    lineas = [f"Para **homologación {str(tipo or '').lower()}** esto es lo que aplica:"]

    condicion = nota_minima(db, tipo)
    if condicion:
        lineas.append(f"• Nota mínima: **{condicion['valor']:g}**")

    documentos = documentos_requeridos(db, tipo)
    if documentos:
        titulos = ", ".join(d.titulo for d in documentos)
        lineas.append(f"• Documentos: **{len(documentos)}** ({titulos})")
    else:
        lineas.append("• Documentos adicionales: **no se exigen** en el registro.")

    plazo = resumen_plazo(db, tipo)
    if plazo:
        lineas.append(f"• Plazo: {plazo}")

    return "\n".join(lineas)


def texto_limite(db: Session, tipo: Optional[str]) -> Optional[str]:
    """Frase amable sobre el límite de créditos, tomada de la normativa."""
    for regla in reglas_para_tipo(db, tipo):
        if (regla.categoria or "").upper() not in ("CREDITOS", "LIMITE"):
            continue
        valor = _num(regla.valor)
        if valor is not None and (
            "%" in (regla.descripcion or "")
            or "porcentaje" in (regla.descripcion or "").lower()
        ):
            return (
                f"la suma de créditos que homologues o convalides no puede superar "
                f"el **{valor:g} %** de los créditos de tu plan de estudios"
            )
        if regla.descripcion:
            return _frase(regla.descripcion, capitalizar=False)
    return None


def resumen_plazo(db: Session, tipo: Optional[str]) -> Optional[str]:
    for regla in reglas_para_tipo(db, tipo):
        if (regla.tipo_regla or "").upper() == "PLAZO":
            return f"{regla.descripcion} ({_fuente(regla)})"
    return None