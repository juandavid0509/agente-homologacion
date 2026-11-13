import pytest

from app.db.models import Conversacion, Documento, Materia, Mensaje, Solicitud
from app.services import agent, conversation as conv_service, llm, normativa
from app.services import documentos as doc_service, solicitudes


def _fake_llm(captured):
    def fake(messages, **kwargs):
        captured.append(messages)
        return "Respuesta simulada de Lía."
    return fake


def _turn(db, user, conv, text):
    conv_service.add_message(db, conv, "user", text)
    response = agent.generate_response(user, text, db, conv)
    conv_service.add_message(db, conv, "assistant", response)
    db.refresh(conv)
    return response


def test_history_is_sent_to_ollama(db, user, monkeypatch):
    captured = []
    monkeypatch.setattr(agent.llm, "chat", _fake_llm(captured))
    conv = conv_service.create_conversation(db, user)

    _turn(db, user, conv, "Hola, necesito orientación")
    _turn(db, user, conv, "Me gustaría entender mejor mi situación")

    assert len(captured) == 2
    segundo_envio = captured[1]
    contenidos = [m["content"] for m in segundo_envio]

    # El segundo envío debe contener el primer turno completo.
    assert any("Hola, necesito orientación" in c for c in contenidos)
    assert any("Respuesta simulada de Lía." in c for c in contenidos)
    # Y debe terminar con el mensaje actual del estudiante.
    assert segundo_envio[-1] == {
        "role": "user",
        "content": "Me gustaría entender mejor mi situación",
    }


def test_messages_are_persisted_with_roles(db, user, monkeypatch):
    monkeypatch.setattr(agent.llm, "chat", _fake_llm([]))
    conv = conv_service.create_conversation(db, user)
    _turn(db, user, conv, "Necesito orientación")

    mensajes = (
        db.query(Mensaje)
        .filter(Mensaje.conversacion_id == conv.conversacion_id)
        .order_by(Mensaje.mensaje_id.asc())
        .all()
    )
    roles = [m.rol for m in mensajes]
    assert "assistant" in roles and "user" in roles
    assert any(m.contenido == "Necesito orientación" for m in mensajes)


def test_conversations_are_isolated_between_students(db, user, other_user):
    conv = conv_service.create_conversation(db, user)
    with pytest.raises(PermissionError):
        conv_service.get_for_user(db, conv.conversacion_id, other_user.estudiante_id)


def test_context_survives_new_database_session(user, monkeypatch, db):
    """Simula recargar la página: se lee el historial desde la BD, no desde RAM."""
    monkeypatch.setattr(agent.llm, "chat", _fake_llm([]))
    conv = conv_service.create_conversation(db, user)
    _turn(db, user, conv, "Mi materia se llama Cálculo Diferencial")

    historial = conv_service.build_history(db, conv)
    assert any("Cálculo Diferencial" in m["content"] for m in historial)
    assert any(m["role"] == "assistant" for m in historial)


def test_no_false_cancellation_on_common_words(db, user, monkeypatch):
    monkeypatch.setattr(agent.llm, "chat", _fake_llm([]))
    conv = conv_service.create_conversation(db, user)
    conv.hechos = {
        **conv_service.DEFAULT_HECHOS,
        "state": "ASK_TIPO",
        "materia": "Cálculo Diferencial",
        "materia_verificada": True,
    }
    db.add(conv)
    db.commit()

    _turn(db, user, conv, "Necesito preparar los documentos para la solicitud")
    db.refresh(conv)

    # "preparar" contiene "para", pero NO debe reiniciar el trámite.
    assert conv.hechos["state"] == "ASK_TIPO"
    assert conv.hechos["materia"] == "Cálculo Diferencial"


