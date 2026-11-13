from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, LargeBinary, Enum, Float, JSON, BigInteger, SmallInteger, Boolean
from sqlalchemy.orm import declarative_base, relationship
import datetime

Base = declarative_base()

class Usuario(Base):
    __tablename__ = "usuarios"
    estudiante_id = Column(Integer, primary_key=True, index=True)
    documento = Column(String(12), nullable=False)
    codigo = Column(String(20), unique=True, index=True)
    nombres = Column(String(50))
    apellidos = Column(String(50))
    correo = Column(String(100), unique=True, index=True) # <-- NUEVA LÍNEA
    password = Column(String(255), nullable=False)
    programa_id = Column(Integer, ForeignKey("programas.programa_id"))
    rol = Column(Enum('ESTUDIANTE', 'ADMIN', name="rol_enum"), default='ESTUDIANTE')
    estado = Column(SmallInteger, default=1)
    fecha_registro = Column(DateTime, default=datetime.datetime.utcnow)

class Programa(Base):
    __tablename__ = "programas"
    programa_id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String(100), unique=True)
    descripcion = Column(String(255))
    estado = Column(Integer)
    fecha_registro = Column(DateTime)

class Materia(Base):
    __tablename__ = "materias"
    materia_id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String(100), unique=True)
    creditos = Column(SmallInteger, nullable=False)
    es_homologable = Column(SmallInteger)
    estado = Column(SmallInteger)
    fecha_registro = Column(DateTime)

class ConocimientoNormativo(Base):
    __tablename__ = "conocimiento_normativo"
    id = Column(BigInteger, primary_key=True, index=True)
    codigo = Column(String(100), unique=True)
    categoria = Column(String(100))
    titulo = Column(String(255))
    descripcion = Column(Text)
    tipo_homologacion = Column(String(50))
    tipo_regla = Column(Enum('REQUISITO', 'CONDICION', 'LIMITE', 'PLAZO', 'INFORMATIVO', 'PROCEDIMIENTO'))
    valor = Column(JSON)
    operador = Column(String(30))
    es_obligatorio = Column(SmallInteger, default=1)
    articulo = Column(String(100))
    documento_fuente = Column(String(255))
    estado = Column(Enum('ACTIVO', 'INACTIVO'), default='ACTIVO')
    fecha_actualizacion = Column(DateTime, default=datetime.datetime.utcnow)

class Solicitud(Base):
    __tablename__ = "solicitudes"
    solicitud_id = Column(Integer, primary_key=True, index=True)
    estudiante_id = Column(Integer, ForeignKey("usuarios.estudiante_id"))
    materia_origen = Column(String(150))
    materia_destino_id = Column(Integer, ForeignKey("materias.materia_id"))
    creditos_aportados = Column(Integer)
    nota_aportada = Column(Float)
    tipo_homologacion = Column(String(20))
    semestre_cursado = Column(Integer)
    materia_aprobada = Column(SmallInteger)
    rule_id = Column(String(10))
    # 0 = borrador, 1 = radicada (pendiente de aprobación), 2 = aprobada, 3 = rechazada
    estado = Column(SmallInteger, default=0)
    fecha_registro = Column(DateTime, default=datetime.datetime.utcnow)

    documentos = relationship("Documento", back_populates="solicitud")


class Documento(Base):
    __tablename__ = "documentos"
    documento_id = Column(Integer, primary_key=True, index=True)
    solicitud_id = Column(Integer, ForeignKey("solicitudes.solicitud_id"))
    tipo_documento = Column(String(100))
    nombre_archivo = Column(String(255))
    pdf_content = Column(LargeBinary)
    # 1 = validado automáticamente, 0 = pendiente de validación manual
    verificado = Column(SmallInteger, default=1)
    observacion = Column(String(255))
    fecha_carga = Column(DateTime, default=datetime.datetime.utcnow)

    solicitud = relationship("Solicitud", back_populates="documentos")


class Conversacion(Base):
    """Una conversación independiente por estudiante y, opcionalmente, por trámite."""

    __tablename__ = "conversaciones"
    conversacion_id = Column(Integer, primary_key=True, index=True)
    estudiante_id = Column(Integer, ForeignKey("usuarios.estudiante_id"), nullable=False, index=True)
    solicitud_id = Column(Integer, ForeignKey("solicitudes.solicitud_id"), nullable=True)
    titulo = Column(String(200), default="Nueva conversación")
    estado = Column(Enum("ACTIVA", "ARCHIVADA", name="estado_conversacion_enum"), default="ACTIVA")
    # Solo las conversaciones con un proceso terminado se muestran en el historial.
    proceso_completado = Column(Boolean, default=False, nullable=False)
    resumen = Column(Text, nullable=True)
    resumen_hasta_id = Column(BigInteger, nullable=True)
    hechos = Column(JSON, default=dict)
    creada_en = Column(DateTime, default=datetime.datetime.utcnow)
    actualizada_en = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    mensajes = relationship(
        "Mensaje",
        back_populates="conversacion",
        cascade="all, delete-orphan",
        order_by="Mensaje.mensaje_id",
    )


class Mensaje(Base):
    """Historial persistente de mensajes (user / assistant / system / tool)."""

    __tablename__ = "mensajes"
    mensaje_id = Column(Integer, primary_key=True, index=True)
    conversacion_id = Column(
        Integer,
        ForeignKey("conversaciones.conversacion_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    rol = Column(Enum("system", "user", "assistant", "tool", name="rol_mensaje_enum"), nullable=False)
    contenido = Column(Text, nullable=False)
    tokens_aprox = Column(Integer, nullable=True)
    meta = Column("metadata", JSON, nullable=True)
    creado_en = Column(DateTime, default=datetime.datetime.utcnow)

    conversacion = relationship("Conversacion", back_populates="mensajes")