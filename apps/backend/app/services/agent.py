"""Agente conversacional Lía — versión con motor normativo.

El agente combina:
- LLM local (llama3.2:3b) para diálogo natural y dudas,
- base de datos MySQL (`conocimiento_normativo`, `materias`) para requisitos,
  documentos, plazos y condiciones,
- un flujo guiado que evalúa si el estudiante cumple la normativa y le dice
  con claridad y empatía si la homologación es viable o no.
"""

import difflib
import logging
import re
import unicodedata
from typing import Optional

from sqlalchemy.orm import Session

from app.db.models import Conversacion, Documento, Materia, Solicitud, Usuario
from app.services import conversation as conv_service
from app.services import documentos as doc_service
from app.services import llm, normativa, solicitudes
from app.services.prompts import build_system_prompt

logger = logging.getLogger(__name__)

CANCEL_WORDS = [
    "cancelar", "cancela", "reiniciar", "reinicia", "detente", "detener",
    "olvidalo", "abortar", "stop",
]
HOMOLOGABLE_WORDS = [
    "homologable", "se puede homologar", "es valida", "es valido",
    "sirve para homologar", "se homologa",
]
GREETING_WORDS = ["hola", "buenos dias", "buenas tardes", "buenas noches", "que tal"]
START_WORDS = [
    "iniciar", "empezar", "comenzar", "radicar", "quiero homologar",
    "crear solicitud", "hacer la solicitud", "solicitar homologacion",
]

# Señales de que el estudiante está corrigiendo un dato ya registrado.
CORRECTION_CUES = [
    "me equivoque", "equivocado", "en realidad", "pero", "cambia", "cambiar",
    "corrige", "corregir", "actualiza", "actualizar", "no es", "era",
    "la materia es", "pertenece", "reemplaza", "reemplazar",
]

UPDATE_STATES = {"REVIEW_SUMMARY", "ASK_UPDATES"}

INFO_PATTERNS = [
    r"\brequisitos?\b",
    r"\bpasos?\b",
    r"que necesito",
    r"que se necesita",
    r"que documentos",
    r"documentos (necesito|requiere|requieren|debo|deberia)",
    r"como es el proceso",
    r"como funciona (el|la) (proceso|homologacion)",
    r"informacion (sobre|del|de la) (proceso|homologacion)",
    r"informacion (completa|general)",
    r"toda la informacion",
    r"dame (todo|toda)",
    r"explicame (todo|toda)",
    r"\bplazos?\b",
    r"cuanto (tiempo|tarda)",
    r"\bcostos?\b",
    r"\barancel\b",
]

ALL_INFO_WORDS = [
    "toda la informacion", "informacion completa", "informacion general",
    "todo el proceso", "toda la informacion completa", "dame todo",
    "dame toda", "explicame todo", "explicame toda", "completa", "completo",
    "ambas", "ambos", "los dos", "no se", "no estoy seguro", "no estoy segura",
    "cualquiera", "general",
]

# El estudiante puede consultar el estado de SUS trámites en cualquier momento.
STATUS_PATTERNS = [
    r"\bestado\b.*\b(mi|mis)\b.*\b(solicitud|tramite|proceso|homologacion|radicado)\b",
    r"\b(mi|mis)\b.*\b(solicitud|tramite|proceso|homologacion)\b.*\bestado\b",
    r"\bcomo\s+(va|van|esta|estan)\b.*\b(mi|mis)\b.*\b(solicitud|tramite|proceso|homologacion)\b",
    r"\ben\s+que\s+(va|estado)\b.*\b(mi|mis)\b.*\b(solicitud|tramite|proceso|homologacion)\b",
    r"\bconsultar\b.*\b(estado|solicitud|tramite|radicado)\b",
    r"\bestado\s+de\s+(la\s+)?(homologacion|solicitud|radicacion)\b",
    r"\bresultado\s+de\s+(mi|la)\s+(solicitud|homologacion|radicacion)\b",
    r"\bya\s+(fue|esta|salio)\s+(aprobada|rechazada)\b",
]

# Estados de una solicitud en `solicitudes.estado`.
ESTADOS_SOLICITUD = {
    0: ("Borrador (sin radicar)", "📝", "Aún puedes completarlo o ajustarlo en el chat."),
    1: ("Pendiente de aprobación", "⏳", "La oficina de homologaciones la está revisando."),
    2: ("Aprobada", "✅", "La homologación fue aprobada."),
    3: ("Rechazada", "❌", "La homologación no fue aprobada."),
}

ROMANOS = {
    "i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6,
    "vii": 7, "viii": 8, "ix": 9, "x": 10, "xi": 11, "xii": 12,
}
NUMEROS_PALABRA = {
    "uno": 1, "una": 1, "primero": 1, "primera": 1,
    "dos": 2, "segundo": 2, "segunda": 2,
    "tres": 3, "tercero": 3, "tercera": 3,
    "cuatro": 4, "cuarto": 4, "cuarta": 4,
    "cinco": 5, "quinto": 5, "quinta": 5,
    "seis": 6, "sexto": 6, "sexta": 6,
    "siete": 7, "septimo": 7, "septima": 7,
    "ocho": 8, "octavo": 8, "octava": 8,
    "nueve": 9, "noveno": 9, "novena": 9,
    "diez": 10, "decimo": 10, "decima": 10,
    "once": 11, "doce": 12,
}

ABREVIATURAS = [
    (r"\bo\.?\s*o\.?(?=\s|$)", "orientada a objetos"),
    (r"\boo\b", "orientada a objetos"),
    (r"\bprog\b", "programacion"),
    (r"\bing\b", "ingenieria"),
    (r"\binform\b", "informacion"),
    (r"\bbd\b", "base de datos"),
    (r"\btics?\b", "tecnologia de la informacion y comunicacion"),
]

MATERIA_STOP = {
    "materia", "materias", "asignatura", "asignaturas", "espacio", "academico",
    "academica", "curso", "de", "del", "la", "el", "los", "las", "un", "una",
    "y", "o", "a", "en", "para", "por", "con", "que", "no", "es", "son",
}

STOP_WORDS = {
    "la", "el", "los", "las", "de", "del", "para", "por", "materia",
    "asignatura", "quiero", "saber", "si", "es", "una", "un", "me",
    "solicitar", "homologar", "homologacion", "puedes", "podrias",
    "revisar", "consultar", "sistema", "que", "como", "esta", "este",
    "mi", "en", "y", "a", "al", "se", "puede", "valida", "valido",
    "sirve", "favor", "decir", "dime", "necesito", "hola", "porfa",
    "porfavor", "gracias",
}

FLOW_STATES = {
    "ASK_MATERIA", "ASK_TIPO", "ASK_NOTA", "ASK_SEMESTRE", "ASK_APROBADA",
    "ASK_CREDITOS", "ASK_DOCS", "REVIEW_SUMMARY", "ASK_UPDATES",
}

GENERIC_FALLBACK = (
    "Qué pena contigo, en este momento no puedo generar la respuesta con el modelo "
    "local. Pero sigo aquí para acompañarte: puedo mostrarte los requisitos, revisar "
    "una materia o iniciar tu solicitud. ¿Qué te gustaría hacer?"
)


# ---------------------------------------------------------------------------
# Utilidades de texto
# ---------------------------------------------------------------------------
def _normalize(text: str) -> str:
    text = (text or "").lower().strip()
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", text)


def _has_any(normalized_text: str, words) -> bool:
    for word in words:
        w = _normalize(word)
        if re.search(rf"(?<!\w){re.escape(w)}(?!\w)", normalized_text):
            return True
    return False


def _is_info_request(normalized_text: str) -> bool:
    return any(re.search(pattern, normalized_text) for pattern in INFO_PATTERNS)


def _wants_all_info(normalized_text: str) -> bool:
    return any(_normalize(word) in normalized_text for word in ALL_INFO_WORDS)


def _is_status_request(normalized_text: str) -> bool:
    return any(re.search(pattern, normalized_text) for pattern in STATUS_PATTERNS)


def _tipo_mencionado(normalized_text: str) -> Optional[str]:
    """Tipo de homologación que el estudiante nombra en su mensaje."""
    if _has_any(normalized_text, ["interna", "interno"]):
        return "INTERNA"
    if _has_any(normalized_text, ["externa", "externo"]):
        return "EXTERNA"
    return None


def _looks_like_question(text: str) -> bool:
    norm = _normalize(text)
    if "?" in text or "¿" in text:
        return True
    return bool(
        re.match(
            r"^(que|como|cual|cuales|cuando|donde|quien|cuanto|cuanta|por que|para que|puedo|podria|debo|necesito)\b",
            norm,
        )
    )


def _is_affirmative(normalized_text: str) -> bool:
    if re.match(r"^(si|claro|dale|vale|confirmo|listo|hagamoslo|ok|okay)\b", normalized_text):
        return True
    return any(p in normalized_text for p in ["de acuerdo", "por supuesto", "esta bien"])


def _is_negative(normalized_text: str) -> bool:
    if re.match(r"^(no|luego|despues|gracias|ahora no|todavia no)\b", normalized_text):
        return True
    return "ahora no" in normalized_text or "todavia no" in normalized_text