def test_homologable_tristate_when_status_is_null(db, user, monkeypatch):
    monkeypatch.setattr(
        agent.llm, "chat",
        lambda *a, **k: (_ for _ in ()).throw(llm.LLMUnavailable("sin Ollama")),
    )
    materia = Materia(nombre="Cálculo Diferencial", creditos=4, es_homologable=None, estado=1)
    db.add(materia)
    db.commit()

    conv = conv_service.create_conversation(db, user)
    response = _turn(db, user, conv, "¿Es homologable Cálculo Diferencial?")

    assert "no tiene un estado" in response.lower() or "verificar" in response.lower()


def test_ollama_failure_falls_back_without_breaking(db, user, monkeypatch):
    monkeypatch.setattr(
        agent.llm, "chat",
        lambda *a, **k: (_ for _ in ()).throw(llm.LLMUnavailable("sin Ollama")),
    )
    conv = conv_service.create_conversation(db, user)
    conv.hechos = {**conv_service.DEFAULT_HECHOS, "tipo": "EXTERNA"}
    db.add(conv)
    db.commit()
    response = _turn(db, user, conv, "¿Cuáles son los requisitos?")

    assert response.strip()
    assert "no hay reglas normativas" in response.lower() or "no voy a inventar" in response.lower()


def test_empty_message_is_rejected(db, user):
    conv = conv_service.create_conversation(db, user)
    response = agent.generate_response(user, "   ", db, conv)
    assert "pregunta" in response.lower()


def test_document_upload_advances_flow(db, user, monkeypatch, normativa_rules):
    monkeypatch.setattr(agent.llm, "chat", _fake_llm([]))
    conv = conv_service.create_conversation(db, user)
    conv.hechos = {
        **conv_service.DEFAULT_HECHOS,
        "state": "ASK_DOCS",
        "materia": "Cálculo Diferencial",
        "tipo": "EXTERNA",
        "nota": 4.2,
        "creditos": 3,
        "docs_subidos": 0,
    }
    db.add(conv)
    db.commit()

    agent.handle_document_uploaded(user, db, conv, "certificado.pdf", "Certificado de calificaciones")
    agent.handle_document_uploaded(user, db, conv, "contenidos.pdf", "Contenidos programáticos")
    agent.handle_document_uploaded(user, db, conv, "constancia.pdf", "Constancia de no sanción disciplinaria")
    db.refresh(conv)
    assert conv.hechos["docs_subidos"] == 3
    assert conv.hechos["state"] == "ASK_DOCS"

    response = agent.handle_document_uploaded(
        user, db, conv, "pago.pdf", "Comprobante de pago"
    )
    db.refresh(conv)
    assert conv.hechos["docs_subidos"] == 4
    assert conv.hechos["state"] == "REVIEW_SUMMARY"
    assert "Resumen de tu solicitud" in response
    assert "Comprobante de pago" in response


def test_requisitos_se_leen_de_la_base(db, user, normativa_rules):
    conv = conv_service.create_conversation(db, user)
    conv.hechos = {**conv_service.DEFAULT_HECHOS, "tipo": "EXTERNA"}
    db.add(conv)
    db.commit()

    response = _turn(db, user, conv, "¿Qué requisitos hay para la homologación externa?")

    assert "Certificado de calificaciones" in response
    assert "Comprobante de pago" in response
    assert "3.8" in response
    assert "Artículo 51" in response or "Reglamento Estudiantil ITFIP" in response


def test_pregunta_de_tipo_y_detalle_externo(db, user, normativa_rules):
    conv = conv_service.create_conversation(db, user)

    response = _turn(db, user, conv, "¿Cuáles son los requisitos?")
    assert conv.hechos["state"] == "ASK_TIPO_INFO"
    assert "interna" in response.lower() and "externa" in response.lower()
    # Primero pregunta el tipo; no vuelca todos los documentos de golpe.
    assert "Comprobante de pago" not in response
    assert "toda la información" in response.lower()

    response = _turn(db, user, conv, "Es externa")
    assert conv.hechos["state"] == "WAITING_START_CONFIRMATION"
    assert conv.hechos["tipo"] == "EXTERNA"
    assert "Comprobante de pago" in response


