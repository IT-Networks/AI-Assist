# Design: Enhanced MCP Commands mit Multi-Source Research

**Version:** 1.0
**Datum:** 2026-03-16
**Status:** Approved
**Basiert auf:** Brainstorming-Session zur MCP-Funktions-Verbesserung

---

## 1. Architektur-Übersicht

### 1.1 High-Level Komponenten-Diagramm

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              ENHANCED MCP SYSTEM                                 │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  ┌────────────────────────────────────────────────────────────────────────────┐ │
│  │                         MCP COMMAND LAYER                                   │ │
│  │  ┌──────────────────┐              ┌──────────────────┐                    │ │
│  │  │   /brainstorm    │              │     /design      │                    │ │
│  │  │                  │              │                  │                    │ │
│  │  │  • Use Cases     │              │  • Sequenz-Diag. │                    │ │
│  │  │  • Schaubilder   │              │  • UML/ERD       │                    │ │
│  │  │  • Stakeholder   │              │  • API-Specs     │                    │ │
│  │  └────────┬─────────┘              └────────┬─────────┘                    │ │
│  │           │                                  │                              │ │
│  │           └──────────────┬───────────────────┘                              │ │
│  │                          │                                                  │ │
│  │                          ▼                                                  │ │
│  │  ┌───────────────────────────────────────────────────────────────────────┐ │ │
│  │  │                    CONTEXT ORCHESTRATOR (NEU)                          │ │ │
│  │  │                                                                        │ │ │
│  │  │   ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  │ │ │
│  │  │   │   Skill     │  │  Research   │  │  Source     │  │   Output    │  │ │ │
│  │  │   │  Resolver   │  │  Router     │  │  Aggregator │  │  Formatter  │  │ │ │
│  │  │   └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  │ │ │
│  │  │          │                │                │                │         │ │ │
│  │  └──────────┼────────────────┼────────────────┼────────────────┼─────────┘ │ │
│  └─────────────┼────────────────┼────────────────┼────────────────┼───────────┘ │
│                │                │                │                │             │
│  ┌─────────────┼────────────────┼────────────────┼────────────────┼───────────┐ │
│  │             ▼                ▼                ▼                ▼           │ │
│  │  ┌─────────────────────────────────────────────────────────────────────┐  │ │
│  │  │                      DATA SOURCE LAYER                               │  │ │
│  │  │                                                                      │  │ │
│  │  │   ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐            │  │ │
│  │  │   │  Skills  │  │   Web    │  │Confluence│  │ Handbuch │            │  │ │
│  │  │   │(internal)│  │(external)│  │(internal)│  │(internal)│            │  │ │
│  │  │   └──────────┘  └──────────┘  └──────────┘  └──────────┘            │  │ │
│  │  │                                                                      │  │ │
│  │  │   ════════════════════════════════════════════════════════════════  │  │ │
│  │  │   │  INTERNAL ONLY  │           │  EXTERNAL SAFE  │                 │  │ │
│  │  │   │  (no web leak)  │           │  (anonymized)   │                 │  │ │
│  │  │   ════════════════════════════════════════════════════════════════  │  │ │
│  │  └─────────────────────────────────────────────────────────────────────┘  │ │
│  └───────────────────────────────────────────────────────────────────────────┘ │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 Datenfluss-Diagramm: Sichere Recherche

