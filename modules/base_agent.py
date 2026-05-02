"""
base_agent.py — Contrato base para todos los empleados del Shorts Factory

Cada agente tiene: nombre, cargo, departamento, a quién reporta,
y se comunica con el CEO por Telegram en primera persona.
"""

import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class BaseAgent:
    name: str        = ""
    role: str        = ""
    department: str  = ""
    reports_to: str  = "CEO"

    def __repr__(self) -> str:
        return f"{self.name} ({self.role})"

    def log(self, msg: str) -> None:
        logger.info(f"[{self.name}] {msg}")

    def notify(self, msg: str) -> None:
        """Reporta al CEO por Telegram. No crítico si falla."""
        try:
            from modules import telegram_commander
            telegram_commander.notify(f"*{self.name}*\n{msg}")
        except Exception:
            pass

    def _llm_call(self, prompt: str, system: str = "", max_tokens: int = 400) -> str:
        """Groq → OpenAI → Ollama. Retorna '' si todo falla."""
        from modules import llm_service
        return llm_service.call_llm(prompt, system=system, max_tokens=max_tokens)
