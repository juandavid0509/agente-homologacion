import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.core.config import settings
from app.db.database import init_db
from app.services import llm

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Crear tablas nuevas si no existen (no altera las existentes).
    try:
        init_db()
    except Exception as exc:
        logger.error("No se pudo inicializar la base de datos: %s", exc)

    estado = llm.health()
    if estado.get("ok") and estado.get("model_available"):
        logger.info("Ollama disponible con el modelo %s", settings.OLLAMA_MODEL)
    else:
        logger.warning(
            "Ollama no está listo (%s). El chat seguirá funcionando con respuestas "
            "deterministas de respaldo.",
            estado,
        )
    yield


app = FastAPI(title="Agente Homologaciones UniEspinal", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")


@app.get("/")
def read_root():
    return {"status": "ok", "message": "Backend funcionando"}


@app.get("/health")
def health():
    from app.db.database import engine

    db_ok = True
    db_error = None
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("SELECT 1")
    except Exception as exc:
        db_ok = False
        db_error = str(exc)

    llm_status = llm.health()
    return {
        "status": "ok" if db_ok and llm_status.get("model_available") else "degraded",
        "database": {"ok": db_ok, "error": db_error},
        "llm": llm_status,
        "model": settings.OLLAMA_MODEL,
    }
