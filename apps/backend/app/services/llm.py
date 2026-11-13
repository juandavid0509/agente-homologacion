"""Cliente de Ollama.

Se habla directamente con la API HTTP `/api/chat` para tener control total
sobre contexto (`num_ctx`), tokens de salida, `keep_alive`, timeouts y
reintentos. Ollama es *stateless*: el backend debe reenviar el historial en
cada petición, cosa que hace `app.services.agent`.
"""

import logging
import time
from typing import Dict, List, Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


class LLMUnavailable(RuntimeError):
    """Ollama no está disponible, el modelo no existe o la respuesta fue inválida."""


def _payload(messages: List[Dict[str, str]], num_predict: Optional[int], temperature: Optional[float]) -> dict:
    return {
        "model": settings.OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "keep_alive": settings.OLLAMA_KEEP_ALIVE,
        "options": {
            "num_ctx": settings.OLLAMA_NUM_CTX,
            "num_predict": num_predict or settings.OLLAMA_NUM_PREDICT,
            "temperature": settings.OLLAMA_TEMPERATURE if temperature is None else temperature,
            "top_p": settings.OLLAMA_TOP_P,
            "repeat_penalty": settings.OLLAMA_REPEAT_PENALTY,
        },
    }


def chat(
    messages: List[Dict[str, str]],
    num_predict: Optional[int] = None,
    temperature: Optional[float] = None,
) -> str:
    """Envía la conversación completa a Ollama y devuelve el texto del asistente."""
    if settings.LLM_MODE != "ollama":
        raise LLMUnavailable(f"LLM_MODE='{settings.LLM_MODE}' (se esperaba 'ollama')")

    url = f"{settings.OLLAMA_HOST}/api/chat"
    payload = _payload(messages, num_predict, temperature)
    timeout = httpx.Timeout(settings.OLLAMA_TIMEOUT, connect=settings.OLLAMA_CONNECT_TIMEOUT)
    last_error: Optional[Exception] = None

    for attempt in range(1, settings.OLLAMA_RETRIES + 1):
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.post(url, json=payload)

            if resp.status_code == 404:
                raise LLMUnavailable(
                    f"El modelo '{settings.OLLAMA_MODEL}' no está disponible en Ollama. "
                    f"Ejecuta: ollama pull {settings.OLLAMA_MODEL}"
                )

            if resp.status_code >= 500:
                raise httpx.HTTPStatusError(
                    f"Ollama respondió {resp.status_code}", request=resp.request, response=resp
                )

            resp.raise_for_status()
            data = resp.json()
            content = ((data.get("message") or {}).get("content") or "").strip()
            if not content:
                raise LLMUnavailable("Ollama devolvió una respuesta vacía.")
            return content

        except LLMUnavailable:
            raise
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:300] if exc.response is not None else str(exc)
            if exc.response is not None and exc.response.status_code >= 500 and attempt < settings.OLLAMA_RETRIES:
                last_error = exc
                logger.warning("Ollama 5xx (intento %s/%s): %s", attempt, settings.OLLAMA_RETRIES, detail)
                time.sleep(0.6 * attempt)
                continue
            raise LLMUnavailable(f"Error HTTP de Ollama: {detail}") from exc
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.RemoteProtocolError) as exc:
            last_error = exc
            logger.warning(
                "No se pudo hablar con Ollama (intento %s/%s): %s",
                attempt,
                settings.OLLAMA_RETRIES,
                exc,
            )
            if attempt < settings.OLLAMA_RETRIES:
                time.sleep(0.6 * attempt)

    raise LLMUnavailable(
        f"No se pudo conectar con Ollama en {settings.OLLAMA_HOST}: {last_error}"
    )


def health() -> dict:
    """Comprueba que Ollama responda y si el modelo configurado está descargado."""
    try:
        with httpx.Client(timeout=httpx.Timeout(5.0, connect=2.0)) as client:
            resp = client.get(f"{settings.OLLAMA_HOST}/api/tags")
            resp.raise_for_status()
            data = resp.json()
        names = [m.get("name", "") for m in data.get("models", [])]
        # Ollama puede devolver "llama3.2:3b" o "llama3.2:3b" sin tag implícito.
        model_ok = any(
            n == settings.OLLAMA_MODEL
            or n.split(":")[0] == settings.OLLAMA_MODEL.split(":")[0]
            for n in names
        )
        return {
            "ok": True,
            "host": settings.OLLAMA_HOST,
            "model": settings.OLLAMA_MODEL,
            "model_available": model_ok,
            "models": names,
        }
    except Exception as exc:  # pragma: no cover - depende del entorno
        return {
            "ok": False,
            "host": settings.OLLAMA_HOST,
            "model": settings.OLLAMA_MODEL,
            "model_available": False,
            "error": str(exc),
        }
