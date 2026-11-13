import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import get_password_hash
from app.db.models import Base, ConocimientoNormativo, Programa, Usuario


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def user(db):
    programa = Programa(nombre="Ingeniería de Sistemas", estado=1)
    db.add(programa)
    db.commit()
    db.refresh(programa)

    usuario = Usuario(
        documento="1234567890",
        codigo="EST001",
        nombres="Ana",
        apellidos="Pérez",
        correo="ana@uniespinal.edu.co",
        password=get_password_hash("secreta"),
        programa_id=programa.programa_id,
        rol="ESTUDIANTE",
        estado=1,
    )
    db.add(usuario)
    db.commit()
    db.refresh(usuario)
    return usuario


@pytest.fixture()
def normativa_rules(db):
    """Copia de las reglas reales cargadas en conocimiento_normativo."""
    reglas = [
        dict(id=1, codigo="HOM-001", categoria="NOTA", titulo="Nota mínima para homologación interna",
             descripcion="La nota mínima aprobatoria para homologaciones internas es 3.5.",
             tipo_homologacion="INTERNA", tipo_regla="CONDICION", valor=3.5, operador=">=",
             es_obligatorio=1, articulo="Artículo 41", documento_fuente="Reglamento Estudiantil ITFIP"),
        dict(id=2, codigo="HOM-002", categoria="NOTA", titulo="Nota mínima para homologación externa",
             descripcion="La nota mínima aprobatoria para homologaciones externas es 3.8.",
             tipo_homologacion="EXTERNA", tipo_regla="CONDICION", valor=3.8, operador=">=",
             es_obligatorio=1, articulo="Artículo 41", documento_fuente="Reglamento Estudiantil ITFIP"),
        dict(id=3, codigo="HOM-003", categoria="DOCUMENTACION", titulo="Certificado de calificaciones",
             descripcion="Debe presentar certificado de calificaciones con intensidad horaria, créditos académicos y horas teóricas y prácticas.",
             tipo_homologacion="EXTERNA", tipo_regla="REQUISITO", es_obligatorio=1,
             articulo="Artículo 51", documento_fuente="Reglamento Estudiantil ITFIP"),
        dict(id=4, codigo="HOM-004", categoria="DOCUMENTACION", titulo="Contenidos programáticos",
             descripcion="Debe presentar copia de los contenidos programáticos de los espacios académicos cursados y aprobados.",
             tipo_homologacion="EXTERNA", tipo_regla="REQUISITO", es_obligatorio=1,
             articulo="Artículo 51", documento_fuente="Reglamento Estudiantil ITFIP"),
        dict(id=5, codigo="HOM-005", categoria="DOCUMENTACION", titulo="Constancia de no sanción disciplinaria",
             descripcion="Debe presentar constancia de no haber sido sancionado disciplinariamente.",
             tipo_homologacion="EXTERNA", tipo_regla="REQUISITO", es_obligatorio=1,
             articulo="Artículo 51", documento_fuente="Reglamento Estudiantil ITFIP"),
        dict(id=6, codigo="HOM-006", categoria="DOCUMENTACION", titulo="Comprobante de pago",
             descripcion="Debe presentar el registro del pago correspondiente cuando la homologación sea externa.",
             tipo_homologacion="EXTERNA", tipo_regla="REQUISITO", es_obligatorio=1,
             articulo="Artículo 51", documento_fuente="Reglamento Estudiantil ITFIP"),
        dict(id=7, codigo="HOM-007", categoria="PLAZO", titulo="Anticipación de la solicitud",
             descripcion="La solicitud debe realizarse ocho días hábiles antes del proceso de inscripción.",
             tipo_homologacion="TODAS", tipo_regla="PLAZO", valor=8, operador=">=",
             es_obligatorio=1, articulo="Artículo 51", documento_fuente="Reglamento Estudiantil ITFIP"),
        dict(id=8, codigo="HOM-008", categoria="CREDITOS", titulo="Límite de créditos homologables",
             descripcion="El total de créditos autorizados por homologaciones y convalidaciones no puede superar el 50% de los créditos del plan de estudios.",
             tipo_homologacion="TODAS", tipo_regla="LIMITE", valor=50, operador="<=",
             es_obligatorio=1, articulo="Artículo 51, parágrafo 1", documento_fuente="Reglamento Estudiantil ITFIP"),
        dict(id=9, codigo="HOM-009", categoria="PROCEDIMIENTO", titulo="Solicitud escrita y motivada",
             descripcion="Para transferencia interna se requiere presentar una solicitud escrita y motivada.",
             tipo_homologacion="INTERNA", tipo_regla="REQUISITO", es_obligatorio=1,
             articulo="Artículo 19", documento_fuente="Reglamento Estudiantil ITFIP"),
        dict(id=10, codigo="HOM-010", categoria="PROCEDIMIENTO", titulo="Semestre cursado",
             descripcion="El estudiante debe haber cursado por lo menos un semestre del programa del que desea retirarse.",
             tipo_homologacion="INTERNA", tipo_regla="REQUISITO", valor=1, operador=">=",
             es_obligatorio=1, articulo="Artículo 19", documento_fuente="Reglamento Estudiantil ITFIP"),
        dict(id=11, codigo="HOM-011", categoria="PROCEDIMIENTO", titulo="Materias aprobadas",
             descripcion="Los espacios académicos que se desean homologar deben haber sido aprobados.",
             tipo_homologacion="INTERNA", tipo_regla="REQUISITO", es_obligatorio=1,
             articulo="Artículo 19", documento_fuente="Reglamento Estudiantil ITFIP"),
        dict(id=12, codigo="HOM-012", categoria="EQUIVALENCIA", titulo="Equivalencia de contenidos",
             descripcion="Se deben evaluar los resultados y las equivalencias entre el espacio académico cursado y el espacio académico del plan de estudios.",
             tipo_homologacion="TODAS", tipo_regla="CONDICION", es_obligatorio=1,
             articulo="Artículo 49", documento_fuente="Reglamento Estudiantil ITFIP"),
        dict(id=13, codigo="HOM-013", categoria="EQUIVALENCIA", titulo="Evaluación de intensidad horaria",
             descripcion="El certificado debe identificar la intensidad horaria.",
             tipo_homologacion="TODAS", tipo_regla="INFORMATIVO", es_obligatorio=0,
             articulo="Artículos 38 y 51", documento_fuente="Reglamento Estudiantil ITFIP"),
    ]
    for data in reglas:
        db.add(ConocimientoNormativo(estado="ACTIVO", **data))
    db.commit()
    return reglas


@pytest.fixture()
def other_user(db):
    usuario = Usuario(
        documento="9876543210",
        codigo="EST002",
        nombres="Luis",
        apellidos="Gómez",
        correo="luis@uniespinal.edu.co",
        password=get_password_hash("secreta"),
        rol="ESTUDIANTE",
        estado=1,
    )
    db.add(usuario)
    db.commit()
    db.refresh(usuario)
    return usuario
