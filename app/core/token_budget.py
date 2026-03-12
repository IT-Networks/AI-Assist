"""
Token Budget Manager - Verwaltet das Token-Budget für Anfragen.

Stellt sicher, dass verschiedene Kontext-Kategorien ihre Limits einhalten
und ermöglicht intelligente Kompression wenn nötig.
"""

from dataclasses import dataclass, field
from typing import Dict, Optional

from app.core.config import settings


@dataclass
class TokenBudget:
    """
    Verwaltet das Token-Budget für eine LLM-Anfrage.

    Kategorien:
    - system: System Prompt (reserviert)
    - memory: Long-term Memory Facts
    - context: Tool-Outputs, Dateien
    - conversation: Chat-Historie
    - response: Für LLM-Antwort reserviert
    """

    # Gesamtbudget
    total_budget: int = field(default_factory=lambda: settings.context.max_tokens)

    # Reservierungen (nicht überschreitbar)
    system_reserved: int = 2000      # System Prompt
    response_reserved: int = 4000    # Platz für LLM-Antwort

    # Soft-Limits pro Kategorie (können bei Bedarf angepasst werden)
    memory_limit: int = 2000         # Long-term Memory
    context_limit: int = 10000       # Tool-Outputs, Files
    conversation_limit: int = 18000  # Chat-Historie

    # Aktuelle Nutzung
    used_system: int = 0
    used_memory: int = 0
    used_context: int = 0
    used_conversation: int = 0

    # Compaction Threshold (80% = Compaction starten)
    compaction_threshold: float = 0.8

    # PERFORMANCE: Kategorie-Mapping für schnellen Lookup (vermeidet getattr/setattr)
    # Wird in __post_init__ initialisiert
    _category_map: Dict[str, tuple] = field(default_factory=dict, repr=False)

    def __post_init__(self):
        """Initialisiert das Kategorie-Mapping für schnellen Lookup."""
        self._category_map = {
            "system": ("system_reserved", "used_system"),
            "memory": ("memory_limit", "used_memory"),
            "context": ("context_limit", "used_context"),
            "conversation": ("conversation_limit", "used_conversation"),
        }

    @property
    def available_total(self) -> int:
        """Verfügbares Budget (abzüglich Reservierungen)."""
        return self.total_budget - self.response_reserved

    @property
    def used_total(self) -> int:
        """Aktuell genutztes Budget."""
        return self.used_system + self.used_memory + self.used_context + self.used_conversation

    @property
    def remaining(self) -> int:
        """Noch verfügbare Tokens."""
        return max(0, self.available_total - self.used_total)

    @property
    def usage_percent(self) -> float:
        """Nutzung in Prozent (0.0 - 1.0)."""
        if self.available_total == 0:
            return 1.0
        return self.used_total / self.available_total

    def needs_compaction(self) -> bool:
        """True wenn Compaction nötig (über Threshold)."""
        return self.usage_percent >= self.compaction_threshold

    def can_add(self, category: str, tokens: int) -> bool:
        """Prüft ob Tokens zur Kategorie hinzugefügt werden können."""
        # PERFORMANCE: Dict-Lookup statt String-Interpolation + getattr
        mapping = self._category_map.get(category)
        if not mapping:
            return False

        limit_attr, used_attr = mapping
        limit = getattr(self, limit_attr)
        used = getattr(self, used_attr)

        # Prüfe Kategorie-Limit und Gesamt-Limit in einem Schritt
        return (used + tokens <= limit) and (self.used_total + tokens <= self.available_total)

    def add(self, category: str, tokens: int) -> bool:
        """
        Fügt Tokens zu einer Kategorie hinzu.

        Returns: True wenn erfolgreich, False wenn Limit überschritten.
        """
        # PERFORMANCE: Dict-Lookup statt String-Interpolation
        mapping = self._category_map.get(category)
        if not mapping:
            return False

        _, used_attr = mapping
        current = getattr(self, used_attr)
        setattr(self, used_attr, current + tokens)
        return True

    def set(self, category: str, tokens: int) -> None:
        """Setzt die Token-Nutzung einer Kategorie."""
        # PERFORMANCE: Dict-Lookup statt String-Interpolation
        mapping = self._category_map.get(category)
        if mapping:
            _, used_attr = mapping
            setattr(self, used_attr, tokens)

    def get_status(self) -> Dict:
        """Gibt Status-Dict für Debugging/UI zurück."""
        return {
            "total_budget": self.total_budget,
            "available": self.available_total,
            "used": self.used_total,
            "remaining": self.remaining,
            "usage_percent": round(self.usage_percent * 100, 1),
            "needs_compaction": self.needs_compaction(),
            "breakdown": {
                "system": {"used": self.used_system, "limit": self.system_reserved},
                "memory": {"used": self.used_memory, "limit": self.memory_limit},
                "context": {"used": self.used_context, "limit": self.context_limit},
                "conversation": {"used": self.used_conversation, "limit": self.conversation_limit},
            }
        }

    def __str__(self) -> str:
        return (
            f"TokenBudget({self.used_total}/{self.available_total} "
            f"= {self.usage_percent*100:.1f}%"
            f"{' [COMPACT!]' if self.needs_compaction() else ''})"
        )


def create_budget_from_config() -> TokenBudget:
    """Erstellt TokenBudget aus Config-Einstellungen."""
    return TokenBudget(
        total_budget=settings.context.max_tokens,
        # Weitere Config-Werte können hier geladen werden
    )