def test_materia_homologable_responde_desde_catalogo(db, user, normativa_rules):
    db.add(Materia(nombre="Cálculo Diferencial", creditos=3, es_homologable=1, estado=1))
    db.commit()
    conv = conv_service.create_conversation(db, user)
    conv.hechos = {**conv_service.DEFAULT_HECHOS, "tipo": "EXTERNA"}
    db.add(conv)
    db.commit()

    response = _turn(db, user, conv, "¿Es homologable Cálculo Diferencial?")

    assert "sí figura como homologable" in response.lower() or "figura como homologable" in response.lower()
    assert "3.8" in response


def test_nota_baja_bloquea_homologacion_externa(db, user, normativa_rules):
    conv = conv_service.create_conversation(db, user)
    conv.hechos = {
        **conv_service.DEFAULT_HECHOS,
        "state": "ASK_NOTA",
        "materia": "Cálculo Diferencial",
        "tipo": "EXTERNA",
    }
    db.add(conv)
    db.commit()

    response = _turn(db, user, conv, "Mi nota fue 3.5")
    db.refresh(conv)

    assert conv.hechos["state"] == "ELIGIBILITY_FAILED"
    assert "3.8" in response
    assert "no es posible" in response.lower()


def test_nota_suficiente_continua_el_flujo(db, user, normativa_rules):
    conv = conv_service.create_conversation(db, user)
    conv.hechos = {
        **conv_service.DEFAULT_HECHOS,
        "state": "ASK_NOTA",
        "materia": "Cálculo Diferencial",
        "tipo": "EXTERNA",
    }
    db.add(conv)
    db.commit()

    response = _turn(db, user, conv, "Obtuve 4.2")
    db.refresh(conv)

    assert conv.hechos["state"] == "ASK_CREDITOS"
    assert conv.hechos["nota"] == 4.2
    assert "créditos" in response.lower()


def test_semestre_insuficiente_bloquea_interna(db, user, normativa_rules):
    conv = conv_service.create_conversation(db, user)
    conv.hechos = {
        **conv_service.DEFAULT_HECHOS,
        "state": "ASK_SEMESTRE",
        "materia": "Cálculo Diferencial",
        "tipo": "INTERNA",
        "nota": 4.0,
    }
    db.add(conv)
    db.commit()

    response = _turn(db, user, conv, "0 semestres")
    db.refresh(conv)

    assert conv.hechos["state"] == "ELIGIBILITY_FAILED"
    assert "no es posible" in response.lower()


def test_documentos_dinamicos_por_tipo(db, normativa_rules):
    externos = normativa.documentos_requeridos(db, "EXTERNA")
    internos = normativa.documentos_requeridos(db, "INTERNA")

    assert len(externos) == 4
    assert any(d.titulo == "Comprobante de pago" for d in externos)
    assert len(internos) == 0


def test_flujo_completo_externo(db, user, normativa_rules):
    conv = conv_service.create_conversation(db, user)

    _turn(db, user, conv, "Quiero iniciar mi solicitud de homologación")
    assert conv.hechos["state"] == "ASK_MATERIA"

    _turn(db, user, conv, "Cálculo Diferencial")
    assert conv.hechos["state"] == "ASK_TIPO"

    _turn(db, user, conv, "Es externa")
    assert conv.hechos["state"] == "ASK_NOTA"

    _turn(db, user, conv, "4.2")
    assert conv.hechos["state"] == "ASK_CREDITOS"

    response = _turn(db, user, conv, "3 créditos")
    assert conv.hechos["state"] == "ASK_DOCS"
    assert "Certificado de calificaciones" in response