```
                                    USER QUERY
                                        │
                                        ▼
                    ┌───────────────────────────────────────┐
                    │         QUERY ANALYZER                 │
                    │  • Klassifiziert Query-Typ            │
                    │  • Identifiziert benötigte Quellen    │
                    │  • Extrahiert Suchbegriffe            │
                    └───────────────────┬───────────────────┘
                                        │
                    ┌───────────────────┼───────────────────┐
                    │                   │                   │
                    ▼                   ▼                   ▼
    ┌───────────────────────┐ ┌─────────────────┐ ┌─────────────────────┐
    │   INTERNAL RESEARCH   │ │  WEB RESEARCH   │ │ CONFLUENCE RESEARCH │
    │                       │ │                 │ │                     │
    │ • Skills/Knowledge    │ │ • Anonymized    │ │ • Space-Suche       │
    │ • Handbuch FTS        │ │   Query         │ │ • Seiten-Abruf      │
    │ • Lokale Dokumente    │ │ • No internal   │ │ • Attachment-Scan   │
    │                       │ │   context sent  │ │                     │
    └───────────┬───────────┘ └────────┬────────┘ └──────────┬──────────┘
                │                      │                      │
                │    ┌─────────────────┼─────────────────┐    │
                │    │                 │                 │    │
                │    ▼                 ▼                 ▼    │
                │  ┌─────────────────────────────────────────┐│
                │  │         RESULT AGGREGATOR               ││
                │  │  • Deduplizierung                       ││
                │  │  • Relevanz-Ranking                     ││
                │  │  • Quellen-Attribution                  ││
                └──│  • Token-Budget-Management              │┘
                   └─────────────────────────────────────────┘
                                        │
                                        ▼
                   ┌─────────────────────────────────────────┐
                   │         OUTPUT FORMATTER                 │
                   │  • Enterprise-Templates anwenden        │
                   │  • Diagramme generieren                 │
                   │  • Quellen-Referenzen einfügen         │
                   └─────────────────────────────────────────┘
```

---

## 2. Komponenten-Design

