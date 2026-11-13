"""Persistencia y gestión de conversaciones / mensajes."""

import datetime
import logging
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import Conversacion, Documento, Mensaje, Solicitud, Usuario
from app.services import llm
from app.services.solicitudes import ESTADO_BORRADOR

logger = logging.getLogger(__name__)


class ConversationLockedError(Exception):
    """El chat pertenece a una solicitud radicada y no puede eliminarse."""

DEFAULT_HECHOS: Dict = {
    "state": "IDLE",
    "materia": None,
    "materia_id": None,
    "materia_ingresada": None,
    "materia_verificada": False,
    "tipo": None,
    "creditos": None,
    "nota": None,
    "semestre": None,
    "aprobada": None,
    "docs_subidos": 0,
    "documentos": [],
    "reemplazar": None,
    "solicitud_id": None,
    "motivo_no_viable": None,
    "_return_state": None,
}

GREETING = (
    "¡Hola, {nombre}! 😊 Soy **Lía**, tu orientadora en procesos de homologación. "
    "Puedo explicarte los requisitos, revisar si una materia figura como homologable "
    "o acompañarte paso a paso para iniciar tu solicitud. Si quieres, dime "
    "**«toda la información»** y te muestro el panorama completo. ¿En qué te ayudo hoy?"
)


def create_conversation(
    db: Session,
    user: Usuario,
    titulo: Optional[str] = None,
    solicitud_id: Optional[int] = None,
) -> Conversacion:
    conv = Conversacion(
        estudiante_id=user.estudiante_id,
        solicitud_id=solicitud_id,
        titulo=titulo or "Nueva conversación",
        estado="ACTIVA",
        proceso_completado=False,
        hechos=dict(DEFAULT_HECHOS),
    )
    db.add(conv)
    db.commit()
    db.refresh(conv)

    add_message(
        db,
        conv,
        "assistant",
        GREETING.format(nombre=user.nombres or "estudiante"),
        meta={"tipo": "saludo_inicial"},
    )
    return conv


def get_for_user(db: Session, conversation_id: int, user_id: int) -> Conversacion:
    conv = (
        db.query(Conversacion)
        .filter(Conversacion.conversacion_id == conversation_id)
        .first()
    )
    if not conv or conv.estudiante_id != user_id:
        raise PermissionError("La conversación no existe o no pertenece al estudiante.")
    return conv


def get_or_create(
    db: Session,
    user: Usuario,
    conversation_id: Optional[int] = None,
) -> Conversacion:
    if conversation_id is not None:
        return get_for_user(db, conversation_id, user.estudiante_id)

    conv = (
        db.query(Conversacion)
        .filter(
            Conversacion.estudiante_id == user.estudiante_id,
            Conversacion.estado == "ACTIVA",
            Conversacion.proceso_completado.is_(False),
        )
        .order_by(Conversacion.actualizada_en.desc(), Conversacion.conversacion_id.desc())
        .first()
    )
    if conv:
        return conv
    return create_conversation(db, user)


def get_draft(db: Session, user_id: int) -> Optional[Conversacion]:
    """Borrador actual: chat en curso que aún no termina un trámite."""
    return (
        db.query(Conversacion)
        .filter(
            Conversacion.estudiante_id == user_id,
            Conversacion.estado == "ACTIVA",
            Conversacion.proceso_completado.is_(False),
        )
        .order_by(Conversacion.actualizada_en.desc(), Conversacion.conversacion_id.desc())
        .first()
    )


def list_conversations(db: Session, user_id: int) -> List[Conversacion]:
    """Historial visible: únicamente procesos de homologación terminados."""
    return (
        db.query(Conversacion)
        .filter(
            Conversacion.estudiante_id == user_id,
            Conversacion.proceso_completado.is_(True),
            Conversacion.estado != "ARCHIVADA",
        )
        .order_by(Conversacion.actualizada_en.desc(), Conversacion.conversacion_id.desc())
        .all()
    )