def _extract_subject(text: str) -> str:
    clean = text.lower()
    clean = re.sub(r"[¿?¡!.,;:()\"']", " ", clean)
    for phrase in HOMOLOGABLE_WORDS:
        clean = re.sub(rf"(?<!\w){re.escape(phrase)}(?!\w)", " ", clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    tokens = [t for t in clean.split() if t not in STOP_WORDS]
    return " ".join(tokens).strip()


def _parse_decimal(text: str) -> Optional[float]:
    match = re.search(r"\d+(?:[.,]\d+)?", text or "")
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", "."))
    except ValueError:
        return None


def _parse_int(text: str) -> Optional[int]:
    match = re.search(r"\d+", text or "")
    return int(match.group(0)) if match else None


# ---------------------------------------------------------------------------
# Materias: búsqueda tolerante a errores de escritura
# ---------------------------------------------------------------------------
def _canon_tokens(text: str) -> list:
    """Normaliza nombres de materias: tildes, abreviaturas y números romanos."""
    limpio = _normalize(text)
    for patron, reemplazo in ABREVIATURAS:
        limpio = re.sub(patron, reemplazo, limpio)
    limpio = re.sub(r"[^a-z0-9# ]+", " ", limpio)

    tokens = []
    for token in limpio.split():
        if token in ROMANOS:
            tokens.append(f"#{ROMANOS[token]}")
        elif token in NUMEROS_PALABRA:
            tokens.append(f"#{NUMEROS_PALABRA[token]}")
        elif token.isdigit():
            tokens.append(f"#{int(token)}")
        elif token not in MATERIA_STOP:
            tokens.append(token)
    return tokens


def _similitud_tokens(consulta: list, candidato: list) -> float:
    if not consulta or not candidato:
        return 0.0
    total = 0.0
    for token in consulta:
        total += max(
            difflib.SequenceMatcher(None, token, otro).ratio() for otro in candidato
        )
    return total / len(consulta)


def _score_materia(consulta: list, candidato: list) -> float:
    """Puntaje 0..1. Nunca mezcla versiones distintas (Electiva V vs VI)."""
    numeros_consulta = [t for t in consulta if t.startswith("#")]
    numeros_candidato = [t for t in candidato if t.startswith("#")]
    if numeros_consulta and numeros_candidato and numeros_consulta != numeros_candidato:
        return 0.0

    conjunto_consulta = set(consulta)
    conjunto_candidato = set(candidato)
    union = conjunto_consulta | conjunto_candidato
    interseccion = conjunto_consulta & conjunto_candidato

    jaccard = len(interseccion) / len(union) if union else 0.0
    contencion = (
        len(interseccion) / min(len(conjunto_consulta), len(conjunto_candidato))
        if interseccion
        else 0.0
    )
    secuencia = difflib.SequenceMatcher(
        None, " ".join(consulta), " ".join(candidato)
    ).ratio()
    tokens = _similitud_tokens(consulta, candidato)

    puntaje = max(jaccard, contencion * 0.95, secuencia, tokens * 0.97)

    # El estudiante no indicó versión, pero la materia sí la tiene: no es exacto.
    if numeros_candidato and not numeros_consulta:
        puntaje = min(puntaje, 0.90)
    # Consultas demasiado cortas frente al nombre completo: evitar falsos positivos.
    if len(consulta) + 1 < len(candidato):
        puntaje = min(puntaje, 0.88)
    return puntaje


def _materias_rankeadas(db: Session, subject_query: str) -> list:
    """Devuelve [(materia, puntaje)] ordenado de mayor a menor."""
    consulta = _canon_tokens(subject_query)
    if not consulta:
        return []

    materias = db.query(Materia).all()
    if not materias:
        return []

    resultados = []
    for materia in materias:
        if not materia.nombre:
            continue
        puntaje = _score_materia(consulta, _canon_tokens(materia.nombre))
        if puntaje > 0:
            resultados.append((materia, puntaje))

    resultados.sort(key=lambda item: item[1], reverse=True)
    return resultados


def _find_materia(db: Session, subject_query: str) -> Optional[Materia]:
    """Mejor coincidencia solo si es suficientemente clara y no ambigua."""
    resultados = _materias_rankeadas(db, subject_query)
    if not resultados:
        return None

    mejor, puntaje = resultados[0]
    if puntaje < 0.86:
        return None

    # Si hay otra materia casi igual de probable, es mejor preguntar que adivinar.
    if len(resultados) > 1:
        segundo, puntaje_segundo = resultados[1]
        if (
            segundo.materia_id != mejor.materia_id
            and puntaje_segundo >= puntaje - 0.02
        ):
            return None
    return mejor


def _suggest_materias(db: Session, subject_query: str, limit: int = 3) -> list:
    """Sugerencias para cuando no hay una coincidencia segura."""
    sugerencias = []
    vistos = set()
    for materia, puntaje in _materias_rankeadas(db, subject_query):
        if puntaje < 0.55 or materia.materia_id in vistos:
            continue
        vistos.add(materia.materia_id)
        sugerencias.append(materia.nombre)
        if len(sugerencias) >= limit:
            break
    return sugerencias


def _materia_estado(materia: Optional[Materia]) -> str:
    if materia is None:
        return (
            "no aparece registrada con ese nombre, así que **debemos verificarlo** "
            "con la coordinación u oficina de homologaciones"
        )
    if materia.es_homologable == 1:
        return "**sí figura como homologable** en el catálogo institucional"
    if materia.es_homologable == 0:
        return "**no figura como homologable** en el catálogo institucional"
    return (
        "está registrada, pero **no tiene un estado de homologabilidad definido**; "
        "esto debe verificarse con la oficina correspondiente"
    )


def _materia_context(materia: Optional[Materia], subject: str) -> str:
    if materia is None:
        return (
            f"Búsqueda en el catálogo de materias para '{subject}': sin coincidencias. "
            "No afirmes que es homologable ni que no lo es: indica que debe verificarse "
            "y pide el nombre exacto o el código de la materia."
        )
    estado = _materia_estado(materia)
    return (
        f"Catálogo de materias: **{materia.nombre}** (créditos registrados: "
        f"{materia.creditos}). Estado: {estado}. "
        "Este estado es un dato del registro, no una decisión de homologación. "
        "No contradigas este dato ni lo cambies."
    )


# ---------------------------------------------------------------------------
# Documentos y preguntas del flujo
# ---------------------------------------------------------------------------
def _documentos(db: Session, tipo: Optional[str]):
    return normativa.documentos_requeridos(db, tipo)


def _norm_doc(texto: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _normalize(texto or "")).strip()


def _documentos_hechos(hechos: dict) -> list:
    return list(hechos.get("documentos") or [])


def _documentos_faltantes(db: Session, hechos: dict) -> list:
    requeridos = _documentos(db, hechos.get("tipo"))
    presentes = {_norm_doc(d.get("tipo")) for d in _documentos_hechos(hechos)}
    return [r.titulo for r in requeridos if _norm_doc(r.titulo) not in presentes]


def _siguiente_documento(db: Session, hechos: dict) -> Optional[str]:
    reemplazar = hechos.get("reemplazar")
    if reemplazar:
        return reemplazar
    faltantes = _documentos_faltantes(db, hechos)
    return faltantes[0] if faltantes else None


def _problema_viabilidad(db: Session, hechos: dict) -> Optional[str]:
    """Revisa nota/semestre/aprobada con los datos actuales (sin inventar nada)."""
    tipo = hechos.get("tipo")
    nota = hechos.get("nota")
    if nota is not None:
        evaluacion = normativa.evaluar_nota(db, tipo, nota)
        if evaluacion and not evaluacion["cumple"]:
            return (
                f"con **{nota:g}** no se cumple la nota mínima de "
                f"**{evaluacion['minimo']:g}** para la homologación "
                f"{(tipo or '').lower()} ({evaluacion['fuente']})"
            )

    if tipo == "INTERNA":
        semestre = hechos.get("semestre")
        if semestre is not None:
            evaluacion = normativa.evaluar_semestre(db, tipo, semestre)
            if evaluacion and not evaluacion["cumple"]:
                return (
                    f"la normativa interna exige al menos "
                    f"**{evaluacion['minimo']:g} semestre(s)** y registras "
                    f"**{semestre}**"
                )
        if hechos.get("aprobada") is False:
            return (
                "la normativa exige que la materia esté **aprobada** antes de "
                "homologarla"
            )

    return None


def _siguiente_paso(db: Session, user: Usuario, hechos: dict) -> str:
    """Deja el flujo en el siguiente paso pendiente y devuelve la pregunta."""
    nombre = user.nombres or "estudiante"

    if not hechos.get("materia"):
        hechos["state"] = "ASK_MATERIA"
        return _preguntar_materia(user)

    if not hechos.get("tipo"):
        hechos["state"] = "ASK_TIPO"
        return (
            f"¿La homologación de **{hechos['materia']}** es **interna** "
            "(misma institución) o **externa** (otra institución)?"
        )

    problema = _problema_viabilidad(db, hechos)
    if problema:
        hechos["state"] = "ELIGIBILITY_FAILED"
        hechos["motivo_no_viable"] = problema
        return (
            f"Con los datos actuales **no es posible** continuar: {problema}. "
            "Si fue un error al escribirlo, corrígeme el dato y lo revisamos de nuevo."
        )

    tipo = hechos["tipo"]
    condicion = normativa.nota_minima(db, tipo)
    if condicion and hechos.get("nota") is None:
        hechos["state"] = "ASK_NOTA"
        return (
            f"Para **homologación {tipo.lower()}**, {_nota_texto(condicion)}. "
            "**¿Cuál fue la nota que obtuviste en la materia?**"
        )

    if tipo == "INTERNA" and hechos.get("semestre") is None:
        hechos["state"] = "ASK_SEMESTRE"
        return f"**¿Cuántos semestres has cursado** en tu programa, {nombre}?"

    if tipo == "INTERNA" and hechos.get("aprobada") is None:
        hechos["state"] = "ASK_APROBADA"
        return "**¿Ya aprobaste la materia que deseas homologar?**"

    if hechos.get("creditos") is None:
        hechos["state"] = "ASK_CREDITOS"
        return f"**¿Cuántos créditos tiene {hechos.get('materia')}?**"

    faltantes = _documentos_faltantes(db, hechos)
    if faltantes:
        hechos["state"] = "ASK_DOCS"
        total = len(_documentos(db, tipo))
        entregados = total - len(faltantes)
        return (
            f"📎 Sube con el clip **{faltantes[0]}**. "
            f"Llevas {entregados} de {total} documentos."
        )

    hechos["state"] = "REVIEW_SUMMARY"
    return (
        f"{_resumen_texto(db, user, hechos)}\n\n"
        "**¿Está todo correcto?** Si deseas cambiar algo, dímelo; si está bien, "
        "responde **sí** y envío tu solicitud."
    )


def _resumen_texto(db: Session, user: Usuario, hechos: dict) -> str:
    tipo = (hechos.get("tipo") or "").upper()
    lineas = ["📝 **Resumen de tu solicitud**", ""]
    lineas.append(f"• **Materia:** {hechos.get('materia') or '—'}")
    lineas.append(f"• **Tipo:** homologación {tipo.lower() or '—'}")
    if hechos.get("nota") is not None:
        lineas.append(f"• **Nota obtenida:** {hechos['nota']:g}")
    if tipo == "INTERNA":
        if hechos.get("semestre") is not None:
            lineas.append(f"• **Semestres cursados:** {hechos['semestre']}")
        if hechos.get("aprobada") is not None:
            lineas.append(
                f"• **Materia aprobada:** {'Sí' if hechos['aprobada'] else 'No'}"
            )
    if hechos.get("creditos") is not None:
        lineas.append(f"• **Créditos:** {hechos['creditos']}")

    documentos = _documentos_hechos(hechos)
    if documentos:
        lineas.append(f"• **Documentos adjuntos ({len(documentos)}):**")
        for indice, documento in enumerate(documentos, 1):
            nota = "" if documento.get("verificado", True) else " (validación manual)"
            lineas.append(
                f"  {indice}. {documento.get('tipo')} — {documento.get('archivo')}{nota}"
            )
    else:
        lineas.append("• **Documentos:** no se requieren para este tipo.")

    lineas.append("")
    lineas.append(
        "Si algo no coincide, dímelo (por ejemplo: «la nota es 4.5» o "
        "«la materia es interna»)."
    )
    return "\n".join(lineas)


def _pregunta_pendiente(db: Session, hechos: dict) -> Optional[str]:
    state = hechos.get("state")
    if state == "ASK_MATERIA":
        return "¿qué materia deseas homologar?"
    if state == "ASK_TIPO":
        return "¿la homologación es interna o externa?"
    if state == "ASK_NOTA":
        return "¿cuál fue la nota que obtuviste en la materia?"
    if state == "ASK_SEMESTRE":
        return "¿cuántos semestres has cursado en tu programa?"
    if state == "ASK_APROBADA":
        return "¿ya aprobaste la materia que deseas homologar?"
    if state == "ASK_CREDITOS":
        return "¿cuántos créditos tiene la materia?"
    if state == "ASK_DOCS":
        siguiente = _siguiente_documento(db, hechos)
        return f"¿puedes subir **{siguiente}** con el clip 📎?" if siguiente else None
    if state == "REVIEW_SUMMARY":
        return "¿confirmas que el resumen está correcto?"
    if state == "ASK_UPDATES":
        return "¿hay algo más que quieras actualizar?"
    return None


def _merge_hechos(conv: Conversacion) -> dict:
    hechos = dict(conv_service.DEFAULT_HECHOS)
    hechos.update(conv.hechos or {})
    return hechos


def _reset_flow(hechos: dict) -> dict:
    hechos.update(
        state="IDLE",
        materia=None,
        materia_id=None,
        materia_ingresada=None,
        materia_verificada=False,
        tipo=None,
        creditos=None,
        nota=None,
        semestre=None,
        aprobada=None,
        docs_subidos=0,
        documentos=[],
        reemplazar=None,
        solicitud_id=None,
        motivo_no_viable=None,
        _return_state=None,
    )
    return hechos


# ---------------------------------------------------------------------------
# Núcleo
# ---------------------------------------------------------------------------
def _respond(
    user: Usuario,
    db: Session,
    conv: Conversacion,
    hechos: dict,
    *,
    directiva: Optional[str] = None,
    fallback: Optional[str] = None,
    grounded: Optional[str] = None,
    extra_system: Optional[str] = None,
    current_text: Optional[str] = None,
    use_llm: bool = True,
) -> str:
    conv_service.save_hechos(db, conv, hechos)

    # Solo se guarda en el historial cuando el trámite llegó a su fin.
    if hechos.get("state") in ("FINISHED", "SUBMITTED") and not conv.proceso_completado:
        conv_service.mark_completed(db, conv, hechos)

    if not use_llm:
        return fallback or GENERIC_FALLBACK

    bloques = [build_system_prompt(user, conv, hechos)]
    if grounded:
        bloques.append("## Información confirmada del sistema\n" + grounded)
    if extra_system:
        bloques.append("## Evento del sistema\n" + extra_system)
    if directiva:
        bloques.append("## Instrucción para este turno\n" + directiva)

    messages = [{"role": "system", "content": "\n\n".join(bloques)}]
    messages.extend(conv_service.build_history(db, conv))

    if current_text:
        already_included = (
            messages
            and messages[-1].get("role") == "user"
            and messages[-1].get("content") == current_text
        )
        if not already_included:
            messages.append({"role": "user", "content": current_text})

    try:
        return llm.chat(messages)
    except llm.LLMUnavailable as exc:
        logger.warning("Ollama no disponible (%s). Se usa respuesta determinista.", exc)
        return fallback or GENERIC_FALLBACK


def _preguntar_materia(user: Usuario) -> str:
    nombre = user.nombres or "estudiante"
    return (
        f"¡Con mucha gusto, {nombre}! Para empezar, **¿qué materia deseas homologar?** "
        "Puedes escribir el nombre como lo recuerdes: si tiene algún error de escritura, "
        "yo la busco por ti."
    )


def _nota_texto(condicion: Optional[dict]) -> str:
    if not condicion:
        return "No hay una nota mínima registrada para este tipo de homologación."
    return (
        f"la nota mínima registrada es **{condicion['valor']:g}** "
        f"({condicion['fuente']})"
    )


def _plazo_recordatorio(db: Session, tipo: Optional[str]) -> str:
    plazo = normativa.resumen_plazo(db, tipo)
    if not plazo:
        return ""
    plazo = plazo.strip()
    plazo = plazo[0].lower() + plazo[1:]
    if not plazo.endswith("."):
        plazo += "."
    return f" Recuerda que {plazo}"


def _datos_completos(db: Session, hechos: dict) -> bool:
    tipo = hechos.get("tipo")
    if not hechos.get("materia") or not tipo:
        return False
    condicion = normativa.nota_minima(db, tipo)
    if condicion and hechos.get("nota") is None:
        return False
    if tipo == "INTERNA":
        if hechos.get("semestre") is None or hechos.get("aprobada") is None:
            return False
    if hechos.get("creditos") is None:
        return False
    return not _documentos_faltantes(db, hechos)


def _finalizar(user: Usuario, db: Session, conv: Conversacion, hechos: dict) -> str:
    """Registra la solicitud ya confirmada y cierra el trámite."""
    nombre = user.nombres or "estudiante"
    solicitud = solicitudes.sincronizar(db, conv, hechos, finalizar=True)
    hechos["solicitud_id"] = solicitud.solicitud_id
    hechos["state"] = "SUBMITTED"

    return _respond(
        user, db, conv, hechos,
        fallback=(
            f"✅ **¡Solicitud registrada, {nombre}!**\n\n"
            f"Tu trámite de homologación **{(hechos.get('tipo') or '').lower()}** de "
            f"**{hechos.get('materia')}** quedó **en espera de aprobación** por la "
            "oficina de homologaciones. Guardamos tus datos y documentos en el sistema.\n\n"
            "📚 La decisión final corresponde a la institución y te informarán por los "
            "canales oficiales. Puedes consultar el trámite en tu historial.\n\n"
            "¿Te ayudo con algo más?"
        ),
        use_llm=False,
        current_text="confirmacion",
    )


def _detectar_cambio_tipo(
    text: str, norm: str, hechos: dict, state: str
) -> Optional[str]:
    """Detecta frases como «vale, pero esta materia es interna»."""
    if state in ("ASK_TIPO", "ASK_TIPO_INFO") or not hechos.get("tipo"):
        return None
    if state not in FLOW_STATES and state != "ELIGIBILITY_FAILED":
        return None
    if _looks_like_question(text):
        return None
    # No confundir una consulta de requisitos/nota con una corrección.
    if any(
        palabra in norm
        for palabra in ("nota", "minima", "requisito", "documento", "plazo", "credito")
    ):
        return None

    nuevo = None
    if _has_any(norm, ["interna", "interno"]):
        nuevo = "INTERNA"
    elif _has_any(norm, ["externa", "externo"]):
        nuevo = "EXTERNA"
    if not nuevo:
        return None

    if not any(cue in norm for cue in CORRECTION_CUES) and len(norm.split()) > 6:
        return None
    return nuevo


def _cambiar_tipo(
    user: Usuario,
    db: Session,
    conv: Conversacion,
    hechos: dict,
    nuevo: str,
    state: str,
) -> str:
    nombre = user.nombres or "estudiante"
    anterior = (hechos.get("tipo") or "").upper()

    if anterior == nuevo:
        pregunta = _siguiente_paso(db, user, hechos)
        return _respond(
            user, db, conv, hechos,
            fallback=(
                f"Tranquilo, {nombre}: ya lo tengo registrado como **{nuevo.lower()}**. "
                f"{pregunta}"
            ),
            use_llm=False,
            current_text="correccion tipo",
        )

    hechos["tipo"] = nuevo
    hechos["reemplazar"] = None

    descartados = 0
    if _documentos_hechos(hechos):
        descartados = solicitudes.descartar_documentos(db, conv)
        hechos["documentos"] = []
        hechos["docs_subidos"] = 0

    aviso = (
        f"Gracias por corregirme, {nombre}. Actualicé el tipo a **{nuevo.lower()}**"
    )
    if anterior:
        aviso += f" (antes figuraba como {anterior.lower()})"
    if descartados:
        aviso += (
            f" y descarté {descartados} documento(s) de la modalidad anterior, "
            "porque ya no aplican"
        )
    aviso += "."

    problema = _problema_viabilidad(db, hechos)
    if problema:
        hechos["state"] = "ELIGIBILITY_FAILED"
        hechos["motivo_no_viable"] = problema
        return _respond(
            user, db, conv, hechos,
            fallback=(
                f"{aviso}\n\nCon **{nuevo.lower()}** no es posible continuar: "
                f"{problema}. Si algún dato está mal, corrígeme y lo revisamos."
            ),
            use_llm=False,
            current_text="correccion tipo",
        )

    pregunta = _siguiente_paso(db, user, hechos)
    return _respond(
        user, db, conv, hechos,
        fallback=f"{aviso}\n\n{pregunta}",
        use_llm=False,
        current_text="correccion tipo",
    )


def _documento_mencionado(db: Session, norm: str, hechos: dict) -> Optional[str]:
    """Devuelve el tipo de documento al que se refiere el estudiante."""
    documentos = _documentos_hechos(hechos)
    if not documentos:
        return None

    palabras = set(norm.split())
    mejor = None
    mejor_puntaje = 0
    for documento in documentos:
        tokens = [t for t in _canon_tokens(documento.get("tipo") or "") if len(t) >= 4]
        coincidencias = sum(1 for t in tokens if t in palabras)
        if coincidencias > mejor_puntaje:
            mejor = documento.get("tipo")
            mejor_puntaje = coincidencias
    if mejor:
        return mejor
    # Sin mención explícita: se asume el último documento entregado.
    return documentos[-1].get("tipo")


def _detectar_documento_equivocado(
    db: Session, norm: str, hechos: dict, state: str
) -> Optional[str]:
    if state not in ("ASK_DOCS", "REVIEW_SUMMARY", "ASK_UPDATES"):
        return None
    if not _documentos_hechos(hechos):
        return None
    palabras = set(norm.split())
    menciona_tipo = any(
        any(t in palabras for t in _canon_tokens(d.get("tipo") or "") if len(t) >= 4)
        for d in _documentos_hechos(hechos)
    )
    menciona_archivo = any(w in norm for w in ("documento", "archivo", "pdf"))
    if not menciona_archivo and not menciona_tipo:
        return None
    if not any(
        w in norm
        for w in (
            "equivoc", "mal", "cambiar", "cambie", "reemplaz", "otro",
            "actualiz", "corrig", "no era",
        )
    ):
        return None
    return _documento_mencionado(db, norm, hechos)


def _aplicar_correcciones(
    db: Session, norm: str, hechos: dict, state: str
) -> list:
    """Actualiza los datos que el estudiante corrige en el resumen."""
    tiene_cue = any(cue in norm for cue in CORRECTION_CUES)
    if state not in UPDATE_STATES and state != "ELIGIBILITY_FAILED" and not tiene_cue:
        return []

    cambios = []

    patron_nota = (
        r"(?:nota|calificacion)[^0-9]{0,25}(\d(?:[.,]\d)?)"
    )
    coincidencia = re.search(patron_nota, norm)
    if not coincidencia:
        coincidencia = re.search(r"(\d(?:[.,]\d)?)\s*(?:de|en)\s*(?:la\s*)?nota", norm)
    if not coincidencia and "nota" in norm:
        numeros = re.findall(r"\b\d(?:[.,]\d)?\b", norm)
        if len(numeros) == 1:
            coincidencia = re.match(r".*?(\d(?:[.,]\d)?)", norm)
    if coincidencia:
        valor = float(coincidencia.group(1).replace(",", "."))
        if 0 <= valor <= 5 and hechos.get("nota") != valor:
            hechos["nota"] = valor
            cambios.append(f"la nota a **{valor:g}**")

    coincidencia = re.search(r"creditos?[^0-9]{0,20}(\d{1,2})", norm) or re.search(
        r"(\d{1,2})\s*creditos?", norm
    )
    if coincidencia:
        valor = int(coincidencia.group(1))
        if 0 < valor <= 60 and hechos.get("creditos") != valor:
            hechos["creditos"] = valor
            cambios.append(f"los créditos a **{valor}**")

    coincidencia = re.search(r"semestres?[^0-9]{0,20}(\d{1,2})", norm) or re.search(
        r"(\d{1,2})\s*semestres?", norm
    )
    if coincidencia:
        valor = int(coincidencia.group(1))
        if 0 <= valor <= 30 and hechos.get("semestre") != valor:
            hechos["semestre"] = valor
            cambios.append(f"los semestres a **{valor}**")

    coincidencia = re.search(
        r"(?:materia|asignatura)\s+(?:es|era|sera|se llama|corresponde a)\s+(.+)",
        norm,
    )
    if coincidencia:
        consulta = coincidencia.group(1).strip()
        materia = _find_materia(db, consulta)
        nombre_materia = materia.nombre if materia else consulta[:150]
        if nombre_materia and hechos.get("materia") != nombre_materia:
            hechos["materia"] = nombre_materia
            hechos["materia_id"] = materia.materia_id if materia else None
            hechos["materia_verificada"] = bool(materia)
            hechos["materia_ingresada"] = consulta[:150]
            cambios.append(f"la materia a **{nombre_materia}**")
            if materia and materia.es_homologable == 0:
                hechos["state"] = "ELIGIBILITY_FAILED"
                hechos["motivo_no_viable"] = (
                    f"La materia {materia.nombre} figura como NO homologable en el "
                    "catálogo institucional."
                )
                return cambios

    if "aprob" in norm:
        if any(p in norm for p in ("no la he aprobado", "no la aprobe", "aun no", "no aprobe")):
            if hechos.get("aprobada") is not False:
                hechos["aprobada"] = False
                cambios.append("la materia como **no aprobada**")
        elif any(p in norm for p in ("ya la aprobe", "si la aprobe", "si, la aprobe", "aprobada")):
            if hechos.get("aprobada") is not True:
                hechos["aprobada"] = True
                cambios.append("la materia como **aprobada**")

    if not cambios:
        return []

    problema = _problema_viabilidad(db, hechos)
    if problema:
        hechos["state"] = "ELIGIBILITY_FAILED"
        hechos["motivo_no_viable"] = problema
        return cambios

    return cambios


def _estado_solicitudes_texto(
    db: Session, user: Usuario, hechos: dict
) -> str:
    """Resumen legible del estado de las solicitudes **del propio estudiante**.

    La consulta siempre se filtra por ``estudiante_id``: un estudiante nunca
    puede ver el trámite de otra persona.
    """
    nombre = user.nombres or "estudiante"
    solicitudes_usuario = (
        db.query(Solicitud)
        .filter(Solicitud.estudiante_id == user.estudiante_id)
        .order_by(Solicitud.fecha_registro.desc(), Solicitud.solicitud_id.desc())
        .all()
    )

    if not solicitudes_usuario:
        texto = (
            f"Aún no tienes solicitudes registradas, {nombre}. "
            "Cuando radiques una homologación podré mostrarte aquí su estado."
        )
        pendiente = _pregunta_pendiente(db, hechos)
        if pendiente:
            texto += f"\n\nRetomemos: {pendiente}"
        return texto

    lineas = ["📋 **Estado de tus solicitudes**", ""]
    for solicitud in solicitudes_usuario[:5]:
        etiqueta, emoji, detalle = ESTADOS_SOLICITUD.get(
            int(solicitud.estado or 0),
            ("Estado desconocido", "❔", "Consulta con la oficina de homologaciones."),
        )
        materia = solicitud.materia_origen or "Materia sin registrar"
        tipo = (
            f" · homologación {solicitud.tipo_homologacion.lower()}"
            if solicitud.tipo_homologacion
            else ""
        )
        lineas.append(f"{emoji} **{materia}**{tipo}")
        lineas.append(f"• **Estado:** {etiqueta}")
        if solicitud.fecha_registro:
            lineas.append(
                f"• **Registrada el:** {solicitud.fecha_registro.strftime('%d/%m/%Y')}"
            )
        if solicitud.nota_aportada is not None:
            lineas.append(f"• **Nota aportada:** {solicitud.nota_aportada:g}")
        documentos = (
            db.query(Documento)
            .filter(Documento.solicitud_id == solicitud.solicitud_id)
            .count()
        )
        if documentos:
            lineas.append(f"• **Documentos adjuntos:** {documentos}")
        lineas.append(f"• {detalle}")
        lineas.append("")

    lineas.append(
        "🔒 Esta información es privada: solo tú puedes consultar tus trámites. "
        "La decisión final corresponde a la institución."
    )

    pendiente = _pregunta_pendiente(db, hechos)
    if pendiente:
        lineas.append("")
        lineas.append(f"Retomemos: {pendiente}")
    return "\n".join(lineas)


def generate_response(
    user: Usuario,
    message: str,
    db: Session,
    conversation: Optional[Conversacion] = None,
) -> str:
    """Genera la respuesta de Lía para un mensaje del estudiante."""
    if user is None:
        return "No pude identificar tu usuario. Vuelve a iniciar sesión, por favor."

    if conversation is None:
        conversation = conv_service.get_or_create(db, user)

    text = (message or "").strip()
    if not text:
        return "¿Me compartes tu pregunta? Estoy aquí para ayudarte con gusto."

    hechos = _merge_hechos(conversation)
    norm = _normalize(text)
    nombre = user.nombres or "estudiante"

    # 1) Cancelación explícita.
    if _has_any(norm, CANCEL_WORDS):
        hechos = _reset_flow(hechos)
        return _respond(
            user, db, conversation, hechos,
            fallback=(
                f"Entendido, {nombre}. Cancelé el proceso actual y no se guardó ningún "
                "avance. Aquí estaré cuando quieras retomarlo. ¿Te ayudo con otra cosa?"
            ),
            use_llm=False,
            current_text=text,
        )

    # 1b) El estudiante consulta el estado de SUS trámites. La consulta se
    #     filtra por su propio id: nunca se muestra información de otra persona.
    if _is_status_request(norm):
        return _respond(
            user, db, conversation, hechos,
            fallback=_estado_solicitudes_texto(db, user, hechos),
            use_llm=False,
            current_text=text,
        )

    # 2) ¿La materia X es homologable? → consulta real al catálogo.
    if _has_any(norm, HOMOLOGABLE_WORDS):
        subject = _extract_subject(text)
        materia = _find_materia(db, subject) if subject else None

        if materia:
            if materia.es_homologable == 1:
                respuesta = (
                    f"¡Buenas noticias, {nombre}! Revisé el catálogo institucional y "
                    f"**{materia.nombre}** sí figura como homologable "
                    f"(registra {materia.creditos} créditos)."
                )
                condicion = normativa.nota_minima(db, hechos.get("tipo"))
                if condicion:
                    respuesta += (
                        f" Para {normativa.etiqueta_tipo(hechos.get('tipo'))}, "
                        f"{_nota_texto(condicion)}."
                    )
                respuesta += (
                    " Recuerda que este dato es orientativo: la decisión final de "
                    "homologación la toma la institución."
                )
            elif materia.es_homologable == 0:
                respuesta = (
                    f"Gracias por preguntar, {nombre}. Revisé el catálogo institucional "
                    f"y **{materia.nombre}** no figura como homologable "
                    f"(registra {materia.creditos} créditos).\n\n"
                    "Por eso, **no es posible homologarla** por esa vía. Si crees que se "
                    "trata de un error del registro, la oficina de homologaciones puede "
                    "verificarlo contigo."
                )
            else:
                respuesta = (
                    f"Revisé el catálogo institucional, {nombre}. **{materia.nombre}** "
                    f"está registrada (registra {materia.creditos} créditos), pero "
                    "**no tiene un estado de homologabilidad definido**. Eso debe "
                    "confirmarlo la oficina de homologaciones."
                )
        else:
            sugerencias = _suggest_materias(db, subject or text)
            pista = ""
            if sugerencias:
                pista = " ¿Quizás te refieres a " + ", ".join(
                    f"**{s}**" for s in sugerencias
                ) + "?"
            respuesta = (
                f"Gracias por preguntar, {nombre}. No encontré una coincidencia segura "
                f"para «{subject or text}» en el catálogo.{pista} "
                "Si ninguna es la materia, la validación corresponde a la oficina de "
                "homologaciones."
            )

        respuesta += (
            "\n\n¿Quieres que revisemos los requisitos o que iniciemos tu solicitud?"
        )
        if hechos.get("state") not in FLOW_STATES:
            hechos["state"] = "WAITING_START_CONFIRMATION"
        return _respond(
            user, db, conversation, hechos,
            grounded=_materia_context(materia, subject or text),
            fallback=respuesta,
            use_llm=False,
            current_text=text,
        )

    # 3) Requisitos / proceso → SIEMPRE desde conocimiento_normativo.
    if _is_info_request(norm):
        tipo_guardado = hechos.get("tipo")
        tipo_consulta = _tipo_mencionado(norm) or tipo_guardado
        state = hechos.get("state")

        # 3a. "Dame toda la información" → panorama general en lenguaje claro.
        #     Si además nombra un tipo, se responde por ese tipo (más abajo).
        if _wants_all_info(norm) and not _tipo_mencionado(norm):
            texto = normativa.requisitos_generales_texto(db)
            if state in FLOW_STATES:
                pendiente = _pregunta_pendiente(db, hechos)
                respuesta = f"Con mucho gusto, {nombre}. {texto}"
                if pendiente:
                    respuesta += f"\n\nRetomemos: {pendiente}"
            else:
                hechos["state"] = "WAITING_START_CONFIRMATION"
                respuesta = (
                    f"Con mucho gusto, {nombre}. {texto}\n\n"
                    "¿Deseas que iniciemos tu solicitud de homologación ahora?"
                )
            return _respond(
                user, db, conversation, hechos,
                fallback=respuesta,
                use_llm=False,
                current_text=text,
            )

        # 3b. Dentro de un trámite activo: responder por el tipo consultado
        #     (aunque sea distinto al del trámite) y retomar donde íbamos.
        if state in FLOW_STATES:
            if not tipo_consulta:
                hechos["_return_state"] = state
                hechos["state"] = "ASK_TIPO_INFO"
                respuesta = (
                    f"Claro que sí, {nombre}. Para darte el detalle exacto, primero "
                    "cuéntame: **¿tu homologación es interna o externa?**\n\n"
                    "• **Interna**: la materia la cursaste en la misma institución.\n"
                    "• **Externa**: la cursaste en otra institución.\n\n"
                    "Después de que me digas, retomamos tu solicitud donde íbamos."
                )
                return _respond(
                    user, db, conversation, hechos,
                    fallback=respuesta,
                    use_llm=False,
                    current_text=text,
                )

            texto = normativa.requisitos_texto(db, tipo_consulta)
            pendiente = _pregunta_pendiente(db, hechos)
            if tipo_guardado and tipo_consulta != tipo_guardado:
                respuesta = (
                    f"Claro que sí, {nombre}. Te muestro la información de la "
                    f"homologación **{tipo_consulta.lower()}**, que es la que consultas.\n\n"
                    f"{texto}"
                )
            else:
                respuesta = f"Claro que sí, {nombre}. {texto}"
            if pendiente:
                respuesta += f"\n\nRetomemos: {pendiente}"
            return _respond(
                user, db, conversation, hechos,
                fallback=respuesta,
                use_llm=False,
                current_text=text,
            )

        # 3c. Sin tipo: primero preguntar interna/externa (no volcar todo de golpe).
        if not tipo_consulta:
            hechos["state"] = "ASK_TIPO_INFO"
            respuesta = (
                f"Con gusto te explico, {nombre}. Para darte la información exacta, "
                "**¿tu homologación es interna o externa?**\n\n"
                "• **Interna**: la materia la cursaste en la misma institución.\n"
                "• **Externa**: la cursaste en otra institución.\n\n"
                "Si prefieres, dime **«toda la información»** y te muestro el panorama "
                "completo de ambos casos."
            )
            return _respond(
                user, db, conversation, hechos,
                fallback=respuesta,
                use_llm=False,
                current_text=text,
            )

        # 3d. Se consulta (y se recuerda) el tipo preguntado, para que las
        #     siguientes preguntas respondan por esa misma modalidad.
        hechos["tipo"] = tipo_consulta
        hechos["state"] = "WAITING_START_CONFIRMATION"
        texto = normativa.requisitos_texto(db, tipo_consulta)
        respuesta = (
            f"Con mucho gusto, {nombre}. {texto}\n\n"
            "¿Deseas que iniciemos tu solicitud de homologación ahora?"
        )
        return _respond(
            user, db, conversation, hechos,
            fallback=respuesta,
            use_llm=False,
            current_text=text,
        )

    state = hechos.get("state", "IDLE")

    # 3d) Correcciones del estudiante: «esta materia es interna»,
    #     «me equivoqué de documento», «la nota es 4.5»...
    nuevo_tipo = _detectar_cambio_tipo(text, norm, hechos, state)
    if nuevo_tipo:
        return _cambiar_tipo(user, db, conversation, hechos, nuevo_tipo, state)

    tipo_equivocado = _detectar_documento_equivocado(db, norm, hechos, state)
    if tipo_equivocado:
        hechos["reemplazar"] = tipo_equivocado
        if state in UPDATE_STATES:
            # Tras reemplazar, se vuelve al resumen para preguntar si hay algo más.
            hechos["_return_state"] = "ASK_UPDATES"
        hechos["state"] = "ASK_DOCS"
        return _respond(
            user, db, conversation, hechos,
            fallback=(
                f"Claro que sí, {nombre}. Sube de nuevo **{tipo_equivocado}** con el "
                "clip 📎 y reemplazo el anterior. Los demás documentos que ya "
                "entregaste se conservan."
            ),
            use_llm=False,
            current_text=text,
        )

    cambios = _aplicar_correcciones(db, norm, hechos, state)
    if cambios:
        detalle = ", ".join(cambios)
        if hechos.get("state") == "ELIGIBILITY_FAILED":
            motivo = hechos.get("motivo_no_viable") or "no se cumplen las condiciones"
            return _respond(
                user, db, conversation, hechos,
                fallback=(
                    f"Listo, {nombre}. Actualicé {detalle}. Sin embargo, con esos datos "
                    f"**no es posible** continuar: {motivo}. Si otra cifra está mal, "
                    "corrígeme y la revisamos con gusto."
                ),
                use_llm=False,
                current_text=text,
            )

        # En el resumen se pregunta si hay algo más; en medio del trámite se
        # retoma el paso pendiente, para no perder el hilo de la conversación.
        if state in UPDATE_STATES:
            hechos["state"] = "ASK_UPDATES"
            resumen = _resumen_texto(db, user, hechos)
            return _respond(
                user, db, conversation, hechos,
                fallback=(
                    f"Listo, {nombre}. Actualicé {detalle}.\n\n{resumen}\n\n"
                    "**¿Hay algo más que quieras actualizar?**"
                ),
                use_llm=False,
                current_text=text,
            )

        # También cubre ELIGIBILITY_FAILED: si la corrección resolvió el
        # problema, `_siguiente_paso` reanuda el trámite; si no, vuelve a
        # explicar el motivo.
        pregunta = _siguiente_paso(db, user, hechos)
        return _respond(
            user, db, conversation, hechos,
            fallback=f"Listo, {nombre}. Actualicé {detalle}.\n\n{pregunta}",
            use_llm=False,
            current_text=text,
        )

    # 4) Tras preguntar por el tipo, mostrar la información que corresponde.
    if state == "ASK_TIPO_INFO":
        if _wants_all_info(norm):
            hechos["state"] = "WAITING_START_CONFIRMATION"
            return _respond(
                user, db, conversation, hechos,
                fallback=(
                    f"Con mucho gusto, {nombre}. "
                    f"{normativa.requisitos_generales_texto(db)}\n\n"
                    "¿Deseas que iniciemos tu solicitud de homologación ahora?"
                ),
                use_llm=False,
                current_text=text,
            )

        if _has_any(norm, ["interna", "interno"]):
            hechos["tipo"] = "INTERNA"
        elif _has_any(norm, ["externa", "externo"]):
            hechos["tipo"] = "EXTERNA"
        else:
            return _respond(
                user, db, conversation, hechos,
                directiva=(
                    "Pide con amabilidad que el estudiante elija entre homologación "
                    "interna o externa, o que pida 'toda la información'."
                ),
                fallback=(
                    f"Para mostrarte la información exacta, {nombre}, "
                    "¿la homologación es **interna** o **externa**? "
                    "También puedo darte el panorama completo si me dices "
                    "**«toda la información»**."
                ),
                use_llm=_looks_like_question(text),
                current_text=text,
            )

        texto = normativa.requisitos_texto(db, hechos["tipo"])
        return_state = hechos.pop("_return_state", None)
        if return_state:
            hechos["state"] = return_state
            pendiente = _pregunta_pendiente(db, hechos)
            respuesta = f"Perfecto, {nombre}. {texto}"
            if pendiente:
                respuesta += f"\n\nRetomemos: {pendiente}"
        else:
            hechos["state"] = "WAITING_START_CONFIRMATION"
            respuesta = (
                f"Perfecto, {nombre}. {texto}\n\n"
                "¿Quieres que iniciemos tu solicitud ahora?"
            )
        return _respond(
            user, db, conversation, hechos,
            fallback=respuesta,
            use_llm=False,
            current_text=text,
        )

    # 5) Flujo guiado.
    if state == "ASK_MATERIA":
        if _looks_like_question(text):
            return _respond(
                user, db, conversation, hechos,
                directiva=(
                    "Responde la duda con calidez y, antes de terminar, recuérdale que "
                    "necesitas el nombre de la materia para continuar."
                ),
                fallback=(
                    f"Claro, {nombre}, te ayudo con eso. Para continuar con la solicitud, "
                    "dime el nombre de la materia como lo recuerdes; yo la busco por ti."
                ),
                current_text=text,
            )

        subject = _extract_subject(text)
        materia = _find_materia(db, subject)
        hechos["materia_ingresada"] = subject[:150] or text[:150]
        if materia:
            hechos["materia"] = materia.nombre
            hechos["materia_id"] = materia.materia_id
            hechos["materia_verificada"] = True
            acuse = f"¡Genial, {nombre}! Registré **{materia.nombre}**."
        else:
            hechos["materia"] = text[:150]
            hechos["materia_id"] = None
            hechos["materia_verificada"] = False
            sugerencias = _suggest_materias(db, subject or text)
            pista = ""
            if sugerencias:
                pista = " ¿Quizás te refieres a " + ", ".join(
                    f"**{s}**" for s in sugerencias
                ) + "?"
            acuse = (
                f"Gracias, {nombre}. Registré «{hechos['materia']}» y la dejaré "
                f"**pendiente de verificación**, porque no encontré una coincidencia "
                f"exacta.{pista}"
            )

        # Si el catálogo dice explícitamente que NO es homologable, no seguimos.
        if materia and materia.es_homologable == 0:
            hechos["state"] = "ELIGIBILITY_FAILED"
            hechos["motivo_no_viable"] = (
                f"La materia {materia.nombre} figura como NO homologable en el "
                "catálogo institucional."
            )
            return _respond(
                user, db, conversation, hechos,
                grounded=_materia_context(materia, subject),
                fallback=(
                    f"Gracias por contarme, {nombre}. Revisé el catálogo institucional "
                    f"y **{materia.nombre}** **no figura como homologable**, así que por "
                    "ahora **no es posible** homologarla.\n\n"
                    "Si crees que se trata de un error del registro, la oficina de "
                    "homologaciones puede verificarlo contigo. ¿Quieres revisar otra "
                    "materia o te muestro los requisitos?"
                ),
                use_llm=False,
                current_text=text,
            )

        hechos["state"] = "ASK_TIPO"

        if hechos.get("tipo"):
            # Ya conocemos el tipo (por ejemplo, tras una consulta de requisitos).
            condicion = normativa.nota_minima(db, hechos["tipo"])
            hechos["state"] = "ASK_NOTA" if condicion else "ASK_CREDITOS"
            resumen = normativa.resumen_tipo(db, hechos["tipo"])
            pregunta = (
                "**¿Cuál fue la nota que obtuviste en la materia?**"
                if condicion
                else f"**¿Cuántos créditos tiene {hechos.get('materia')}?**"
            )
            return _respond(
                user, db, conversation, hechos,
                grounded=_materia_context(materia, subject),
                fallback=(
                    f"{acuse} Ya tengo registrado que es **{hechos['tipo'].lower()}**. "
                    f"{resumen}\n\n{pregunta}"
                ),
                use_llm=False,
                current_text=text,
            )

        return _respond(
            user, db, conversation, hechos,
            grounded=_materia_context(materia, subject),
            fallback=(
                f"{acuse} ¿La homologación es **interna** (misma institución) o "
                "**externa** (otra institución)?"
            ),
            use_llm=False,
            current_text=text,
        )

    if state == "ASK_TIPO":
        if _wants_all_info(norm):
            return _respond(
                user, db, conversation, hechos,
                fallback=(
                    f"Con gusto, {nombre}. {normativa.requisitos_generales_texto(db)}\n\n"
                    "Cuando lo tengas claro, dime **interna** o **externa** para "
                    "continuar con tu solicitud."
                ),
                use_llm=False,
                current_text=text,
            )

        if _has_any(norm, ["interna", "interno"]):
            hechos["tipo"] = "INTERNA"
        elif _has_any(norm, ["externa", "externo"]):
            hechos["tipo"] = "EXTERNA"
        else:
            return _respond(
                user, db, conversation, hechos,
                directiva=(
                    "No se identificó el tipo. Pide elegir entre interna y externa, "
                    "explicando la diferencia con amabilidad."
                ),
                fallback=(
                    "No logré identificar el tipo, con gusto te ayudo: "
                    "¿la materia es **interna** o **externa**? Si no estás seguro, "
                    "dime **«toda la información»** y te explico ambos casos."
                ),
                use_llm=_looks_like_question(text),
                current_text=text,
            )

        condicion = normativa.nota_minima(db, hechos["tipo"])
        hechos["state"] = "ASK_NOTA" if condicion else "ASK_CREDITOS"
        resumen = normativa.resumen_tipo(db, hechos["tipo"])

        if condicion:
            return _respond(
                user, db, conversation, hechos,
                fallback=(
                    f"¡Perfecto, {nombre}! {resumen}\n\n"
                    "**¿Cuál fue la nota que obtuviste en la materia?**"
                ),
                use_llm=False,
                current_text=text,
            )

        return _respond(
            user, db, conversation, hechos,
            fallback=(
                f"¡Perfecto, {nombre}! {resumen}\n\n"
                f"Ahora cuéntame, **¿cuántos créditos tiene {hechos.get('materia')}?**"
            ),
            use_llm=False,
            current_text=text,
        )

    if state == "ASK_NOTA":
        nota = _parse_decimal(text)
        if nota is None or nota < 0 or nota > 5:
            return _respond(
                user, db, conversation, hechos,
                directiva="Pide una nota válida en escala de 0 a 5, con amabilidad.",
                fallback=(
                    f"Con gusto te ayudo, {nombre}. Necesito la nota en una escala de 0 a 5 "
                    "(por ejemplo, 4.2). ¿Me la compartes?"
                ),
                use_llm=_looks_like_question(text),
                current_text=text,
            )

        hechos["nota"] = nota
        evaluacion = normativa.evaluar_nota(db, hechos.get("tipo"), nota)

        if evaluacion and not evaluacion["cumple"]:
            hechos["state"] = "ELIGIBILITY_FAILED"
            hechos["motivo_no_viable"] = (
                f"Nota {nota:g} inferior a la mínima {evaluacion['minimo']:g} "
                f"para homologación {(hechos.get('tipo') or '').lower()} "
                f"({evaluacion['fuente']})."
            )
            return _respond(
                user, db, conversation, hechos,
                fallback=(
                    f"Te agradezco mucho que me compartas tu nota, {nombre}. "
                    f"Con **{nota:g}** no cumples la nota mínima de "
                    f"**{evaluacion['minimo']:g}** para la homologación "
                    f"{(hechos.get('tipo') or '').lower()} ({evaluacion['fuente']}).\n\n"
                    "Por eso, **en este momento no es posible homologar** esta materia por "
                    "esa vía. No es una decisión mía: así lo establece la normativa y la "
                    "decisión final corresponde a la institución. Lo siento mucho.\n\n"
                    "Puedo ayudarte a revisar otra materia, mostrarte otras opciones o "
                    "dejar la consulta para que la oficina de homologaciones evalúe tu caso. "
                    "¿Qué prefieres?"
                ),
                use_llm=False,
                current_text=text,
            )

        if hechos.get("tipo") == "INTERNA":
            hechos["state"] = "ASK_SEMESTRE"
            acuse = (
                f"**¡Muy bien, {nombre}!** Con **{nota:g}** cumples la nota mínima "
                f"({evaluacion['fuente']})."
                if evaluacion
                else f"Gracias, {nombre}. Registré tu nota **{nota:g}**."
            )
            return _respond(
                user, db, conversation, hechos,
                fallback=(
                    f"{acuse} Ahora, **¿cuántos semestres has cursado** en tu programa?"
                ),
                use_llm=False,
                current_text=text,
            )

        hechos["state"] = "ASK_CREDITOS"
        acuse = (
            f"**¡Excelente, {nombre}!** Con **{nota:g}** cumples la nota mínima "
            f"de {evaluacion['minimo']:g} ({evaluacion['fuente']})."
            if evaluacion
            else f"Gracias, {nombre}. Registré tu nota **{nota:g}**."
        )
        return _respond(
            user, db, conversation, hechos,
            fallback=(
                f"{acuse} Ahora cuéntame, "
                f"**¿cuántos créditos tiene {hechos.get('materia')}?**"
            ),
            use_llm=False,
            current_text=text,
        )

    if state == "ASK_SEMESTRE":
        semestre = _parse_int(text)
        if semestre is None or semestre < 0 or semestre > 30:
            return _respond(
                user, db, conversation, hechos,
                directiva="Pide el número de semestres cursados, con amabilidad.",
                fallback=f"Con gusto, {nombre}. ¿Cuántos semestres has cursado en tu programa?",
                use_llm=_looks_like_question(text),
                current_text=text,
            )

        evaluacion = normativa.evaluar_semestre(db, hechos.get("tipo"), semestre)
        if evaluacion and not evaluacion["cumple"]:
            hechos["state"] = "ELIGIBILITY_FAILED"
            hechos["motivo_no_viable"] = (
                f"Semestres cursados ({semestre}) inferiores al mínimo "
                f"{evaluacion['minimo']:g} exigido por la normativa interna."
            )
            return _respond(
                user, db, conversation, hechos,
                fallback=(
                    f"Gracias por contármelo, {nombre}. La normativa interna exige haber "
                    f"cursado al menos **{evaluacion['minimo']:g} semestre(s)** y me indicas "
                    f"**{semestre}**. Por eso, por ahora **no es posible** completar esta "
                    "homologación interna.\n\n"
                    "Cuando cumplas ese requisito podrás retomarlo. Mientras tanto, puedo "
                    "explicarte otras opciones o dejar tu caso para la oficina de "
                    "homologaciones. ¿Te ayudo en algo más?"
                ),
                use_llm=False,
                current_text=text,
            )

        hechos["semestre"] = semestre
        hechos["state"] = "ASK_APROBADA"
        return _respond(
            user, db, conversation, hechos,
            fallback=(
                f"¡Gracias, {nombre}! Registré **{semestre} semestre(s)**. "
                "Una última verificación: **¿ya aprobaste la materia que deseas homologar?**"
            ),
            use_llm=False,
            current_text=text,
        )

    if state == "ASK_APROBADA":
        if _is_affirmative(norm):
            hechos["aprobada"] = True
            hechos["state"] = "ASK_CREDITOS"
            return _respond(
                user, db, conversation, hechos,
                fallback=(
                    f"¡Qué bueno, {nombre}! Entonces vamos muy bien. "
                    f"**¿Cuántos créditos tiene {hechos.get('materia')}?**"
                ),
                use_llm=False,
                current_text=text,
            )

        if _is_negative(norm):
            regla = normativa.regla_materia_aprobada(db, hechos.get("tipo"))
            detalle = (
                f" ({regla.documento_fuente or 'normativa institucional'}"
                + (f", {regla.articulo}" if regla and regla.articulo else "")
                + ")"
                if regla
                else ""
            )
            hechos["aprobada"] = False
            hechos["state"] = "ELIGIBILITY_FAILED"
            hechos["motivo_no_viable"] = (
                "La materia no está aprobada; la normativa exige que los espacios "
                "académicos a homologar hayan sido aprobados."
            )
            return _respond(
                user, db, conversation, hechos,
                fallback=(
                    f"Te agradezco la sinceridad, {nombre}. La normativa exige que la "
                    f"materia esté **aprobada** para poder homologarla{detalle}. "
                    "Por eso, por ahora **no es posible** continuar por esta vía.\n\n"
                    "Si la apruebas más adelante, con gusto retomamos el proceso. "
                    "¿Quieres que revise otra materia o te muestre los requisitos?"
                ),
                use_llm=False,
                current_text=text,
            )

        return _respond(
            user, db, conversation, hechos,
            directiva="Pide confirmar con un sí o un no si la materia está aprobada.",
            fallback=(
                f"Para continuar, {nombre}, confírmame por favor: "
                "**¿ya aprobaste la materia?** (Sí o No)"
            ),
            use_llm=_looks_like_question(text),
            current_text=text,
        )

    if state == "ASK_CREDITOS":
        creditos = _parse_int(text)
        if creditos is None or creditos <= 0 or creditos > 60:
            return _respond(
                user, db, conversation, hechos,
                directiva="Pide un número de créditos válido, con amabilidad.",
                fallback=(
                    f"Con gusto, {nombre}. Necesito un número de créditos entre 1 y 60. "
                    "¿Cuántos créditos tiene la materia?"
                ),
                use_llm=_looks_like_question(text),
                current_text=text,
            )

        hechos["creditos"] = creditos
        docs = _documentos(db, hechos.get("tipo"))
        limite = normativa.texto_limite(db, hechos.get("tipo"))
        recordatorio_limite = (
            f"\n\n📊 **Ten en cuenta:** {limite}." if limite else ""
        )

        if docs:
            hechos["state"] = "ASK_DOCS"
            return _respond(
                user, db, conversation, hechos,
                fallback=(
                    f"¡Perfecto, {nombre}! Registré **{creditos} créditos**."
                    f"{recordatorio_limite}\n\n"
                    f"📎 Ahora adjunta el primer documento con el clip: "
                    f"**{docs[0].titulo}**. ¿Lo tienes listo?"
                ),
                use_llm=False,
                current_text=text,
            )

        # Sin documentos: se muestra el resumen para que confirme antes de guardar.
        hechos["state"] = "REVIEW_SUMMARY"
        return _respond(
            user, db, conversation, hechos,
            fallback=(
                f"¡Perfecto, {nombre}! Registré **{creditos} créditos**."
                f"{recordatorio_limite}\n\n"
                f"{_resumen_texto(db, user, hechos)}\n\n"
                "**¿Está todo correcto?** Si deseas cambiar algo, dímelo; si está bien, "
                "responde **sí** y envío tu solicitud."
            ),
            use_llm=False,
            current_text=text,
        )

    if state == "ASK_DOCS":
        if _looks_like_question(text):
            siguiente = _siguiente_documento(db, hechos)
            return _respond(
                user, db, conversation, hechos,
                directiva=(
                    "Responde la duda con amabilidad y recuérdale que puede subir el "
                    f"documento pendiente con el clip: {siguiente or 'el que falta'}."
                ),
                fallback=(
                    f"Claro, {nombre}, te ayudo. Para continuar, sube con el clip 📎 "
                    f"**{siguiente or 'el documento pendiente'}**. "
                    f"Llevas {len(_documentos_hechos(hechos))} de "
                    f"{len(_documentos(db, hechos.get('tipo')))} documentos."
                ),
                current_text=text,
            )

        siguiente = _siguiente_documento(db, hechos)
        if not siguiente:
            return _respond(
                user, db, conversation, hechos,
                fallback=_siguiente_paso(db, user, hechos),
                use_llm=False,
                current_text=text,
            )

        return _respond(
            user, db, conversation, hechos,
            fallback=(
                f"Con gusto seguimos, {nombre}. Usa el clip 📎 para subir "
                f"**{siguiente}**. Llevas {len(_documentos_hechos(hechos))} de "
                f"{len(_documentos(db, hechos.get('tipo')))} documentos."
            ),
            use_llm=False,
            current_text=text,
        )

    # 5b) Resumen final: el estudiante confirma o pide cambios.
    if state == "REVIEW_SUMMARY":
        if _is_affirmative(norm) and not _is_negative(norm):
            return _finalizar(user, db, conversation, hechos)
        if _is_negative(norm):
            hechos["state"] = "ASK_UPDATES"
            return _respond(
                user, db, conversation, hechos,
                fallback=(
                    f"Claro, {nombre}. Dime qué dato deseas cambiar y lo actualizo. "
                    "Por ejemplo: «la nota es 4.5», «los créditos son 4» o "
                    "«la materia es interna»."
                ),
                use_llm=False,
                current_text=text,
            )
        return _respond(
            user, db, conversation, hechos,
            fallback=(
                f"Para continuar, {nombre}, dime **sí** si el resumen está correcto, "
                "o cuéntame qué deseas cambiar (por ejemplo: «la nota es 4.5»)."
            ),
            use_llm=False,
            current_text=text,
        )

    # 5c) Después de una actualización: ¿algo más? Si no, se registra.
    if state == "ASK_UPDATES":
        if _is_negative(norm):
            if not _datos_completos(db, hechos):
                hechos["state"] = "ASK_UPDATES"
                return _respond(
                    user, db, conversation, hechos,
                    fallback=f"Aún falta un dato, {nombre}. " + _siguiente_paso(db, user, hechos),
                    use_llm=False,
                    current_text=text,
                )
            return _finalizar(user, db, conversation, hechos)
        if _is_affirmative(norm):
            return _respond(
                user, db, conversation, hechos,
                fallback=(
                    f"Claro, {nombre}. ¿Qué deseas actualizar? Puedes decirme, por "
                    "ejemplo: «la nota es 4.5» o «la materia es interna»."
                ),
                use_llm=False,
                current_text=text,
            )
        return _respond(
            user, db, conversation, hechos,
            fallback=(
                f"Con gusto, {nombre}. ¿Hay algo más que quieras actualizar? "
                "Si ya está todo bien, dime **no** y registro tu solicitud."
            ),
            use_llm=False,
            current_text=text,
        )

    # 5d) Trámite ya enviado: el chat queda guardado y no se puede borrar.
    if state == "SUBMITTED":
        if _has_any(norm, START_WORDS):
            return _respond(
                user, db, conversation, hechos,
                fallback=(
                    f"Este chat quedó guardado con tu solicitud radicada, {nombre}, "
                    "así que no lo reinicio para no perder el historial. "
                    "Para iniciar otro trámite, usa el botón **+ (Nueva conversación)** "
                    "y con gusto te acompaño. ¿Te ayudo con algo más sobre esta solicitud?"
                ),
                use_llm=False,
                current_text=text,
            )
        return _respond(
            user, db, conversation, hechos,
            directiva=(
                "El trámite ya fue enviado y está en espera de aprobación. Responde "
                "la duda con amabilidad sin prometer fechas ni resultados, y recuerda "
                "que la decisión final es de la institución. Este chat está guardado y "
                "no se puede eliminar."
            ),
            fallback=(
                f"Tu solicitud ya quedó registrada y está **en espera de aprobación**, "
                f"{nombre}. Puedes preguntarme por su estado cuando quieras. "
                "Para un nuevo trámite, usa el botón **+ (Nueva conversación)**. "
                "¿Te ayudo en algo más?"
            ),
            current_text=text,
        )

    # 6) Confirmación para iniciar tras mostrar requisitos.
    if state == "WAITING_START_CONFIRMATION":
        if _is_affirmative(norm):
            hechos["state"] = "ASK_MATERIA"
            return _respond(
                user, db, conversation, hechos,
                fallback=_preguntar_materia(user),
                use_llm=False,
                current_text=text,
            )
        if _is_negative(norm):
            hechos["state"] = "IDLE"
            return _respond(
                user, db, conversation, hechos,
                fallback=(
                    f"Con gusto, {nombre}. Aquí estaré cuando quieras retomar el proceso. "
                    "¿Te ayudo con otra consulta?"
                ),
                use_llm=False,
                current_text=text,
            )

    # 7) Si no fue viable, explicar con empatía y permitir reintentar.
    if state == "ELIGIBILITY_FAILED":
        if _has_any(norm, START_WORDS) or any(
            p in norm for p in ["otra materia", "reintentar", "intentar de nuevo", "nueva solicitud"]
        ):
            hechos = _reset_flow(hechos)
            hechos["state"] = "ASK_MATERIA"
            return _respond(
                user, db, conversation, hechos,
                fallback=_preguntar_materia(user),
                use_llm=False,
                current_text=text,
            )

        motivo = hechos.get("motivo_no_viable") or "No se cumplen las condiciones normativas."
        return _respond(
            user, db, conversation, hechos,
            grounded=f"Resultado de la validación: NO VIABLE. Motivo: {motivo}",
            directiva=(
                "Explica con empatía que la homologación no es viable según la normativa, "
                "sin cambiar la decisión ni inventar excepciones. Responde la duda del "
                "estudiante y ofrécele alternativas (otra materia, revisar requisitos, "
                "consultar con la oficina)."
            ),
            fallback=(
                f"Entiendo tu inquietud, {nombre}. Según la normativa, esta homologación "
                f"no es viable: {motivo} Si quieres, revisamos otra materia o te muestro "
                "los requisitos para que consultes tu caso con la oficina. ¿Qué prefieres?"
            ),
            current_text=text,
        )

    # 8) Iniciar solicitud.
    if _has_any(norm, START_WORDS):
        hechos = _reset_flow(hechos)
        hechos["state"] = "ASK_MATERIA"
        return _respond(
            user, db, conversation, hechos,
            fallback=_preguntar_materia(user),
            use_llm=False,
            current_text=text,
        )

    # 8b) Revisar otra materia sin reiniciar la conversación desde cero.
    if any(
        expresion in norm
        for expresion in ["otra materia", "otra asignatura", "revisar otra", "buscar otra"]
    ):
        hechos = _reset_flow(hechos)
        hechos["state"] = "ASK_MATERIA"
        return _respond(
            user, db, conversation, hechos,
            fallback=(
                f"Con gusto, {nombre}. Vamos a revisar otra materia. "
                f"{_preguntar_materia(user)}"
            ),
            use_llm=False,
            current_text=text,
        )

    # 9) Saludo.
    if _has_any(norm, GREETING_WORDS) and len(norm.split()) <= 6:
        return _respond(
            user, db, conversation, hechos,
            fallback=(
                f"¡Hola, {nombre}! 😊 Qué gusto tenerte por aquí. Puedo revisar si una "
                "materia figura como homologable, mostrarte los requisitos según la "
                "normativa o acompañarte paso a paso en tu solicitud. "
                "¿Con qué te ayudo hoy?"
            ),
            current_text=text,
        )

    # 10) Conversación libre con grounding normativo.
    reglas = normativa.reglas_para_tipo(db, hechos.get("tipo"))
    grounded = normativa.requisitos_texto(db, hechos.get("tipo")) if reglas else None
    return _respond(
        user, db, conversation, hechos,
        grounded=grounded,
        directiva=(
            f"Conversa con {nombre} de forma cálida, cercana y respetuosa. Responde su "
            "pregunta manteniendo el hilo. Si se relaciona con homologación, usa solo la "
            "información confirmada del sistema y cita la fuente. Si falta un dato, pídelo "
            "con amabilidad. Si no está en la normativa, dilo y remite a la oficina. "
            "Nunca inventes requisitos, notas, equivalencias ni decisiones."
        ),
        fallback=GENERIC_FALLBACK,
        current_text=text,
    )


# ---------------------------------------------------------------------------
# Subida de documentos
# ---------------------------------------------------------------------------
def preparar_documento(db: Session, hechos: dict) -> tuple:
    """Devuelve (tipo esperado, es_reemplazo) sin confiar en el cliente."""
    reemplazar = hechos.get("reemplazar")
    if reemplazar:
        return reemplazar, True
    return _siguiente_documento(db, hechos), False


def handle_document_uploaded(
    user: Usuario,
    db: Session,
    conversation: Conversacion,
    nombre_archivo: str,
    tipo_documento: str,
    documento_id: Optional[int] = None,
    verificado: bool = True,
    observacion: Optional[str] = None,
) -> str:
    hechos = _merge_hechos(conversation)
    nombre = user.nombres or "estudiante"
    total = len(_documentos(db, hechos.get("tipo")))

    anteriores = [
        d
        for d in _documentos_hechos(hechos)
        if _norm_doc(d.get("tipo")) != _norm_doc(tipo_documento)
    ]
    reemplazo = len(anteriores) != len(_documentos_hechos(hechos))
    anteriores.append(
        {
            "tipo": tipo_documento,
            "archivo": nombre_archivo,
            "documento_id": documento_id,
            "verificado": bool(verificado),
            "observacion": observacion or "",
        }
    )
    hechos["documentos"] = anteriores
    hechos["docs_subidos"] = len(anteriores)
    hechos["reemplazar"] = None

    encabezado = (
        f"🔄 Reemplacé **{tipo_documento}** por el nuevo archivo."
        if reemplazo
        else f"¡Recibido, {nombre}! 📄 Guardé **{tipo_documento}**."
    )
    if observacion:
        encabezado += f" {observacion}."

    evento = (
        f"El estudiante subió '{nombre_archivo}' como '{tipo_documento}'. "
        f"Documentos entregados: {len(anteriores)} de {total}."
    )

    volver = hechos.pop("_return_state", None)
    if volver == "ASK_UPDATES":
        hechos["state"] = "ASK_UPDATES"
        resumen = _resumen_texto(db, user, hechos)
        return _respond(
            user, db, conversation, hechos,
            extra_system=evento,
            fallback=(
                f"{encabezado}\n\n{resumen}\n\n"
                "**¿Hay algo más que quieras actualizar?**"
            ),
            use_llm=False,
        )

    return _respond(
        user, db, conversation, hechos,
        extra_system=evento,
        fallback=f"{encabezado}\n\n{_siguiente_paso(db, user, hechos)}",
        use_llm=False,
    )


def handle_document_rejected(
    user: Usuario,
    db: Session,
    conversation: Conversacion,
    nombre_archivo: str,
    tipo_documento: str,
    motivo: str,
) -> str:
    """El PDF no coincide con el estudiante: no se guarda ni se cuenta."""
    hechos = _merge_hechos(conversation)
    nombre = user.nombres or "estudiante"

    ya_estaba = any(
        _norm_doc(d.get("tipo")) == _norm_doc(tipo_documento)
        for d in _documentos_hechos(hechos)
    )
    if ya_estaba:
        hechos["reemplazar"] = tipo_documento
    hechos["state"] = "ASK_DOCS"

    return _respond(
        user, db, conversation, hechos,
        fallback=(
            f"Gracias por enviarlo, {nombre}. Revisé **{tipo_documento}** y no pude "
            f"validarlo: {motivo}.\n\n"
            "No lo guardé ni lo conté en tu solicitud. ¿Puedes subir de nuevo "
            f"**{tipo_documento}** con el clip 📎? Los documentos que ya entregaste "
            "siguen guardados."
        ),
        use_llm=False,
    )