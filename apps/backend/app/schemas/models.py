from typing import Optional

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    correo: str
    password: str


class RegisterRequest(BaseModel):
    documento: str
    codigo: str
    nombres: str
    apellidos: str
    correo: str
    password: str
    programa_id: int


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    user_id: Optional[int] = None
    conversation_id: Optional[int] = None


class ChatResponse(BaseModel):
    response: str
    conversation_id: int
    message_id: Optional[int] = None


class ConversationCreate(BaseModel):
    user_id: int
    titulo: Optional[str] = None
    solicitud_id: Optional[int] = None


class ConversationOut(BaseModel):
    id: int
    title: str
    state: str
    proceso_completado: bool = False
    updated_at: Optional[str] = None


class MessageOut(BaseModel):
    id: int
    role: str
    content: str
    created_at: Optional[str] = None