def test_long_conversation_gets_summarized(db, user, monkeypatch):
    monkeypatch.setattr(conv_service.settings, "SUMMARY_TRIGGER_MESSAGES", 4)
    monkeypatch.setattr(conv_service.settings, "SUMMARY_KEEP_RECENT", 2)
    monkeypatch.setattr(agent.llm, "chat", lambda *a, **k: "RESUMEN GENERADO")

    conv = conv_service.create_conversation(db, user)
    for i in range(6):
        conv_service.add_message(db, conv, "user", f"Pregunta {i}")
        conv_service.add_message(db, conv, "assistant", f"Respuesta {i}")
    db.refresh(conv)

    conv_service.maybe_summarize(db, conv)
    db.refresh(conv)

    assert conv.resumen == "RESUMEN GENERADO"
    assert conv.resumen_hasta_id is not None
    # Los mensajes antiguos siguen persistidos (auditoría).
    assert db.query(Mensaje).filter(Mensaje.conversacion_id == conv.conversacion_id).count() > 2


# ---------------------------------------------------------------------------
# Catálogo de materias: datos reales y búsqueda tolerante
# ---------------------------------------------------------------------------
def _catalogo(db):
    db.add_all(
        [
            Materia(materia_id=1, nombre="Electiva V", creditos=3, es_homologable=1, estado=1),
            Materia(materia_id=2, nombre="Electiva VI", creditos=3, es_homologable=0, estado=1),
            Materia(materia_id=3, nombre="Cálculo Diferencial", creditos=3, es_homologable=1, estado=1),
            Materia(materia_id=4, nombre="Programación O.O.", creditos=3, es_homologable=1, estado=1),
            Materia(materia_id=5, nombre="Estructuras de Inform. I", creditos=3, es_homologable=1, estado=1),
        ]
    )
    db.commit()


def test_electiva_vi_no_se_reporta_como_homologable(db, user):
    _catalogo(db)
    conv = conv_service.create_conversation(db, user)

    response = _turn(db, user, conv, "¿La materia Electiva VI es homologable?")

    assert "Electiva VI" in response
    assert "no figura como homologable" in response.lower()
    assert "no es posible homologarla" in response.lower()
    assert "sí figura" not in response.lower()


def test_flujo_se_detiene_si_materia_no_es_homologable(db, user):
    _catalogo(db)
    conv = conv_service.create_conversation(db, user)

    _turn(db, user, conv, "Quiero iniciar mi solicitud de homologación")
    response = _turn(db, user, conv, "Electiva VI")
    db.refresh(conv)

    assert conv.hechos["state"] == "ELIGIBILITY_FAILED"
    assert conv.hechos["materia"] == "Electiva VI"
    assert "no figura como homologable" in response.lower()
    assert "no es posible" in response.lower()


def test_busqueda_tolera_errores_numeros_y_abreviaturas(db):
    _catalogo(db)

    assert agent._find_materia(db, "electiva 6").nombre == "Electiva VI"
    assert agent._find_materia(db, "electiva VI").nombre == "Electiva VI"
    assert agent._find_materia(db, "electiva sexta").nombre == "Electiva VI"
    assert agent._find_materia(db, "electiva v").nombre == "Electiva V"
    assert agent._find_materia(db, "calculo diferncial").nombre == "Cálculo Diferencial"
    assert agent._find_materia(db, "programacion orientada a objetos").nombre == "Programación O.O."
    assert agent._find_materia(db, "prog oo").nombre == "Programación O.O."
    assert agent._find_materia(db, "estructuras inform 1").nombre == "Estructuras de Inform. I"
    # "electiva" sin versión es ambigua: es mejor preguntar que adivinar.
    assert agent._find_materia(db, "electiva") is None


def test_requisitos_tienen_formato_legible(db, user, normativa_rules):
    conv = conv_service.create_conversation(db, user)
    conv.hechos = {**conv_service.DEFAULT_HECHOS, "tipo": "EXTERNA"}
    db.add(conv)
    db.commit()

    response = _turn(db, user, conv, "¿Qué requisitos hay para la homologación externa?")

    assert "_(condición" not in response
    assert ">=" not in response
    assert "🎯 **Condiciones académicas**" in response
    assert "📄 **Documentos que debes entregar (4)**" in response
    assert "1. **Certificado de calificaciones** —" in response
    assert "**Nota mínima: 3.8**" in response