def mark_completed(db: Session, conv: Conversacion, hechos: dict) -> None:
    """Marca el trámite como terminado y lo deja visible en el historial."""
    conv.proceso_completado = True
    tipo = (hechos.get("tipo") or "").strip().lower()
    materia = (hechos.get("materia") or "").strip()
    if tipo and materia:
        conv.titulo = f"Homologación {tipo} · {materia}"[:200]
    elif tipo:
        conv.titulo = f"Homologación {tipo}"[:200]
    conv.actualizada_en = datetime.datetime.utcnow()
    db.add(conv)
    db.commit()


def _descartar_borrador(db: Session, solicitud_id: Optional[int]) -> None:
    """Elimina la solicitud en borrador (con sus documentos) de un chat.

    Solo se tocan solicitudes en estado 0: un trámite ya radicado nunca se
    borra al eliminar el chat, porque forma parte del historial oficial.
    """
    if not solicitud_id:
        return
    borrador = (
        db.query(Solicitud)
        .filter(
            Solicitud.solicitud_id == solicitud_id,
            Solicitud.estado == ESTADO_BORRADOR,
        )
        .first()
    )
    if borrador is None:
        return
    db.query(Documento).filter(Documento.solicitud_id == solicitud_id).delete(
        synchronize_session=False
    )
    db.delete(borrador)


def _tramite_radicado(db: Session, conv: Conversacion) -> bool:
    """True si el chat ya forma parte de una solicitud radicada."""
    if conv.proceso_completado:
        return True
    if not conv.solicitud_id:
        return False
    return (
        db.query(Solicitud)
        .filter(
            Solicitud.solicitud_id == conv.solicitud_id,
            Solicitud.estado != ESTADO_BORRADOR,
        )
        .first()
        is not None
    )


def delete_conversation(db: Session, conversation_id: int, user_id: int) -> None:
    """Elimina una conversación en borrador y todos sus mensajes.

    Los chats asociados a una solicitud radicada son historial del trámite y
    no se pueden borrar (ni siquiera por el propio estudiante).
    """
    conv = get_for_user(db, conversation_id, user_id)
    if _tramite_radicado(db, conv):
        raise ConversationLockedError(
            "Este chat está asociado a una solicitud radicada y no se puede eliminar: "
            "forma parte del historial de tu trámite."
        )

    solicitud_id = conv.solicitud_id
    db.query(Mensaje).filter(Mensaje.conversacion_id == conversation_id).delete(
        synchronize_session=False
    )
    # Se borra primero la conversación por la llave foránea hacia solicitudes.
    db.query(Conversacion).filter(
        Conversacion.conversacion_id == conversation_id
    ).delete(synchronize_session=False)
    db.flush()
    _descartar_borrador(db, solicitud_id)
    db.commit()


def delete_empty_drafts(db: Session, user_id: int) -> None:
    """Limpia borradores sin participación del estudiante (evita basura)."""
    borradores = (
        db.query(Conversacion)
        .filter(
            Conversacion.estudiante_id == user_id,
            Conversacion.estado == "ACTIVA",
            Conversacion.proceso_completado.is_(False),
        )
        .all()
    )
    for conv in borradores:
        tiene_usuario = (
            db.query(Mensaje)
            .filter(Mensaje.conversacion_id == conv.conversacion_id, Mensaje.rol == "user")
            .first()
        )
        if not tiene_usuario:
            solicitud_id = conv.solicitud_id
            db.query(Mensaje).filter(
                Mensaje.conversacion_id == conv.conversacion_id
            ).delete(synchronize_session=False)
            db.query(Conversacion).filter(
                Conversacion.conversacion_id == conv.conversacion_id
            ).delete(synchronize_session=False)
            db.flush()
            _descartar_borrador(db, solicitud_id)
    db.commit()


def archive_conversation(db: Session, conversation_id: int, user_id: int) -> Conversacion:
    conv = get_for_user(db, conversation_id, user_id)
    conv.estado = "ARCHIVADA"
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return conv


