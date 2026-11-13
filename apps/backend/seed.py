"""Crea el usuario administrador inicial.

Uso:
    python seed.py
"""

from app.core.security import get_password_hash
from app.db.database import SessionLocal, init_db
from app.db.models import Usuario


def seed_db():
    init_db()
    db = SessionLocal()
    try:
        correo = "admin@uniespinal.edu.co"
        if not db.query(Usuario).filter(Usuario.correo == correo).first():
            admin = Usuario(
                documento="0000000000",
                codigo="ADMIN001",
                nombres="Administrador",
                apellidos="UniEspinal",
                correo=correo,
                password=get_password_hash("admin123"),
                rol="ADMIN",
                estado=1,
            )
            db.add(admin)
            db.commit()
            print(f"✅ Admin creado: {correo} / admin123")
        else:
            print("ℹ️ El admin ya existe; no se hicieron cambios.")
    finally:
        db.close()


if __name__ == "__main__":
    seed_db()