### 2.1 Context Orchestrator

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        CONTEXT ORCHESTRATOR                              │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │                      SKILL RESOLVER                                 │ │
│  │                                                                     │ │
│  │  Input:  command_name: str, session_id: str                        │ │
│  │  Output: List[Skill] (aktivierte Enterprise-Skills)                │ │
│  │                                                                     │ │
│  │  Logik:                                                            │ │
│  │  1. Finde Skills mit trigger_commands = command_name               │ │
│  │  2. Prüfe ob Skill für Session aktiviert ist                       │ │
│  │  3. Lade system_prompt und knowledge_sources                       │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                          │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │                      RESEARCH ROUTER                                │ │
│  │                                                                     │ │
│  │  Input:  query: str, sources: List[SourceType], config: Config     │ │
│  │  Output: ResearchPlan (welche Quellen wie abgefragt werden)        │ │
│  │                                                                     │ │
│  │  Routing-Matrix:                                                   │ │
│  │  ┌─────────────┬────────────┬─────────────┬───────────────────┐    │ │
│  │  │ Query-Typ   │ Web        │ Confluence  │ Handbuch/Skills   │    │ │
│  │  ├─────────────┼────────────┼─────────────┼───────────────────┤    │ │
│  │  │ Technologie │ ✓ (anonym) │ ✓           │ ✓                 │    │ │
│  │  │ Best Pract. │ ✓ (anonym) │ ✓           │ ✓                 │    │ │
│  │  │ Fachlich    │ ✗          │ ✓           │ ✓                 │    │ │
│  │  │ Intern      │ ✗          │ ✓           │ ✓                 │    │ │
│  │  └─────────────┴────────────┴─────────────┴───────────────────┘    │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                          │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │                     SOURCE AGGREGATOR                               │ │
│  │                                                                     │ │
│  │  Input:  List[ResearchResult] von verschiedenen Quellen            │ │
│  │  Output: AggregatedContext mit Relevanz-Scores                     │ │
│  │                                                                     │ │
│  │  Aggregations-Logik:                                               │ │
│  │  1. Deduplizierung (ähnliche Inhalte zusammenfassen)               │ │
│  │  2. Relevanz-Ranking (BM25 + Recency + Source-Weight)              │ │
│  │  3. Token-Budget einhalten (wichtigste zuerst)                     │ │
│  │  4. Quellen-Attribution (woher stammt was)                         │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                          │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │                     OUTPUT FORMATTER                                │ │
│  │                                                                     │ │
│  │  Input:  command: str, context: AggregatedContext, skills: Skills  │ │
│  │  Output: FormattedPrompt mit Templates und Anweisungen             │ │
│  │                                                                     │ │
│  │  Templates pro Command:                                            │ │
│  │  • brainstorm → Use Cases, Schaubilder, Stakeholder                │ │
│  │  • design → Sequenzdiagramme, UML, API-Specs                       │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 2.2 Research Router - Detaildesign

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         RESEARCH ROUTER                                  │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │                    QUERY CLASSIFIER                               │   │
│  │                                                                   │   │
│  │  Klassifiziert Query in Kategorien:                              │   │
│  │                                                                   │   │
│  │  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐   │   │
│  │  │   TECHNICAL     │  │   BUSINESS      │  │   INTERNAL      │   │   │
│  │  │                 │  │                 │  │                 │   │   │
│  │  │ • Frameworks    │  │ • Prozesse      │  │ • Projekte      │   │   │
│  │  │ • Libraries     │  │ • Domäne        │  │ • Personen      │   │   │
│  │  │ • Patterns      │  │ • Anforderungen │  │ • Services      │   │   │
│  │  │                 │  │                 │  │                 │   │   │
│  │  │ Web: ✓ SAFE     │  │ Web: ⚠ CAREFUL │  │ Web: ✗ NEVER    │   │   │
│  │  └─────────────────┘  └─────────────────┘  └─────────────────┘   │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │                    QUERY SANITIZER                                │   │
│  │                                                                   │   │
│  │  Entfernt sensible Daten aus Web-Queries:                        │   │
│  │                                                                   │   │
│  │  Input:  "Wie integriere ich OrderService mit SAP?"              │   │
│  │  Output: "Wie integriere ich Java Service mit SAP?"              │   │
│  │          ─────────────────────────────────────                   │   │
│  │          Entfernt: Service-Namen, Projekt-IDs, Personen          │   │
│  │                                                                   │   │
│  │  Sanitization Rules:                                             │   │
│  │  • Interne Service-Namen → generische Begriffe                   │   │
│  │  • Projekt-Codes → entfernen                                     │   │
│  │  • Personen-Namen → entfernen                                    │   │
│  │  • IP-Adressen/URLs → entfernen                                  │   │
│  │  • Datenbank-Namen → generische Begriffe                         │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │                    SOURCE SELECTOR                                │   │
│  │                                                                   │   │
│  │  Wählt Quellen basierend auf Query-Klassifikation:               │   │
│  │                                                                   │   │
│  │  ┌─────────────────────────────────────────────────────────────┐ │   │
│  │  │  Query: "Best Practices für REST API Versionierung"        │ │   │
│  │  │                                                             │ │   │
│  │  │  Klassifikation: TECHNICAL                                  │ │   │
│  │  │                                                             │ │   │
│  │  │  Ausgewählte Quellen:                                       │ │   │
│  │  │  ┌───────────────┬──────────┬────────────────────────────┐ │ │   │
│  │  │  │ Quelle        │ Priorität│ Query                      │ │ │   │
│  │  │  ├───────────────┼──────────┼────────────────────────────┤ │ │   │
│  │  │  │ Skills        │ 1        │ REST API Versionierung     │ │ │   │
│  │  │  │ Confluence    │ 2        │ REST API Richtlinien       │ │ │   │
│  │  │  │ Web           │ 3        │ REST API versioning best   │ │ │   │
│  │  │  │               │          │ practices 2026             │ │ │   │
│  │  │  └───────────────┴──────────┴────────────────────────────┘ │ │   │
│  │  └─────────────────────────────────────────────────────────────┘ │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Sequenzdiagramme

### 3.1 Brainstorm mit Multi-Source Research

