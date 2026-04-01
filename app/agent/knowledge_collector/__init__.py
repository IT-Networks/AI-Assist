"""
Knowledge Collector – Systematische Wissenserfassung aus multiplen Quellen.

Architektur:
- SourceProvider ABC: Einheitliches Interface für Wissensquellen (Confluence, Handbuch, ...)
- ResearchOrchestrator: Koordiniert Discovery → Planning → Execution → Synthesis
- ResearchAgent (SubAgent): Extrahiert Fakten aus einzelnen Seiten/Dokumenten
- KnowledgeSynthesizer: Synthetisiert Findings zu strukturierten MDs
- KnowledgeStore: Persistiert MDs + FTS5-Index für schnelle Suche
"""