def test_toda_la_informacion_da_panorama_general(db, user, normativa_rules):
    conv = conv_service.create_conversation(db, user)

    response = _turn(db, user, conv, "Dame toda la información")

    assert "panorama general" in response.lower()
    assert "Homologación interna" in response
    assert "Homologación externa" in response
    assert "3.5" in response and "3.8" in response
    assert conv.hechos["state"] == "WAITING_START_CONFIRMATION"


# ---------------------------------------------------------------------------
# Historial: solo procesos terminados y chats eliminables
# ---------------------------------------------------------------------------
def test_conversacion_solo_aparece_en_historial_al_completar(db, user, normativa_rules):
    conv = conv_service.create_conversation(db, user)
    assert conv_service.list_conversations(db, user.estudiante_id) == []

    _turn(db, user, conv, "Quiero iniciar mi solicitud de homologación")
    _turn(db, user, conv, "Cálculo Integral")
    _turn(db, user, conv, "Es interna")
    _turn(db, user, conv, "4.2")
    _turn(db, user, conv, "2 semestres")
    _turn(db, user, conv, "Sí, ya la aprobé")
    _turn(db, user, conv, "3 créditos")
    db.refresh(conv)
    assert conv.hechos["state"] == "REVIEW_SUMMARY"
    assert conv.proceso_completado is False

    response = _turn(db, user, conv, "Sí, todo correcto")
    db.refresh(conv)

    assert conv.hechos["state"] == "SUBMITTED"
    assert conv.proceso_completado is True
    assert "en espera de aprobación" in response.lower()
    historial = conv_service.list_conversations(db, user.estudiante_id)
    assert [c.conversacion_id for c in historial] == [conv.conversacion_id]
    assert conv.titulo.lower().startswith("homologación interna")

    from app.db.models import Solicitud

    solicitud = (
        db.query(Solicitud)
        .filter(Solicitud.solicitud_id == conv.solicitud_id)
        .first()
    )
    assert solicitud is not None
    assert solicitud.estado == 1
    assert solicitud.materia_origen == "Cálculo Integral"
    assert solicitud.creditos_aportados == 3
    assert solicitud.nota_aportada == 4.2
    assert solicitud.semestre_cursado == 2
    assert solicitud.materia_aprobada == 1
    assert solicitud.tipo_homologacion == "INTERNA"
    assert solicitud.rule_id == "HOM-001"


def test_eliminar_conversacion_borra_sus_mensajes(db, user):
    conv = conv_service.create_conversation(db, user)
    conv_id = conv.conversacion_id
    conv_service.add_message(db, conv, "user", "Mensaje de prueba")

    conv_service.delete_conversation(db, conv_id, user.estudiante_id)

    assert db.query(Conversacion).filter(Conversacion.conversacion_id == conv_id).first() is None
    assert db.query(Mensaje).filter(Mensaje.conversacion_id == conv_id).count() == 0


def test_eliminar_chat_descarta_el_borrador_y_sus_documentos(db, user):
    conv = conv_service.create_conversation(db, user)
    conv_id = conv.conversacion_id
    documento = solicitudes.guardar_documento(
        db,
        conv,
        tipo_documento="Certificado de calificaciones",
        nombre_archivo="x.pdf",
        contenido=b"x",
    )
    solicitud_id = documento.solicitud_id

    conv_service.delete_conversation(db, conv_id, user.estudiante_id)

    assert db.query(Solicitud).filter(Solicitud.solicitud_id == solicitud_id).first() is None
    assert db.query(Documento).filter(Documento.solicitud_id == solicitud_id).count() == 0