```
┌─────┐  ┌───────────┐  ┌──────────┐  ┌──────────┐  ┌─────────┐  ┌─────────┐  ┌─────┐
│User │  │Orchestrator│  │SkillMgr │  │WebSearch │  │Confluence│  │Handbuch │  │ LLM │
└──┬──┘  └─────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬────┘  └──┬──┘
   │           │             │             │             │             │          │
   │ /brainstorm "Feature X" │             │             │             │          │
   │──────────▶│             │             │             │             │          │
   │           │             │             │             │             │          │
   │           │ get_skills_for_command("brainstorm")    │             │          │
   │           │────────────▶│             │             │             │          │
   │           │             │             │             │             │          │
   │           │◀────────────│             │             │             │          │
   │           │ [enterprise-brainstorm,   │             │             │          │
   │           │  arch-guidelines]         │             │             │          │
   │           │             │             │             │             │          │
   │           │ analyze_query("Feature X")│             │             │          │
   │           │─────────────────────────────────────────────────────────────────▶│
   │           │             │             │             │             │          │
   │           │◀─────────────────────────────────────────────────────────────────│
   │           │ {type: TECHNICAL,         │             │             │          │
   │           │  keywords: ["Feature", ...]}            │             │          │
   │           │             │             │             │             │          │
   │           │ ═══════════ PARALLEL RESEARCH ═══════════             │          │
   │           │             │             │             │             │          │
   │           │ search_knowledge("Feature X")           │             │          │
   │           │────────────▶│             │             │             │          │
   │           │             │             │             │             │          │
   │           │ web_search("Feature best practices 2026")             │          │
   │           │────────────────────────▶│ │             │             │          │
   │           │             │             │             │             │          │
   │           │ search_confluence("Feature Architektur")│             │          │
   │           │─────────────────────────────────────────▶│            │          │
   │           │             │             │             │             │          │
   │           │ search_handbook("Feature")│             │             │          │
   │           │────────────────────────────────────────────────────▶│ │          │
   │           │             │             │             │             │          │
   │           │◀────────────│◀────────────│◀────────────│◀────────────│          │
   │           │ [skill_results, web_results, confluence_results, handbook_results]
   │           │             │             │             │             │          │
   │           │ aggregate_and_rank(results)             │             │          │
   │           │─────────────────────────────────────────────────────────────────▶│
   │           │             │             │             │             │          │
   │           │ build_prompt(skills, aggregated_context)│             │          │
   │           │─────────────────────────────────────────────────────────────────▶│
   │           │             │             │             │             │          │
   │           │◀─────────────────────────────────────────────────────────────────│
   │           │ {use_cases, diagrams, stakeholders,     │             │          │
   │           │  sources_cited}           │             │             │          │
   │           │             │             │             │             │          │
   │◀──────────│             │             │             │             │          │
   │ Brainstorm│Output mit   │             │             │             │          │
   │ Enterprise│-Format      │             │             │             │          │
   │           │             │             │             │             │          │
```

### 3.2 Design mit UML-Generierung

