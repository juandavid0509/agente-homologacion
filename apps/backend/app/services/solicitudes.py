"""Persistencia de solicitudes y documentos del trámite de homologación.

La solicitud se crea como **borrador** mientras el estudiante conversa y solo
pasa a estado **radicada** cuando confirma el resumen final. Así el historial
no muestra trámites a medias y, si abandona, no queda una solicitud enviada.
"""

import datetime
from typing import Iterable, Optional

from sqlalchemy.orm import Session

from app.db.models import Conversacion, Documento, Materia, Solicitud
from app.services import normativa

ESTADO_BORRADOR = 0
ESTADO_RADICADA = 1
ESTADO_APROBADA = 2
ESTADO_RECHAZADA = 3


def obtener_borrador(
    db: Session, user_id: int, conversacion: Optional[Conversacion] = None
) -> Optional[Solicitud]:
    if conversacion is not None and conversacion.solicitud_id:
        solicitud = (
            db.query(Solicitud)
            .filter(
                Solicitud.solicitud_id == conversacion.solicitud_id,
                Solicitud.estudiante_id == user_id,
                Solicitud.estado == ESTADO_BORRADOR,
            )
            .first()
        )
        if solicitud:
            return solicitud

    return (
        db.query(Solicitud)
        .filter(
            Solicitud.estudiante_id == user_id,
            Solicitud.estado == ESTADO_BORRADOR,
        )
        .order_by(Solicitud.solicitud_id.desc())
        .first()
    )


def asegurar_borrador(db: Session, conversacion: Conversacion) -> Solicitud:
    """Devuelve el borrador de la conversación, creándolo si no existe."""
    solicitud = obtener_borrador(db, conversacion.estudiante_id, conversacion)
    if solicitud is None:
        solicitud = Solicitud(
            estudiante_id=conversacion.estudiante_id,
            materia_origen="",
            estado=ESTADO_BORRADOR,
            fecha_registro=datetime.datetime.utcnow(),
        )
        db.add(solicitud)
        db.commit()
        db.refresh(solicitud)

    if conversacion.solicitud_id != solicitud.solicitud_id:
        conversacion.solicitud_id = solicitud.solicitud_id
        db.add(conversacion)
        db.commit()

    return solicitud


def _materia_destino(db: Session, hechos: dict) -> Optional[Materia]:
    materia_id = hechos.get("materia_id")
    if materia_id:
        materia = (
            db.query(Materia).filter(Materia.materia_id == materia_id).first()
        )
        if materia:
            return materia

    nombre = (hechos.get("materia") or "").strip()
    if not nombre:
        return None
    return (
        db.query(Materia)
        .filter(Materia.nombre == nombre)
        .first()
    )


def sincronizar(
    db: Session,
    conversacion: Conversacion,
    hechos: dict,
    *,
    finalizar: bool = False,
) -> Solicitud:
    """Vuelca los datos conversados en las columnas de ``solicitudes``."""
    solicitud = asegurar_borrador(db, conversacion)
    materia = _materia_destino(db, hechos)

    solicitud.materia_origen = (
        hechos.get("materia")
        or hechos.get("materia_ingresada")
        or solicitud.materia_origen
        or ""
    )[:150] or None
    solicitud.materia_destino_id = materia.materia_id if materia else None
    solicitud.creditos_aportados = hechos.get("creditos")
    solicitud.nota_aportada = hechos.get("nota")
    solicitud.tipo_homologacion = (hechos.get("tipo") or "").upper() or None
    solicitud.semestre_cursado = hechos.get("semestre")

    aprobada = hechos.get("aprobada")
    solicitud.materia_aprobada = None if aprobada is None else (1 if aprobada else 0)

    regla = normativa.nota_minima(db, hechos.get("tipo"))
    if regla and regla.get("regla") is not None:
        solicitud.rule_id = (regla["regla"].codigo or "")[:10] or None

    if finalizar:
        solicitud.estado = ESTADO_RADICADA

    db.add(solicitud)
    db.commit()
    db.refresh(solicitud)
    return solicitud


def guardar_documento(
    db: Session,
    conversacion: Conversacion,
    *,
    tipo_documento: str,
    nombre_archivo: str,
    contenido: bytes,
    verificado: bool = True,
    observacion: Optional[str] = None,
) -> Documento:
    """Guarda (o reemplaza) el documento vigente de un tipo."""
    solicitud = asegurar_borrador(db, conversacion)

    # Una sola versión vigente por tipo: si el estudiante se equivocó, se
    # actualiza por el nuevo sin acumular archivos duplicados.
    anteriores = (
        db.query(Documento)
        .filter(
            Documento.solicitud_id == solicitud.solicitud_id,
            Documento.tipo_documento == tipo_documento,
        )
        .all()
    )
    for anterior in anteriores:
        db.delete(anterior)
    db.flush()

    documento = Documento(
        solicitud_id=solicitud.solicitud_id,
        tipo_documento=tipo_documento,
        nombre_archivo=nombre_archivo,
        pdf_content=contenido,
        verificado=1 if verificado else 0,
        observacion=(observacion or "")[:255] or None,
        fecha_carga=datetime.datetime.utcnow(),
    )
    db.add(documento)
    db.commit()
    db.refresh(documento)
    return documento


def contar_documentos(db: Session, conversacion: Conversacion) -> int:
    if not conversacion.solicitud_id:
        return 0
    return (
        db.query(Documento)
        .filter(Documento.solicitud_id == conversacion.solicitud_id)
        .count()
    )


def descartar_documentos(
    db: Session,
    conversacion: Conversacion,
    tipos: Optional[Iterable[str]] = None,
) -> int:
    """Elimina documentos del borrador (por ejemplo, al cambiar de modalidad)."""
    if not conversacion.solicitud_id:
        return 0

    query = db.query(Documento).filter(
        Documento.solicitud_id == conversacion.solicitud_id
    )
    if tipos:
        query = query.filter(Documento.tipo_documento.in_(list(tipos)))
    documentos = query.all()
    for documento in documentos:
        db.delete(documento)
    db.commit()
    return len(documentos)