def test_eliminar_chat_conserva_una_solicitud_radicada(db, user):
    conv = conv_service.create_conversation(db, user)
    hechos = {
        **conv_service.DEFAULT_HECHOS,
        "state": "SUBMITTED",
        "materia": "Cálculo Integral",
        "tipo": "INTERNA",
        "nota": 4.2,
        "creditos": 3,
        "semestre": 2,
        "aprobada": True,
    }
    solicitud = solicitudes.sincronizar(db, conv, hechos, finalizar=True)

    conv_service.delete_conversation(db, conv.conversacion_id, user.estudiante_id)

    assert (
        db.query(Solicitud)
        .filter(Solicitud.solicitud_id == solicitud.solicitud_id)
        .first()
        is not None
    )


# ---------------------------------------------------------------------------
# Correcciones del estudiante
# ---------------------------------------------------------------------------
def test_corrige_tipo_en_medio_del_flujo(db, user, normativa_rules):
    conv = conv_service.create_conversation(db, user)
    conv.hechos = {
        **conv_service.DEFAULT_HECHOS,
        "state": "ASK_NOTA",
        "materia": "Cálculo Diferencial",
        "tipo": "EXTERNA",
    }
    db.add(conv)
    db.commit()

    response = _turn(db, user, conv, "vale, pero esta materia es interna")
    db.refresh(conv)

    assert conv.hechos["tipo"] == "INTERNA"
    assert conv.hechos["state"] == "ASK_NOTA"
    assert "actualicé el tipo a **interna**" in response.lower()
    assert "nota" in response.lower()


def test_cambio_de_nota_revalida_la_viabilidad(db, user, normativa_rules):
    conv = conv_service.create_conversation(db, user)
    conv.hechos = {
        **conv_service.DEFAULT_HECHOS,
        "state": "ASK_CREDITOS",
        "materia": "Cálculo Diferencial",
        "tipo": "EXTERNA",
        "nota": 3.6,
    }
    db.add(conv)
    db.commit()

    response = _turn(db, user, conv, "me equivoqué, la materia es interna")
    db.refresh(conv)

    # Con 3.6 sí cumple la nota interna (3.5); sigue con los semestres.
    assert conv.hechos["tipo"] == "INTERNA"
    assert conv.hechos["state"] == "ASK_SEMESTRE"
    assert "semestres" in response.lower()


def test_corregir_nota_en_medio_del_flujo_retoma_el_paso_pendiente(
    db, user, normativa_rules
):
    conv = conv_service.create_conversation(db, user)
    conv.hechos = {
        **conv_service.DEFAULT_HECHOS,
        "state": "ASK_CREDITOS",
        "materia": "Cálculo Diferencial",
        "tipo": "INTERNA",
        "nota": 4.2,
        "semestre": 2,
        "aprobada": True,
    }
    db.add(conv)
    db.commit()

    response = _turn(db, user, conv, "me equivoqué, la nota es 4.5")
    db.refresh(conv)

    assert conv.hechos["nota"] == 4.5
    # Continúa en el paso que estaba pendiente, no salta al resumen.
    assert conv.hechos["state"] == "ASK_CREDITOS"
    assert "créditos" in response.lower()


def test_cambiar_tipo_descarta_documentos_de_la_modalidad_anterior(
    db, user, normativa_rules
):
    conv = conv_service.create_conversation(db, user)
    conv.hechos = {
        **conv_service.DEFAULT_HECHOS,
        "state": "ASK_DOCS",
        "materia": "Cálculo Integral",
        "tipo": "EXTERNA",
        "nota": 4.2,
        "creditos": 3,
        "documentos": [
            {
                "tipo": "Certificado de calificaciones",
                "archivo": "x.pdf",
                "verificado": True,
            }
        ],
        "docs_subidos": 1,
    }
    db.add(conv)
    db.commit()
    solicitudes.guardar_documento(
        db,
        conv,
        tipo_documento="Certificado de calificaciones",
        nombre_archivo="x.pdf",
        contenido=b"x",
    )

    response = _turn(db, user, conv, "me equivoqué, esta materia es interna")
    db.refresh(conv)

    assert conv.hechos["tipo"] == "INTERNA"
    assert conv.hechos["documentos"] == []
    assert conv.hechos["state"] == "ASK_SEMESTRE"
    assert "descarté" in response.lower()
    assert (
        db.query(Documento)
        .filter(Documento.solicitud_id == conv.solicitud_id)
        .count()
        == 0
    )


