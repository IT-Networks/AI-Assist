"""
Auto-Learner - Erkennt und speichert Wissen automatisch aus dem Chat.

Trigger:
1. Explizite User-Befehle: "Merke dir...", "Vergiss nicht...", "Wichtig:"
2. Problemlösungen: Fehler → Lösung → Bestätigung
3. Entscheidungen: Diskussion → "wir machen X", "ich entscheide mich für Y"
4. Wiederholte Patterns: Gleiches Muster 3x verwendet

Inspiriert von Claude Code's Auto-Memory System.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app.services.memory_store import (
    MemoryStore,
    MemoryScope,
    MemoryCategory,
    MemorySource,
    get_memory_store
)
from app.services.context_manager import ContextManager, get_context_manager


@dataclass
class LearningCandidate:
    """Ein Kandidat für Auto-Learning."""
    category: str           # pattern, decision, solution, preference, warning
    key: str               # Kurzer Titel
    value: str             # Der Inhalt
    importance: float      # 0.0 - 1.0
    confidence: float      # Wie sicher ist die Erkennung?
    source: str            # user, ai_learned
    trigger: str           # Was hat das Learning ausgelöst?
    related_files: List[str] = field(default_factory=list)


# ══════════════════════════════════════════════════════════════════════════════
# Pattern-Definitionen für Erkennung
# ══════════════════════════════════════════════════════════════════════════════

# Explizite User-Befehle zum Merken
REMEMBER_PATTERNS = [
    # Deutsch
    (r"(?:merke?\s*(?:dir)?|vergiss?\s*nicht|wichtig)\s*[:\-]?\s*(.+)", "de"),
    (r"(?:speicher|notier)\s*(?:dir)?\s*[:\-]?\s*(.+)", "de"),
    (r"(?:behalte?|erinner)\s*(?:dir)?\s*[:\-]?\s*(.+)", "de"),
    (r"(?:für\s*die\s*zukunft|künftig|immer)\s*[:\-]?\s*(.+)", "de"),
    # Englisch
    (r"(?:remember|don'?t\s*forget|important)\s*[:\-]?\s*(.+)", "en"),
    (r"(?:note|save|store)\s*(?:that)?\s*[:\-]?\s*(.+)", "en"),
    (r"(?:always|from\s*now\s*on|in\s*the\s*future)\s*[:\-]?\s*(.+)", "en"),
]

# Vergessen-Befehle
FORGET_PATTERNS = [
    (r"(?:vergiss|lösch|entfern)\s*(?:das|die\s*info)?\s*(?:über|zu|von)?\s*[:\-]?\s*(.+)", "de"),
    (r"(?:forget|delete|remove)\s*(?:that|the\s*info)?\s*(?:about)?\s*[:\-]?\s*(.+)", "en"),
]

# Entscheidungs-Patterns
DECISION_PATTERNS = [
    # Deutsch
    (r"(?:wir\s*(?:machen|nehmen|nutzen|verwenden)|ich\s*entscheide\s*mich\s*für)\s+(.+)", "de"),
    (r"(?:die\s*entscheidung\s*ist|entschieden\s*für)\s*[:\-]?\s*(.+)", "de"),
    (r"(?:wir\s*gehen\s*mit|der\s*ansatz\s*ist)\s+(.+)", "de"),
    # Englisch
    (r"(?:we'?(?:ll|re)\s*(?:go\s*with|use)|i\s*(?:decide|choose))\s+(.+)", "en"),
    (r"(?:the\s*decision\s*is|decided\s*(?:on|for))\s*[:\-]?\s*(.+)", "en"),
]

# Problemlösungs-Patterns (im Assistant-Response)
SOLUTION_PATTERNS = [
    # Deutsch
    (r"(?:das\s*problem\s*war|der\s*fehler\s*(?:war|lag))\s*[:\-]?\s*(.+?)(?:\.|$)", "de"),
    (r"(?:die\s*lösung\s*(?:ist|war)|gelöst\s*(?:durch|mit))\s*[:\-]?\s*(.+?)(?:\.|$)", "de"),
    (r"(?:fix(?:ed)?|behoben)\s*[:\-]?\s*(.+?)(?:\.|$)", "de"),
    # Englisch
    (r"(?:the\s*(?:problem|issue)\s*was|the\s*error\s*was)\s*[:\-]?\s*(.+?)(?:\.|$)", "en"),
    (r"(?:the\s*(?:solution|fix)\s*(?:is|was)|solved\s*(?:by|with))\s*[:\-]?\s*(.+?)(?:\.|$)", "en"),
]

# Warning-Patterns
WARNING_PATTERNS = [
    (r"(?:achtung|vorsicht|warnung)\s*[:\-]?\s*(.+)", "de"),
    (r"(?:warning|caution|beware)\s*[:\-]?\s*(.+)", "en"),
    (r"(?:nicht\s*vergessen|don'?t\s*forget)\s*[:\-]?\s*(.+)", "mixed"),
]


class AutoLearner:
    """
    Erkennt und speichert Wissen automatisch aus dem Chat.

    Integriert sich in den Orchestrator und analysiert:
    - User-Nachrichten nach expliziten Lern-Befehlen
    - Assistant-Antworten nach Problemlösungen
    - Konversationsverlauf nach Entscheidungen
    """

    def __init__(
        self,
        memory_store: Optional[MemoryStore] = None,
        context_manager: Optional[ContextManager] = None
    ):
        self.memory = memory_store or get_memory_store()
        self.context = context_manager or get_context_manager()

        # Tracking für Pattern-Erkennung (3x = lernen)
        self._pattern_counts: Dict[str, int] = {}

        # Tracking für Problem-Lösung-Paare
        self._pending_problems: List[Dict[str, Any]] = []

    # ══════════════════════════════════════════════════════════════════════════
    # Haupt-API
    # ══════════════════════════════════════════════════════════════════════════

    async def analyze_user_message(
        self,
        message: str,
        project_id: Optional[str] = None,
        session_id: Optional[str] = None
    ) -> List[LearningCandidate]:
        """
        Analysiert eine User-Nachricht auf Lern-Trigger.

        Returns:
            Liste von LearningCandidates die gespeichert werden sollten
        """
        candidates = []

        # 1. Explizite "Merke dir..." Befehle
        for pattern, lang in REMEMBER_PATTERNS:
            match = re.search(pattern, message, re.IGNORECASE)
            if match:
                content = match.group(1).strip()
                if len(content) > 10:  # Mindestlänge
                    candidates.append(LearningCandidate(
                        category=MemoryCategory.PREFERENCE.value,
                        key=self._extract_key(content),
                        value=content,
                        importance=0.9,
                        confidence=0.95,
                        source=MemorySource.USER.value,
                        trigger=f"explicit_remember ({lang})"
                    ))

        # 2. Vergessen-Befehle (markieren zum Löschen)
        for pattern, lang in FORGET_PATTERNS:
            match = re.search(pattern, message, re.IGNORECASE)
            if match:
                content = match.group(1).strip()
                # Suche und lösche passende Memories
                await self._handle_forget_request(content, project_id, session_id)

        # 3. Entscheidungs-Patterns
        for pattern, lang in DECISION_PATTERNS:
            match = re.search(pattern, message, re.IGNORECASE)
            if match:
                content = match.group(1).strip()
                if len(content) > 5:
                    candidates.append(LearningCandidate(
                        category=MemoryCategory.DECISION.value,
                        key=self._extract_key(content),
                        value=content,
                        importance=0.85,
                        confidence=0.8,
                        source=MemorySource.USER.value,
                        trigger=f"decision_pattern ({lang})"
                    ))

        # 4. Problem-Beschreibung erkennen (für spätere Lösung)
        if self._looks_like_problem(message):
            self._pending_problems.append({
                "description": message[:200],
                "timestamp": datetime.utcnow().isoformat(),
                "project_id": project_id
            })
            # Nur letzte 5 Probleme behalten
            self._pending_problems = self._pending_problems[-5:]

        return candidates

    async def analyze_assistant_response(
        self,
        response: str,
        user_message: str,
        project_id: Optional[str] = None,
        related_files: Optional[List[str]] = None
    ) -> List[LearningCandidate]:
        """
        Analysiert eine Assistant-Antwort auf lernbare Inhalte.

        Returns:
            Liste von LearningCandidates
        """
        candidates = []

        # 1. Lösungs-Patterns erkennen
        for pattern, lang in SOLUTION_PATTERNS:
            match = re.search(pattern, response, re.IGNORECASE)
            if match:
                solution = match.group(1).strip()
                if len(solution) > 20:
                    # Prüfe ob es ein passendes Problem gab
                    problem = self._find_matching_problem(user_message)
                    key = self._extract_key(solution)

                    if problem:
                        value = f"Problem: {problem['description'][:100]}... → Lösung: {solution}"
                    else:
                        value = solution

                    candidates.append(LearningCandidate(
                        category=MemoryCategory.SOLUTION.value,
                        key=key,
                        value=value,
                        importance=0.75,
                        confidence=0.7,
                        source=MemorySource.AI_LEARNED.value,
                        trigger=f"solution_pattern ({lang})",
                        related_files=related_files or []
                    ))

        # 2. Warning-Patterns
        for pattern, lang in WARNING_PATTERNS:
            match = re.search(pattern, response, re.IGNORECASE)
            if match:
                warning = match.group(1).strip()
                if len(warning) > 10:
                    candidates.append(LearningCandidate(
                        category=MemoryCategory.WARNING.value,
                        key=self._extract_key(warning),
                        value=warning,
                        importance=0.8,
                        confidence=0.75,
                        source=MemorySource.AI_LEARNED.value,
                        trigger=f"warning_pattern ({lang})"
                    ))

        return candidates

    async def track_tool_usage(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        project_id: Optional[str] = None
    ) -> Optional[LearningCandidate]:
        """
        Trackt Tool-Nutzung für Pattern-Erkennung.

        Wenn ein Pattern 3x verwendet wird, wird es als Pattern gespeichert.
        """
        # Pattern-Key aus Tool + relevanten Args erstellen
        pattern_key = f"{tool_name}"

        # Bestimmte Args zum Pattern-Key hinzufügen
        if "timeout" in arguments:
            pattern_key += f":timeout={arguments['timeout']}"
        if "working_dir" in arguments:
            pattern_key += f":dir_pattern"

        # Zählen
        self._pattern_counts[pattern_key] = self._pattern_counts.get(pattern_key, 0) + 1

        # Bei 3x → Pattern lernen
        if self._pattern_counts[pattern_key] == 3:
            return LearningCandidate(
                category=MemoryCategory.PATTERN.value,
                key=f"tool_usage_{tool_name}",
                value=f"Tool '{tool_name}' wird häufig mit diesen Argumenten verwendet: {arguments}",
                importance=0.6,
                confidence=0.7,
                source=MemorySource.AI_LEARNED.value,
                trigger="repeated_pattern (3x)"
            )

        return None

    async def save_candidates(
        self,
        candidates: List[LearningCandidate],
        project_id: Optional[str] = None,
        session_id: Optional[str] = None,
        project_path: Optional[str] = None
    ) -> List[str]:
        """
        Speichert LearningCandidates in Memory und optional MEMORY.md.

        Returns:
            Liste der erstellten Memory-IDs
        """
        saved_ids = []

        for candidate in candidates:
            # In SQLite Memory speichern
            memory_id = await self.memory.remember(
                key=candidate.key,
                value=candidate.value,
                category=candidate.category,
                scope=MemoryScope.PROJECT.value if project_id else MemoryScope.SESSION.value,
                project_id=project_id,
                session_id=session_id,
                importance=candidate.importance,
                confidence=candidate.confidence,
                source=candidate.source,
                related_files=candidate.related_files
            )
            saved_ids.append(memory_id)

            # Wichtige Learnings auch in MEMORY.md schreiben
            if project_path and candidate.importance >= 0.8:
                await self.context.append_to_memory_md(
                    project_path=project_path,
                    entry=f"**{candidate.key}:** {candidate.value}",
                    category=candidate.category
                )

        return saved_ids

    # ══════════════════════════════════════════════════════════════════════════
    # Hilfsmethoden
    # ══════════════════════════════════════════════════════════════════════════

    def _extract_key(self, content: str, max_length: int = 50) -> str:
        """Extrahiert einen kurzen Key aus dem Content."""
        # Erste N Wörter nehmen
        words = content.split()[:6]
        key = "_".join(words)

        # Sonderzeichen entfernen
        key = re.sub(r'[^a-zA-Z0-9äöüÄÖÜß_-]', '', key)

        return key[:max_length] if key else "auto_learned"

    def _looks_like_problem(self, message: str) -> bool:
        """Prüft ob eine Nachricht wie ein Problem aussieht."""
        problem_indicators = [
            r"fehler", r"error", r"problem", r"issue",
            r"funktioniert\s*nicht", r"doesn'?t\s*work",
            r"kaputt", r"broken", r"bug",
            r"hilfe", r"help",
            r"warum", r"why",
            r"wie\s*(?:kann|löse)", r"how\s*(?:can|do)",
        ]
        return any(re.search(p, message, re.IGNORECASE) for p in problem_indicators)

    def _find_matching_problem(self, context: str) -> Optional[Dict[str, Any]]:
        """Findet ein passendes Problem aus der Pending-Liste."""
        if not self._pending_problems:
            return None

        # Einfach das letzte Problem nehmen (könnte verbessert werden)
        return self._pending_problems.pop()

    async def _handle_forget_request(
        self,
        content: str,
        project_id: Optional[str],
        session_id: Optional[str]
    ) -> int:
        """Behandelt Vergessen-Anfragen."""
        # Suche passende Memories
        memories = await self.memory.recall(
            query=content,
            project_id=project_id,
            session_id=session_id,
            limit=5
        )

        deleted = 0
        for memory in memories:
            # Nur löschen wenn hohe Übereinstimmung (Key enthält Suchbegriff)
            if any(word.lower() in memory.key.lower() for word in content.split()):
                if await self.memory.forget(memory.id):
                    deleted += 1

        return deleted


# ══════════════════════════════════════════════════════════════════════════════
# Singleton
# ══════════════════════════════════════════════════════════════════════════════

_auto_learner: Optional[AutoLearner] = None


def get_auto_learner() -> AutoLearner:
    """Gibt Singleton-Instanz zurück."""
    global _auto_learner
    if _auto_learner is None:
        _auto_learner = AutoLearner()
    return _auto_learner
