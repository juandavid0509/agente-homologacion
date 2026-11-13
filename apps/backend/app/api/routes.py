import logging
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.core.security import get_password_hash, verify_password
from app.db.database import get_db
from app.db.models import Programa, Usuario
from app.schemas.models import (
    ChatRequest,
    ConversationCreate,
    LoginRequest,
    RegisterRequest,
)
from app.services import agent
from app.services import conversation as conv_service
from app.services import documentos as doc_service
from app.services import solicitudes

logger = logging.getLogger(__name__)

router = APIRouter()


def _get_user(db: Session, user_id: int) -> Usuario:
    user = db.query(Usuario).filter(Usuario.estudiante_id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    return user


def _conversation_payload(conv) -> dict:
    return {
        "id": conv.conversacion_id,
        "title": conv.titulo or "Nueva conversación",
        "state": conv.estado,
        "proceso_completado": bool(conv.proceso_completado),
        # Un chat de trámite radicado se conserva siempre (no se puede borrar).
        "locked": bool(conv.proceso_completado),
        "updated_at": conv.actualizada_en.isoformat() if conv.actualizada_en else None,
    }


def _message_payload(msg) -> dict:
    return {
        "id": msg.mensaje_id,
        "role": msg.rol,
        "content": msg.contenido,
        "created_at": msg.creado_en.isoformat() if msg.creado_en else None,
    }


@router.post("/login")
def login(req: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(Usuario).filter(Usuario.correo == req.correo).first()
    if not user or not verify_password(req.password, user.password):
        raise HTTPException(status_code=401, detail="Correo o contraseña incorrectos")
    return {
        "id": user.estudiante_id,
        "name": f"{user.nombres} {user.apellidos}",
        "role": user.rol,
        "codigo": user.codigo,
    }


@router.get("/programas")
def get_programas(db: Session = Depends(get_db)):
    programas = db.query(Programa).all()
    return [{"id": p.programa_id, "nombre": p.nombre} for p in programas]


@router.post("/register")
def register(req: RegisterRequest, db: Session = Depends(get_db)):
    if db.query(Usuario).filter(Usuario.codigo == req.codigo).first():
        raise HTTPException(status_code=400, detail="El código estudiantil ya está registrado")
    if db.query(Usuario).filter(Usuario.correo == req.correo).first():
        raise HTTPException(status_code=400, detail="El correo electrónico ya está registrado")

    new_user = Usuario(
        documento=req.documento,
        codigo=req.codigo,
        nombres=req.nombres,
        apellidos=req.apellidos,
        correo=req.correo,
        password=get_password_hash(req.password),
        programa_id=req.programa_id,
        rol="ESTUDIANTE",
        estado=1,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return {"message": "Usuario registrado exitosamente"}


# ---------------------------------------------------------------------------
# Conversaciones
# ---------------------------------------------------------------------------
@router.get("/conversations")
def list_conversations(user_id: int, db: Session = Depends(get_db)):
    _get_user(db, user_id)
    conversaciones = conv_service.list_conversations(db, user_id)
    return [_conversation_payload(c) for c in conversaciones]


@router.get("/conversations/draft")
def get_draft_conversation(user_id: int, db: Session = Depends(get_db)):
    """Chat en curso (borrador) para retomarlo al recargar la página."""
    _get_user(db, user_id)
    conv = conv_service.get_draft(db, user_id)
    return _conversation_payload(conv) if conv else None


@router.post("/conversations")
def create_conversation(req: ConversationCreate, db: Session = Depends(get_db)):
    user = _get_user(db, req.user_id)
    conv_service.delete_empty_drafts(db, user.estudiante_id)
    conv = conv_service.create_conversation(
        db, user, titulo=req.titulo, solicitud_id=req.solicitud_id
    )
    return _conversation_payload(conv)


@router.get("/conversations/{conversation_id}/messages")
def get_messages(conversation_id: int, user_id: int, db: Session = Depends(get_db)):
    _get_user(db, user_id)
    try:
        conv = conv_service.get_for_user(db, conversation_id, user_id)
    except PermissionError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    # Los eventos internos (rol "system") no se muestran en la interfaz.
    return [_message_payload(m) for m in conv.mensajes if m.rol in ("user", "assistant")]


@router.delete("/conversations/{conversation_id}")
def delete_conversation(conversation_id: int, user_id: int, db: Session = Depends(get_db)):
    _get_user(db, user_id)
    try:
        conv_service.delete_conversation(db, conversation_id, user_id)
    except PermissionError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except conv_service.ConversationLockedError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"deleted": True, "id": conversation_id}


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------
@router.post("/chat")
def chat(req: ChatRequest, db: Session = Depends(get_db)):
    if req.user_id is None:
        raise HTTPException(status_code=400, detail="user_id es obligatorio")

    user = _get_user(db, req.user_id)

    message = (req.message or "").strip()
    if not message:
        raise HTTPException(status_code=422, detail="El mensaje no puede estar vacío")

    try:
        conv = conv_service.get_or_create(db, user, req.conversation_id)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    # 1) Persistir el mensaje del estudiante (esto ya forma parte del historial).
    conv_service.add_message(db, conv, "user", message)

    # 2) Generar la respuesta con historial + contexto.
    response = agent.generate_response(user, message, db, conv)

    # 3) Persistir la respuesta del asistente.
    assistant_msg = conv_service.add_message(db, conv, "assistant", response)

    # 4) Resumen incremental si la conversación es muy larga.
    try:
        conv_service.maybe_summarize(db, conv)
    except Exception as exc:  # nunca romper el chat por el resumen
        logger.warning("No se pudo resumir la conversación: %s", exc)

    return {
        "response": response,
        "conversation_id": conv.conversacion_id,
        "message_id": assistant_msg.mensaje_id,
        "conversation_saved": bool(conv.proceso_completado),
    }


# ---------------------------------------------------------------------------
# Documentos
# ---------------------------------------------------------------------------
@router.post("/upload_document")
async def upload_document(
    user_id: int = Form(...),
    tipo_documento: str = Form(""),
    conversation_id: Optional[int] = Form(None),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Solo se permiten archivos PDF")

    file_data = await file.read()
    if len(file_data) > doc_service.MAX_PDF_BYTES:
        raise HTTPException(
            status_code=400,
            detail=(
                "El archivo supera el tamaño máximo permitido de **5 MB**. "
                "Comprime el PDF o sube una versión más liviana."
            ),
        )

    user = _get_user(db, user_id)

    try:
        conv = conv_service.get_or_create(db, user, conversation_id)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    if conv.proceso_completado:
        response = (
            f"Tu solicitud ya quedó registrada y está **en espera de aprobación**, "
            f"{user.nombres or 'estudiante'}. Si necesitas otro trámite, inicia una "
            "conversación nueva. ¿Te ayudo en algo más?"
        )
        assistant_msg = conv_service.add_message(db, conv, "assistant", response)
        return {
            "message": "El trámite ya fue enviado",
            "response": response,
            "conversation_id": conv.conversacion_id,
            "message_id": assistant_msg.mensaje_id,
            "verificado": False,
            "conversation_saved": True,
        }

    hechos = agent._merge_hechos(conv)
    esperado, _es_reemplazo = agent.preparar_documento(db, hechos)
    tipo_final = (esperado or tipo_documento or "").strip()

    if not tipo_final:
        response = (
            f"Gracias, {user.nombres or 'estudiante'}. Ya tengo todos los documentos "
            "requeridos para este trámite. Si necesitas **reemplazar** uno, dime cuál "
            "y con gusto lo actualizamos."
        )
        assistant_msg = conv_service.add_message(db, conv, "assistant", response)
        return {
            "message": "No hay documentos pendientes",
            "response": response,
            "conversation_id": conv.conversacion_id,
            "message_id": assistant_msg.mensaje_id,
            "verificado": False,
            "conversation_saved": bool(conv.proceso_completado),
        }

    # Verificación determinista: tamaño + nombre/documento del estudiante en el PDF.
    resultado = doc_service.verificar_documento(file_data, user, tipo_final)
    if not resultado["ok"]:
        response = agent.handle_document_rejected(
            user, db, conv, file.filename, tipo_final, resultado["motivo"]
        )
        assistant_msg = conv_service.add_message(db, conv, "assistant", response)
        return {
            "message": "El documento no coincidió con tus datos",
            "response": response,
            "conversation_id": conv.conversacion_id,
            "message_id": assistant_msg.mensaje_id,
            "tipo_documento": tipo_final,
            "verificado": False,
            "conversation_saved": bool(conv.proceso_completado),
        }

    automatico = resultado["identidad"] in ("nombre", "documento")
    documento = solicitudes.guardar_documento(
        db,
        conv,
        tipo_documento=tipo_final,
        nombre_archivo=file.filename,
        contenido=file_data,
        verificado=automatico,
        observacion=None if automatico else resultado["motivo"],
    )

    conv_service.add_message(
        db, conv, "system",
        f"El estudiante subió el documento '{file.filename}' ({tipo_final}).",
        meta={"tipo": "documento", "documento_id": documento.documento_id},
    )

    response = agent.handle_document_uploaded(
        user, db, conv, file.filename, tipo_final,
        documento_id=documento.documento_id,
        verificado=automatico,
        observacion=None if automatico else resultado["motivo"],
    )
    assistant_msg = conv_service.add_message(db, conv, "assistant", response)

    return {
        "message": (
            "Documento guardado y validado correctamente"
            if automatico
            else "Documento guardado; quedó pendiente de validación manual"
        ),
        "response": response,
        "conversation_id": conv.conversacion_id,
        "message_id": assistant_msg.mensaje_id,
        "tipo_documento": tipo_final,
        "verificado": True,
        "verificacion_automatica": automatico,
        "conversation_saved": bool(conv.proceso_completado),
    }
