"""
KnowledgeSynthesizer – Synthetisiert ResearchFindings zu strukturierten Markdown-Dateien.

Verantwortlichkeiten:
- Gruppierung von Findings nach Kategorie
- LLM-gestützte Synthese zu kohärentem Wissens-Dokument
- Frontmatter-Generierung mit Metadaten
- Quellenangaben-Formatierung
"""

import logging
from datetime import datetime
from typing import Dict, List, Optional

from app.agent.knowledge_collector.models import ResearchFinding, ResearchPlan
from app.services.llm_client import llm_client as default_llm_client, TIMEOUT_TOOL

logger = logging.getLogger(__name__)

_SYNTHESIS_PROMPT = """Du bist ein technischer Redakteur. Deine Aufgabe ist es, die folgenden
extrahierten Fakten und Erkenntnisse zu einem strukturierten Wissens-Dokument zusammenzufassen.

THEMA: {topic}

EXTRAHIERTE FAKTEN ({count} Stück):
{findings_text}

QUELLEN:
{sources_text}

AUFGABE:
Erstelle ein strukturiertes Markdown-Dokument mit folgenden Abschnitten:

## Zusammenfassung
Eine prägnante Zusammenfassung (max. 200 Wörter) der wichtigsten Erkenntnisse.

## Fakten
Gruppiere die Fakten thematisch in Unterabschnitte (### Überschrift).
Jeder Fakt als Bullet-Point mit Quellenangabe in eckigen Klammern [Quelle: ...].

## Erkenntnisse
Nummerierte Liste der wichtigsten übergreifenden Erkenntnisse und Schlussfolgerungen.
Nur Dinge die aus den Fakten abgeleitet werden können – NICHTS erfinden.

## Quellen
Tabelle mit allen verwendeten Quellen (Titel, Typ, ID/URL).

REGELN:
- NUR Informationen verwenden die in den Fakten stehen
- Quellenangaben bei JEDEM Fakt
- Widersprüche explizit benennen
- Deutsche Sprache
- Keine Frontmatter (wird separat erzeugt)
"""


