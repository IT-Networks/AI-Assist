"""
Brainstorm Capability - Interactive requirements discovery.

Transforms ambiguous ideas into concrete specifications through
Socratic dialogue and systematic exploration.
"""

import logging
from typing import Any, Dict, List, Optional

from app.mcp.capabilities.base import (
    BaseCapability,
    CapabilityPhase,
    CapabilitySession
)

logger = logging.getLogger(__name__)


class BrainstormCapability(BaseCapability):
    """
    Interactive brainstorming and requirements discovery.

    Flow:
    1. Understand the initial idea/request
    2. Ask clarifying questions
    3. Explore possibilities and constraints
    4. Identify requirements and success criteria
    5. Generate actionable specification
    """

    @property
    def name(self) -> str:
        return "brainstorm"

    @property
    def description(self) -> str:
        return (
            "SOFTWARE-ENTWICKLUNG: Interaktives Brainstorming für Requirements Discovery. "
            "Transformiert vage Software-Ideen in konkrete technische Spezifikationen. "
            "NUR für: Neue Code-Features, Software-Architektur, technische Konzepte. "
            "NICHT für: Texte schreiben, Dokumentation, Reports, allgemeine Recherche."
        )

    @property
    def handoff_targets(self) -> List[str]:
        return ["design", "implement"]

    def _get_parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Die zu erkundende Idee oder das Problem"
                },
                "context": {
                    "type": "string",
                    "description": "Optional: Zusätzlicher Kontext (Projekt, Constraints)"
                },
                "depth": {
                    "type": "string",
                    "enum": ["shallow", "normal", "deep"],
                    "description": "Tiefe der Exploration (default: normal)"
                },
                "focus_areas": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional: Spezifische Bereiche zum Fokussieren"
                }
            },
            "required": ["query"]
        }

    async def _phase_explore(self, session: CapabilitySession) -> None:
        """Explore the problem space with structured questions."""
        depth = session.metadata.get("depth", "normal")

        # Generate exploration questions
        exploration_prompt = f"""
Du bist ein Requirements-Analyst. Analysiere folgende Idee/Anfrage und generiere
strukturierte Fragen zur Klärung.

IDEE/ANFRAGE:
{session.query}

KONTEXT:
{session.context or "Kein zusätzlicher Kontext"}

Generiere eine strukturierte Analyse mit:
1. VERSTÄNDNIS: Was ist das Kernproblem/die Kernidee?
2. ZIELGRUPPE: Wer sind die Nutzer/Stakeholder?
3. ERFOLGSKRITERIEN: Wie misst man Erfolg?
4. CONSTRAINTS: Technische, zeitliche, budgetäre Einschränkungen?
5. OFFENE FRAGEN: Was muss noch geklärt werden?

Sei {"sehr detailliert" if depth == "deep" else "prägnant"}.
"""

        if self.llm_callback:
            response = await self._call_llm(exploration_prompt)
        else:
            response = self._generate_default_exploration(session.query)

        # Parse response into insights and questions
        insights, questions = self._parse_exploration(response)

        session.add_step(
            phase=CapabilityPhase.EXPLORE,
            title="Problem Space Exploration",
            content=response,
            insights=insights,
            questions=questions
        )

    async def _phase_analyze(self, session: CapabilitySession) -> None:
        """Analyze gathered information and identify requirements."""
        focus_areas = session.metadata.get("focus_areas", [])

        # Get exploration results
        explore_step = next(
            (s for s in session.steps if s.phase == CapabilityPhase.EXPLORE),
            None
        )

        analysis_prompt = f"""
Basierend auf der Exploration, identifiziere konkrete Requirements.

URSPRÜNGLICHE IDEE:
{session.query}

EXPLORATION:
{explore_step.content if explore_step else "Keine Exploration verfügbar"}

{f"FOKUS-BEREICHE: {', '.join(focus_areas)}" if focus_areas else ""}

Erstelle:
1. FUNKTIONALE REQUIREMENTS: Was muss das System tun?
2. NICHT-FUNKTIONALE REQUIREMENTS: Performance, Sicherheit, Skalierbarkeit?
3. USER STORIES: Als [Rolle] möchte ich [Funktion] um [Nutzen]
4. AKZEPTANZKRITERIEN: Wann ist ein Requirement erfüllt?
5. PRIORISIERUNG: Must-have vs Nice-to-have

Formatiere als strukturierte Liste.
"""

        if self.llm_callback:
            response = await self._call_llm(analysis_prompt)
        else:
            response = self._generate_default_analysis(session.query)

        session.add_step(
            phase=CapabilityPhase.ANALYZE,
            title="Requirements Analysis",
            content=response,
            insights=self._extract_requirements(response)
        )

    async def _phase_synthesize(self, session: CapabilitySession) -> None:
        """Synthesize findings into a coherent specification."""
        # Gather all steps
        steps_content = "\n\n".join([
            f"### {s.title}\n{s.content}"
            for s in session.steps
        ])

        synthesis_prompt = f"""
Erstelle eine finale Requirements-Spezifikation basierend auf der Analyse.

ANALYSE:
{steps_content}

Erstelle ein strukturiertes Dokument mit:

# Requirements Specification: [Projekt/Feature Name]

## 1. Executive Summary
Kurze Zusammenfassung (2-3 Sätze)

## 2. Scope
Was ist in-scope und out-of-scope?

## 3. Functional Requirements
- FR1: ...
- FR2: ...

## 4. Non-Functional Requirements
- NFR1: ...
- NFR2: ...

## 5. User Stories
Mit Akzeptanzkriterien

## 6. Open Questions
Noch zu klärende Punkte

## 7. Next Steps
Empfohlene nächste Schritte (z.B. Design, Prototyp)
"""

        if self.llm_callback:
            specification = await self._call_llm(synthesis_prompt)
        else:
            specification = self._generate_default_specification(session.query)

        session.add_step(
            phase=CapabilityPhase.SYNTHESIZE,
            title="Requirements Specification",
            content=specification
        )

        # Create artifact
        session.add_artifact(
            artifact_type="requirements",
            title=f"Requirements: {session.query[:50]}",
            content=specification,
            metadata={"version": "1.0", "status": "draft"}
        )

    def _generate_default_exploration(self, query: str) -> str:
        """Fallback exploration when no LLM is available."""
        return f"""
## Exploration: {query}

### 1. VERSTÄNDNIS
Die Anfrage scheint sich auf "{query}" zu beziehen.

### 2. ZIELGRUPPE
- Primär: [Zu definieren]
- Sekundär: [Zu definieren]

### 3. ERFOLGSKRITERIEN
- [Zu definieren basierend auf Nutzerinterviews]

### 4. CONSTRAINTS
- Technisch: [Zu analysieren]
- Zeitlich: [Zu klären]
- Budget: [Zu klären]

### 5. OFFENE FRAGEN
- Was sind die Kernfunktionen?
- Wer sind die Hauptnutzer?
- Welche Integrationen sind nötig?
"""

    def _generate_default_analysis(self, query: str) -> str:
        """Fallback analysis when no LLM is available."""
        return f"""
## Requirements Analysis: {query}

### Funktionale Requirements
- FR1: [Aus Exploration ableiten]
- FR2: [Aus Exploration ableiten]

### Nicht-Funktionale Requirements
- NFR1: Performance - [Zu definieren]
- NFR2: Sicherheit - [Zu definieren]

### User Stories
- US1: Als Nutzer möchte ich [Funktion], um [Nutzen]

### Priorisierung
- Must-have: [Zu definieren]
- Nice-to-have: [Zu definieren]
"""

    def _generate_default_specification(self, query: str) -> str:
        """Fallback specification when no LLM is available."""
        return f"""
# Requirements Specification: {query}

## 1. Executive Summary
Dieses Dokument spezifiziert die Anforderungen für "{query}".

## 2. Scope
**In-Scope:**
- [Aus Analyse ableiten]

**Out-of-Scope:**
- [Explizit ausschließen]

## 3. Functional Requirements
- FR1: [Requirement]
- FR2: [Requirement]

## 4. Non-Functional Requirements
- NFR1: [Requirement]

## 5. User Stories
[User Stories mit Akzeptanzkriterien]

## 6. Open Questions
- [Offene Fragen]

## 7. Next Steps
1. Review dieser Spezifikation
2. Design-Phase starten (→ /design)
3. Implementierung planen (→ /implement)
"""

    def _parse_exploration(self, response: str) -> tuple[List[str], List[str]]:
        """Extract insights and questions from exploration response."""
        insights = []
        questions = []

        lines = response.split("\n")
        current_section = None

        for line in lines:
            line = line.strip()
            if not line:
                continue

            lower = line.lower()
            if "verständnis" in lower or "understanding" in lower:
                current_section = "insight"
            elif "fragen" in lower or "question" in lower:
                current_section = "question"
            elif line.startswith("- ") or line.startswith("* "):
                content = line[2:].strip()
                if current_section == "insight":
                    insights.append(content)
                elif current_section == "question":
                    questions.append(content)
                elif "?" in content:
                    questions.append(content)
                else:
                    insights.append(content)

        return insights[:10], questions[:10]

    def _extract_requirements(self, response: str) -> List[str]:
        """Extract requirement statements from analysis."""
        requirements = []
        lines = response.split("\n")

        for line in lines:
            line = line.strip()
            # Look for requirement patterns
            if line.startswith(("FR", "NFR", "- ", "* ")):
                req = line.lstrip("- *").strip()
                if req and len(req) > 10:
                    requirements.append(req)

        return requirements[:20]
