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

    def on_hire(self) -> None:
        """Presentación al arrancar — el CEO ve quién está activo."""
        self.log(f"listo para trabajar | Dpto: {self.department} | Reporta a: {self.reports_to}")

    def _llm_call(self, prompt: str, system: str = "", max_tokens: int = 400) -> str:
        """Groq → OpenAI → Ollama. Retorna '' si todo falla."""
        import requests
        import config

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        # ── 1. Groq (rápido y gratuito, 500k tokens/día) ──────────────────────
        groq_key = getattr(config, "GROQ_API_KEY", "")
        if groq_key:
            try:
                resp = requests.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"},
                    json={
                        "model":       getattr(config, "GROQ_MODEL", "llama-3.3-70b-versatile"),
                        "messages":    messages,
                        "temperature": 0.8,
                        "max_tokens":  max_tokens,
                    },
                    timeout=30,
                )
                if resp.status_code == 429:
                    self.log("Groq: cuota agotada — probando OpenAI")
                elif resp.status_code == 200:
                    return resp.json()["choices"][0]["message"]["content"].strip()
                else:
                    resp.raise_for_status()
            except Exception as e:
                self.log(f"Groq error: {e}")

        # ── 2. OpenAI (mejor calidad, de pago) ────────────────────────────────
        openai_key = getattr(config, "OPENAI_API_KEY", "")
        if openai_key:
            try:
                resp = requests.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {openai_key}", "Content-Type": "application/json"},
                    json={
                        "model":       getattr(config, "OPENAI_MODEL", "gpt-4o-mini"),
                        "messages":    messages,
                        "temperature": 0.9,
                        "max_tokens":  max_tokens,
                    },
                    timeout=40,
                )
                if resp.status_code == 200:
                    self.log("usando OpenAI")
                    return resp.json()["choices"][0]["message"]["content"].strip()
                else:
                    self.log(f"OpenAI error {resp.status_code}: {resp.text[:100]}")
            except Exception as e:
                self.log(f"OpenAI error: {e}")

        # ── 3. Ollama local (fallback sin internet) ────────────────────────────
        try:
            import ollama
            resp = ollama.chat(
                model=getattr(config, "OLLAMA_MODEL", "llama3.2"),
                messages=messages,
                options={"num_predict": max_tokens},
            )
            return resp.message.content.strip()
        except Exception as e:
            self.log(f"Ollama error: {e}")
            return ""