```
┌─────┐  ┌───────────┐  ┌──────────┐  ┌───────────┐  ┌─────────┐  ┌─────┐
│User │  │Orchestrator│  │SkillMgr │  │DiagramGen │  │ Sources │  │ LLM │
└──┬──┘  └─────┬─────┘  └────┬─────┘  └─────┬─────┘  └────┬────┘  └──┬──┘
   │           │             │              │             │          │
   │ /design "Payment API"   │              │             │          │
   │──────────▶│             │              │             │          │
   │           │             │              │             │          │
   │           │ get_skills_for_command("design")        │          │
   │           │────────────▶│              │             │          │
   │           │             │              │             │          │
   │           │◀────────────│              │             │          │
   │           │ [enterprise-design,        │             │          │
   │           │  api-guidelines]           │             │          │
   │           │             │              │             │          │
   │           │ get_output_templates("design")          │          │
   │           │────────────▶│              │             │          │
   │           │             │              │             │          │
   │           │◀────────────│              │             │          │
   │           │ {sequence_diagram: template,            │          │
   │           │  class_diagram: template,  │             │          │
   │           │  api_spec: template}       │             │          │
   │           │             │              │             │          │
   │           │ ═══════════ RESEARCH PHASE ═══════════  │          │
   │           │             │              │             │          │
   │           │ parallel_research("Payment API patterns")          │
   │           │──────────────────────────────────────────▶          │
   │           │             │              │             │          │
   │           │◀──────────────────────────────────────────          │
   │           │ [api_patterns, existing_services,       │          │
   │           │  security_requirements]    │             │          │
   │           │             │              │             │          │
   │           │ ═══════════ DESIGN PHASE ═══════════════           │
   │           │             │              │             │          │
   │           │ generate_design(context, templates)     │          │
   │           │─────────────────────────────────────────────────▶│ │
   │           │             │              │             │          │
   │           │◀─────────────────────────────────────────────────│ │
   │           │ {components, interactions, │             │          │
   │           │  api_endpoints}            │             │          │
   │           │             │              │             │          │
   │           │ ═══════════ DIAGRAM GENERATION ═════════           │
   │           │             │              │             │          │
   │           │ generate_sequence_diagram(interactions) │          │
   │           │────────────────────────────▶│            │          │
   │           │             │              │             │          │
   │           │◀────────────────────────────│            │          │
   │           │ ```mermaid                 │             │          │
   │           │ sequenceDiagram            │             │          │
   │           │ ...```                     │             │          │
   │           │             │              │             │          │
   │           │ generate_class_diagram(components)      │          │
   │           │────────────────────────────▶│            │          │
   │           │             │              │             │          │
   │           │◀────────────────────────────│            │          │
   │           │ ```mermaid                 │             │          │
   │           │ classDiagram               │             │          │
   │           │ ...```                     │             │          │
   │           │             │              │             │          │
   │◀──────────│             │              │             │          │
   │ Design mit│Diagrammen   │              │             │          │
   │ und Specs │             │              │             │          │
```

---

## 4. Datenmodelle

### 4.1 Erweitertes Skill-Modell

```python
# app/models/skill.py

from enum import Enum
from typing import List, Optional, Dict
from pydantic import BaseModel
from datetime import datetime


class ActivationMode(str, Enum):
    ALWAYS = "always"
    ON_DEMAND = "on-demand"
    AUTO = "auto"
    COMMAND_TRIGGER = "command-trigger"  # NEU


class ResearchScope(str, Enum):
    """Definiert welche Quellen der Skill für Research nutzen darf."""
    INTERNAL_ONLY = "internal-only"      # Nur Skills, Handbuch, Confluence
    EXTERNAL_SAFE = "external-safe"      # Web mit Sanitization
    ALL = "all"                          # Alle Quellen


class OutputTemplate(BaseModel):
    """Template für strukturierte Ausgabe."""
    name: str
    required: bool = True
    format: Optional[str] = None
    example: Optional[str] = None


class DiagramConfig(BaseModel):
    """Konfiguration für Diagramm-Generierung."""
    type: str  # "sequence", "class", "component", "erd", "usecase"
    format: str = "mermaid"  # "mermaid", "plantuml", "ascii"
    template: Optional[str] = None


class SkillActivation(BaseModel):
    """Aktivierungskonfiguration für Skills."""
    mode: ActivationMode
    trigger_words: List[str] = []
    trigger_commands: List[str] = []  # NEU: z.B. ["brainstorm", "design"]


class ResearchConfig(BaseModel):
    """Konfiguration für Research-Verhalten."""
    scope: ResearchScope = ResearchScope.INTERNAL_ONLY
    allowed_sources: List[str] = ["skills", "handbook", "confluence"]
    sanitize_queries: bool = True
    max_web_results: int = 5
    max_internal_results: int = 10


class OutputConfig(BaseModel):
    """Konfiguration für Output-Formatierung."""
    templates: List[OutputTemplate] = []
    diagrams: List[DiagramConfig] = []
    include_sources: bool = True
    enterprise_formatting: bool = True
```

### 4.2 Research-Datenmodelle