def test_eligibility_failed_se_reanuda_al_corregir_el_tipo(db, user, normativa_rules):
    conv = conv_service.create_conversation(db, user)
    conv.hechos = {
        **conv_service.DEFAULT_HECHOS,
        "state": "ELIGIBILITY_FAILED",
        "materia": "Cálculo Diferencial",
        "tipo": "EXTERNA",
        "nota": 3.6,
        "motivo_no_viable": "con 3.6 no se cumple la nota mínima de 3.8",
    }
    db.add(conv)
    db.commit()

    response = _turn(db, user, conv, "me equivoqué, esta materia es interna")
    db.refresh(conv)

    assert conv.hechos["tipo"] == "INTERNA"
    assert conv.hechos["state"] == "ASK_SEMESTRE"
    assert "actualicé el tipo" in response.lower()
    assert "semestres" in response.lower()


# ---------------------------------------------------------------------------
# Documentos: validación, reemplazo y resumen
# ---------------------------------------------------------------------------
def test_verificacion_pdf_contra_datos_del_estudiante(monkeypatch, user):
    monkeypatch.setattr(
        doc_service,
        "extraer_texto",
        lambda pdf: "Certificado de notas de Ana Pérez, documento 1234567890",
    )
    valido = doc_service.verificar_documento(b"%PDF-1.4", user, "Certificado de calificaciones")
    assert valido["ok"] is True
    assert valido["identidad"] == "documento"

    monkeypatch.setattr(
        doc_service,
        "extraer_texto",
        lambda pdf: "Factura de compra numero 0034903, total 120000",
    )
    invalido = doc_service.verificar_documento(b"%PDF-1.4", user, "Comprobante de pago")
    assert invalido["ok"] is False
    assert "no coincide con el tuyo" in invalido["motivo"].lower()

    # Un escaneo sin texto no se rechaza: pasa a validación manual.
    monkeypatch.setattr(doc_service, "extraer_texto", lambda pdf: "")
    escaneo = doc_service.verificar_documento(b"%PDF-1.4", user, "Comprobante de pago")
    assert escaneo["ok"] is True
    assert escaneo["identidad"] == "sin_texto"


def test_pdf_de_mas_de_5mb_se_rechaza(monkeypatch, user):
    grande = b"x" * (doc_service.MAX_PDF_BYTES + 1)
    resultado = doc_service.verificar_documento(grande, user, "Certificado de calificaciones")
    assert resultado["ok"] is False
    assert "5 MB" in resultado["motivo"]


def test_reemplazo_de_documento_conserva_los_demas(db, user, normativa_rules):
    conv = conv_service.create_conversation(db, user)
    conv.hechos = {
        **conv_service.DEFAULT_HECHOS,
        "state": "ASK_DOCS",
        "materia": "Cálculo Diferencial",
        "tipo": "EXTERNA",
        "nota": 4.2,
        "creditos": 3,
        "documentos": [
            {
                "tipo": "Certificado de calificaciones",
                "archivo": "viejo.pdf",
                "verificado": True,
            }
        ],
        "docs_subidos": 1,
    }
    db.add(conv)
    db.commit()

    response = _turn(db, user, conv, "me equivoqué, subí mal el certificado")
    db.refresh(conv)
    assert conv.hechos["reemplazar"] == "Certificado de calificaciones"
    assert conv.hechos["state"] == "ASK_DOCS"
    assert "reemplazo el anterior" in response.lower()

    agent.handle_document_uploaded(
        user, db, conv, "nuevo.pdf", "Certificado de calificaciones", documento_id=99
    )
    db.refresh(conv)
    assert len(conv.hechos["documentos"]) == 1
    assert conv.hechos["documentos"][0]["archivo"] == "nuevo.pdf"
    assert conv.hechos["reemplazar"] is None