class KnowledgeSynthesizer:
    """Synthetisiert ResearchFindings zu einer strukturierten MD-Datei."""

    def __init__(self, model: str = ""):
        from app.core.config import settings
        self._model = model or settings.knowledge_base.synthesis_model or settings.llm.default_model

    async def synthesize(
        self,
        topic: str,
        plan: ResearchPlan,
        findings: List[ResearchFinding],
    ) -> str:
        """
        Synthetisiert Findings zu einem vollständigen MD-Dokument.

        Args:
            topic: Das recherchierte Thema
            plan: Der ausgeführte Research-Plan
            findings: Alle gesammelten Findings

        Returns:
            Vollständiger MD-String mit Frontmatter
        """
        if not findings:
            return self._build_empty_document(topic, plan)

        # Findings nach Kategorie gruppieren
        grouped = self._group_findings(findings)

        # Findings-Text für den Prompt aufbereiten
        findings_text = self._format_findings_for_prompt(grouped)

        # Quellen sammeln
        sources = self._collect_sources(findings)
        sources_text = "\n".join([
            f"- [{s['title']}] (Typ: {s['type']}, Provider: {s['provider']}, ID: {s['id']})"
            for s in sources
        ])

        # LLM-Synthese
        prompt = _SYNTHESIS_PROMPT.format(
            topic=topic,
            count=len(findings),
            findings_text=findings_text,
            sources_text=sources_text,
        )

        try:
            body_md = await default_llm_client.chat_quick(
                messages=[{"role": "user", "content": prompt}],
                model=self._model,
                temperature=0.2,
                max_tokens=4096,
            )
        except Exception as e:
            logger.error(f"[Synthesizer] LLM-Fehler: {e}")
            body_md = self._fallback_synthesis(topic, grouped, sources)

        # Frontmatter erzeugen und zusammensetzen
        frontmatter = self._build_frontmatter(topic, plan, findings, sources)
        return f"{frontmatter}\n{body_md.strip()}\n"

    def _build_frontmatter(
        self,
        topic: str,
        plan: ResearchPlan,
        findings: List[ResearchFinding],
        sources: List[Dict],
    ) -> str:
        """Erzeugt YAML-Frontmatter für die MD-Datei."""
        # Tags aus Findings extrahieren (häufigste Kategorien)
        categories = [f.category for f in findings]
        tags = list(set(categories))

        # Confidence berechnen (Mehrheit der Findings)
        confidences = [f.confidence for f in findings]
        high_count = confidences.count("high")
        total = len(confidences)
        if total > 0 and high_count / total > 0.5:
            overall_confidence = "high"
        elif high_count > 0:
            overall_confidence = "medium"
        else:
            overall_confidence = "low"

        providers = list(set(f.source_provider for f in findings))
        pdfs_count = sum(1 for f in findings if f.source_type == "pdf")

        # Topic YAML-safe escapen (Doppelpunkte, Anfuehrungszeichen)
        safe_topic = topic.replace('"', '\\"').replace(":", " -")
        safe_space = (plan.space_key or "_allgemein").replace('"', '')

        lines = [
            "---",
            f'title: "{safe_topic}"',
            f'date: "{datetime.now().strftime("%Y-%m-%d")}"',
            f"source: {', '.join(providers)}",
            f"space: {safe_space}",
            f"pages_analyzed: {plan.estimated_pages}",
            f"pdfs_analyzed: {pdfs_count}",
            f"findings_count: {len(findings)}",
            f"confidence: {overall_confidence}",
            f"providers: [{', '.join(providers)}]",
            "tags:",
        ]
        for tag in tags[:10]:
            safe_tag = tag.replace('"', '').replace(":", "").strip()
            if safe_tag:
                lines.append(f"  - {safe_tag}")
        lines.append("---")
        return "\n".join(lines)

    def _build_empty_document(self, topic: str, plan: ResearchPlan) -> str:
        """Erzeugt ein leeres Dokument wenn keine Findings vorhanden."""
        frontmatter = "\n".join([
            "---",
            f'title: "{topic}"',
            f'date: "{datetime.now().strftime("%Y-%m-%d")}"',
            "source: keine",
            f"space: {plan.space_key or '_allgemein'}",
            "pages_analyzed: 0",
            "findings_count: 0",
            "confidence: low",
            "tags: []",
            "---",
        ])
        body = (
            "## Zusammenfassung\n\n"
            f"Zu dem Thema \"{topic}\" konnten keine relevanten Fakten gefunden werden.\n\n"
            "## Fakten\n\nKeine Fakten extrahiert.\n\n"
            "## Erkenntnisse\n\nKeine Erkenntnisse.\n\n"
            "## Quellen\n\nKeine Quellen analysiert.\n"
        )
        return f"{frontmatter}\n\n{body}"

    @staticmethod
    def _group_findings(findings: List[ResearchFinding]) -> Dict[str, List[ResearchFinding]]:
        """Gruppiert Findings nach Kategorie."""
        grouped: Dict[str, List[ResearchFinding]] = {}
        for f in findings:
            grouped.setdefault(f.category, []).append(f)
        return grouped

    @staticmethod
    def _format_findings_for_prompt(grouped: Dict[str, List[ResearchFinding]]) -> str:
        """Formatiert gruppierte Findings als Text für den LLM-Prompt."""
        lines = []
        category_labels = {
            "fact": "Fakten",
            "process": "Prozesse",
            "decision": "Entscheidungen",
            "definition": "Definitionen",
        }
        for category, findings in grouped.items():
            label = category_labels.get(category, category.capitalize())
            lines.append(f"\n### {label}:")
            for f in findings:
                source_hint = f"[{f.source_title}]" if f.source_title else ""
                conf_hint = f" ({f.confidence})" if f.confidence != "medium" else ""
                lines.append(f"- {f.fact} {source_hint}{conf_hint}")
        return "\n".join(lines)

    @staticmethod
    def _collect_sources(findings: List[ResearchFinding]) -> List[Dict]:
        """Sammelt eindeutige Quellen aus den Findings."""
        seen = set()
        sources = []
        for f in findings:
            key = f"{f.source_provider}:{f.source_page_id}"
            if key not in seen:
                seen.add(key)
                sources.append({
                    "id": f.source_page_id,
                    "title": f.source_title,
                    "url": f.source_url,
                    "type": f.source_type,
                    "provider": f.source_provider,
                })
        return sources

    @staticmethod
    def _fallback_synthesis(
        topic: str,
        grouped: Dict[str, List[ResearchFinding]],
        sources: List[Dict],
    ) -> str:
        """Fallback-Synthese ohne LLM (bei Fehler)."""
        lines = [
            f"## Zusammenfassung\n",
            f"Zum Thema \"{topic}\" wurden {sum(len(v) for v in grouped.values())} Fakten gesammelt.\n",
            "",
            "## Fakten\n",
        ]

        category_labels = {
            "fact": "Allgemeine Fakten",
            "process": "Prozesse",
            "decision": "Entscheidungen",
            "definition": "Definitionen",
        }
        for category, findings in grouped.items():
            label = category_labels.get(category, category.capitalize())
            lines.append(f"### {label}")
            for f in findings:
                source_hint = f" [Quelle: {f.source_title}]" if f.source_title else ""
                lines.append(f"- {f.fact}{source_hint}")
            lines.append("")

        lines.append("## Erkenntnisse\n")
        lines.append("(Automatische Synthese nicht verfügbar – Rohdaten oben.)\n")

        lines.append("## Quellen\n")
        lines.append("| Quelle | Typ | ID |")
        lines.append("|--------|-----|-----|")
        for s in sources:
            lines.append(f"| {s['title']} | {s['type']} | {s['id']} |")
        lines.append("")

        return "\n".join(lines)