def add_message(
    db: Session,
    conv: Conversacion,
    role: str,
    content: str,
    meta: Optional[dict] = None,
) -> Mensaje:
    content = (content or "").strip()
    msg = Mensaje(
        conversacion_id=conv.conversacion_id,
        rol=role,
        contenido=content,
        tokens_aprox=max(1, len(content) // 4),
        meta=meta,
    )
    db.add(msg)
    if role == "user" and (not conv.titulo or conv.titulo == "Nueva conversación"):
        conv.titulo = content[:80] or "Nueva conversación"
    conv.actualizada_en = datetime.datetime.utcnow()
    db.add(conv)
    db.commit()
    db.refresh(msg)
    return msg


def build_history(db: Session, conv: Conversacion) -> List[Dict[str, str]]:
    """Últimos turnos user/assistant, acotados por número y por caracteres."""
    query = db.query(Mensaje).filter(Mensaje.conversacion_id == conv.conversacion_id)
    if conv.resumen_hasta_id:
        query = query.filter(Mensaje.mensaje_id > conv.resumen_hasta_id)
    rows = (
        query.order_by(Mensaje.mensaje_id.desc())
        .limit(settings.HISTORY_MAX_MESSAGES)
        .all()
    )
    rows.reverse()

    selected: List[Dict[str, str]] = []
    used_chars = 0
    for msg in reversed(rows):
        if msg.rol not in ("user", "assistant"):
            continue
        if not msg.contenido:
            continue
        if used_chars + len(msg.contenido) > settings.HISTORY_MAX_CHARS and selected:
            break
        selected.append({"role": msg.rol, "content": msg.contenido})
        used_chars += len(msg.contenido)
    selected.reverse()
    return selected


def save_hechos(db: Session, conv: Conversacion, hechos: dict) -> None:
    # Se reasigna el diccionario completo para que SQLAlchemy detecte el cambio.
    conv.hechos = dict(hechos)
    conv.actualizada_en = datetime.datetime.utcnow()
    db.add(conv)
    db.commit()


def maybe_summarize(db: Session, conv: Conversacion) -> None:
    """Resume los mensajes antiguos cuando la conversación crece demasiado.

    Los mensajes NO se borran (auditoría); solo se dejan de enviar al modelo
    y se sustituyen por un resumen persistente.
    """
    query = db.query(Mensaje).filter(
        Mensaje.conversacion_id == conv.conversacion_id,
        Mensaje.rol.in_(["user", "assistant"]),
    )
    if conv.resumen_hasta_id:
        query = query.filter(Mensaje.mensaje_id > conv.resumen_hasta_id)
    mensajes = query.order_by(Mensaje.mensaje_id.asc()).all()

    if len(mensajes) <= settings.SUMMARY_TRIGGER_MESSAGES:
        return

    keep = max(2, settings.SUMMARY_KEEP_RECENT)
    a_resumir = mensajes[:-keep]
    if not a_resumir:
        return

    transcripcion = "\n".join(
        f"{'Estudiante' if m.rol == 'user' else 'Lía'}: {m.contenido[:600]}"
        for m in a_resumir
    )

    resumen_previo = conv.resumen or "Sin resumen previo."
    try:
        nuevo_resumen = llm.chat(
            [
                {
                    "role": "system",
                    "content": (
                        "Resume conversaciones de orientación académica. Conserva solo: "
                        "datos aportados por el estudiante, materias mencionadas, tipo de "
                        "homologación, créditos, documentos entregados, dudas pendientes y "
                        "compromisos. No inventes información. Responde en español, en "
                        "viñetas breves y en tercera persona."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Resumen anterior:\n{resumen_previo}\n\n"
                        f"Conversación nueva a incorporar:\n{transcripcion}\n\n"
                        "Genera el resumen actualizado (máximo 200 palabras)."
                    ),
                },
            ],
            num_predict=260,
            temperature=0.2,
        )
    except Exception as exc:  # Ollama caído: resumen heurístico, nunca romper el chat
        logger.warning("No se pudo resumir con Ollama (%s); se usa resumen heurístico.", exc)
        nuevo_resumen = (resumen_previo + "\n" + transcripcion)[-4000:]

    conv.resumen = nuevo_resumen.strip()
    conv.resumen_hasta_id = a_resumir[-1].mensaje_id
    db.add(conv)
    db.commit()