```python
# app/models/research.py

from enum import Enum
from typing import List, Optional, Dict, Any
from pydantic import BaseModel
from datetime import datetime


class SourceType(str, Enum):
    SKILL = "skill"
    HANDBOOK = "handbook"
    CONFLUENCE = "confluence"
    WEB = "web"
    PDF = "pdf"


class QueryClassification(str, Enum):
    TECHNICAL = "technical"
    BUSINESS = "business"
    INTERNAL = "internal"
    MIXED = "mixed"


class ResearchQuery(BaseModel):
    """Eine einzelne Research-Query."""
    original: str
    sanitized: str
    classification: QueryClassification
    keywords: List[str]
    target_source: SourceType
    priority: int = 1


class ResearchResult(BaseModel):
    """Ein einzelnes Research-Ergebnis."""
    source: SourceType
    source_name: str
    content: str
    relevance_score: float
    url: Optional[str] = None
    metadata: Dict[str, Any] = {}


class AggregatedContext(BaseModel):
    """Aggregierter Kontext aus allen Quellen."""
    query: str
    classification: QueryClassification
    results: List[ResearchResult]
    total_tokens: int
    sources_used: List[SourceType]
    timestamp: datetime
```

---

## 5. API-Spezifikation

### 5.1 Research API

```yaml
openapi: 3.0.3
info:
  title: Enhanced MCP Research API
  version: 1.0.0

paths:
  /api/research/execute:
    post:
      summary: Führt Multi-Source Research aus
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/ResearchRequest'
      responses:
        '200':
          description: Research-Ergebnisse
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/AggregatedContext'

  /api/skills/for-command/{command}:
    get:
      summary: Gibt Skills für ein MCP-Command zurück
      parameters:
        - name: command
          in: path
          required: true
          schema:
            type: string
        - name: session_id
          in: query
          schema:
            type: string
      responses:
        '200':
          description: Liste der aktivierten Skills
          content:
            application/json:
              schema:
                type: array
                items:
                  $ref: '#/components/schemas/SkillSummary'

components:
  schemas:
    ResearchRequest:
      type: object
      required:
        - query
        - command
      properties:
        query:
          type: string
        command:
          type: string
          enum: [brainstorm, design, implement]
        session_id:
          type: string
        sources:
          type: array
          items:
            type: string
            enum: [skill, handbook, confluence, web]
        max_results:
          type: integer
          default: 20
```

---

## 6. Implementierungsplan

### 6.1 Phasen-Übersicht

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        IMPLEMENTIERUNGSPLAN                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  Phase 1: Skill-Erweiterung (1-2 Tage)                                      │
│  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━                                      │
│  □ Skill-Modell um trigger_commands erweitern                               │
│  □ SkillManager.get_skills_for_command() implementieren                     │
│  □ Beispiel-Skills erstellen                                                │
│                                                                              │
│  Phase 2: Research Router (2-3 Tage)                                        │
│  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━                                       │
│  □ Query Classifier implementieren                                          │
│  □ Query Sanitizer für Web-Queries                                          │
│  □ Parallele Research-Ausführung                                            │
│  □ Result Aggregator mit Ranking                                            │
│                                                                              │
│  Phase 3: Output Formatter (1-2 Tage)                                       │
│  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━                                      │
│  □ Template-Engine für strukturierte Ausgabe                                │
│  □ Diagramm-Generierung (Mermaid/ASCII)                                     │
│  □ Quellen-Attribution                                                      │
│                                                                              │
│  Phase 4: UI Integration (1 Tag)                                            │
│  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━                                          │
│  □ Skill-Aktivierung für Commands in Settings                               │
│  □ Research-Source-Toggles                                                  │
│                                                                              │
│  Phase 5: Testing & Dokumentation (1 Tag)                                   │
│  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━                                   │
│  □ Integration Tests                                                        │
│  □ Dokumentation aktualisieren                                              │
│                                                                              │
│  ════════════════════════════════════════════════════════════════════════   │
│  Geschätzter Gesamtaufwand: 6-9 Tage                                        │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 7. Beispiel-Skills

Siehe separate Dateien:
- `skills/enterprise-brainstorm.yaml`
- `skills/enterprise-design.yaml`