def test_reemplazo_desde_resumen_pregunta_si_hay_algo_mas(db, user, normativa_rules):
    conv = conv_service.create_conversation(db, user)
    conv.hechos = {
        **conv_service.DEFAULT_HECHOS,
        "state": "REVIEW_SUMMARY",
        "materia": "Cálculo Diferencial",
        "tipo": "EXTERNA",
        "nota": 4.2,
        "creditos": 3,
        "documentos": [
            {
                "tipo": "Certificado de calificaciones",
                "archivo": "viejo.pdf",
                "verificado": True,
            }
        ],
        "docs_subidos": 1,
    }
    db.add(conv)
    db.commit()

    _turn(db, user, conv, "me equivoqué, subí mal el certificado")
    db.refresh(conv)
    assert conv.hechos["reemplazar"] == "Certificado de calificaciones"
    assert conv.hechos["state"] == "ASK_DOCS"

    response = agent.handle_document_uploaded(
        user, db, conv, "nuevo.pdf", "Certificado de calificaciones", documento_id=99
    )
    db.refresh(conv)
    assert conv.hechos["state"] == "ASK_UPDATES"
    assert conv.hechos["reemplazar"] is None
    assert "algo más" in response.lower()
    assert "Resumen de tu solicitud" in response


def test_guardar_documento_reemplaza_la_version_anterior(db, user):
    conv = conv_service.create_conversation(db, user)
    primero = solicitudes.guardar_documento(
        db, conv,
        tipo_documento="Certificado de calificaciones",
        nombre_archivo="uno.pdf",
        contenido=b"uno",
    )
    segundo = solicitudes.guardar_documento(
        db, conv,
        tipo_documento="Certificado de calificaciones",
        nombre_archivo="dos.pdf",
        contenido=b"dos",
    )

    filas = (
        db.query(Documento)
        .filter(Documento.solicitud_id == primero.solicitud_id)
        .all()
    )
    assert len(filas) == 1
    assert filas[0].documento_id == segundo.documento_id
    assert filas[0].nombre_archivo == "dos.pdf"


def test_resumen_actualiza_datos_y_luego_registra(db, user, normativa_rules):
    conv = conv_service.create_conversation(db, user)
    _turn(db, user, conv, "Quiero iniciar mi solicitud de homologación")
    _turn(db, user, conv, "Cálculo Integral")
    _turn(db, user, conv, "Es interna")
    _turn(db, user, conv, "4.2")
    _turn(db, user, conv, "2 semestres")
    _turn(db, user, conv, "Sí, ya la aprobé")
    _turn(db, user, conv, "3 créditos")
    db.refresh(conv)
    assert conv.hechos["state"] == "REVIEW_SUMMARY"

    response = _turn(db, user, conv, "No, la nota es 4.7")
    db.refresh(conv)
    assert conv.hechos["state"] == "ASK_UPDATES"
    assert conv.hechos["nota"] == 4.7
    assert "actualicé la nota" in response.lower()
    assert "algo más" in response.lower()

    response = _turn(db, user, conv, "No, nada más")
    db.refresh(conv)
    assert conv.hechos["state"] == "SUBMITTED"
    solicitud = (
        db.query(Solicitud)
        .filter(Solicitud.solicitud_id == conv.solicitud_id)
        .first()
    )
    assert solicitud.estado == 1
    assert solicitud.nota_aportada == 4.7
