import logging

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings

logger = logging.getLogger(__name__)

SQLALCHEMY_DATABASE_URL = (
    f"mysql+pymysql://{settings.DB_USER}:{settings.DB_PASSWORD}"
    f"@{settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}"
)

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    pool_pre_ping=True,      # evita conexiones muertas por timeouts de MySQL
    pool_recycle=1800,       # recicla conexiones cada 30 min
    pool_size=10,
    max_overflow=20,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Crea las tablas que falten y aplica migraciones ligeras compatibles."""
    from app.db import models  # noqa: F401  (registra los modelos en Base)

    models.Base.metadata.create_all(bind=engine)
    _migrar_conversaciones()
    _migrar_solicitudes()
    _migrar_documentos()
    logger.info("Esquema de base de datos verificado/creado correctamente.")


def _migrar_conversaciones() -> None:
    """Agrega columnas nuevas a tablas ya existentes (MySQL no las altera solo)."""
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    if "conversaciones" not in inspector.get_table_names():
        return
    columnas = {c["name"] for c in inspector.get_columns("conversaciones")}
    if "proceso_completado" not in columnas:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "ALTER TABLE conversaciones "
                    "ADD COLUMN proceso_completado TINYINT(1) NOT NULL DEFAULT 0"
                )
            )
        logger.info("Columna conversaciones.proceso_completado agregada.")


def _migrar_solicitudes() -> None:
    """Completa las columnas que el trámite necesita en ``solicitudes``."""
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    if "solicitudes" not in inspector.get_table_names():
        return

    columnas = {c["name"] for c in inspector.get_columns("solicitudes")}
    nuevas = {
        "tipo_homologacion": "VARCHAR(20) NULL",
        "semestre_cursado": "INT NULL",
        "materia_aprobada": "TINYINT(1) NULL",
    }
    for columna, definicion in nuevas.items():
        if columna in columnas:
            continue
        with engine.begin() as conn:
            conn.execute(
                text(f"ALTER TABLE solicitudes ADD COLUMN {columna} {definicion}")
            )
        logger.info("Columna solicitudes.%s agregada.", columna)

    # `pdf_filename` y `pdf_content` duplicaban lo que ya guarda `documentos`.
    # Se eliminan solo si nunca se usaron, para no perder información.
    if {"pdf_filename", "pdf_content"} <= columnas:
        with engine.connect() as conn:
            usados = conn.execute(
                text(
                    "SELECT COUNT(*) FROM solicitudes "
                    "WHERE pdf_filename IS NOT NULL OR pdf_content IS NOT NULL"
                )
            ).scalar()
        if not usados:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "ALTER TABLE solicitudes "
                        "DROP COLUMN pdf_filename, DROP COLUMN pdf_content"
                    )
                )
            logger.info(
                "Columnas duplicadas solicitudes.pdf_filename/pdf_content eliminadas."
            )


def _migrar_documentos() -> None:
    """Agrega el estado de validación de los documentos."""
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    if "documentos" not in inspector.get_table_names():
        return

    columnas = {c["name"] for c in inspector.get_columns("documentos")}
    nuevas = {
        "verificado": "TINYINT(1) NOT NULL DEFAULT 0",
        "observacion": "VARCHAR(255) NULL",
    }
    for columna, definicion in nuevas.items():
        if columna in columnas:
            continue
        with engine.begin() as conn:
            conn.execute(
                text(f"ALTER TABLE documentos ADD COLUMN {columna} {definicion}")
            )
        logger.info("Columna documentos.%s agregada.", columna)
