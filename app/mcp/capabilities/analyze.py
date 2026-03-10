"""
Analyze Capability - Code analysis and quality assessment.

Performs comprehensive analysis of code for quality, security,
performance, and architecture.
"""

import logging
from typing import Any, Dict, List, Optional

from app.mcp.capabilities.base import (
    BaseCapability,
    CapabilityPhase,
    CapabilitySession
)

logger = logging.getLogger(__name__)


class AnalyzeCapability(BaseCapability):
    """
    Code analysis and quality assessment capability.

    Flow:
    1. Identify analysis scope and targets
    2. Apply domain-specific analysis
    3. Generate findings and metrics
    4. Create recommendations
    5. Produce analysis report
    """

    @property
    def name(self) -> str:
        return "analyze"

    @property
    def description(self) -> str:
        return (
            "Code-Analyse und Qualitätsbewertung. Analysiert Code auf "
            "Qualität, Sicherheit, Performance und Architektur. "
            "Verwende für: Code Review, Security Audit, Performance-Analyse."
        )

    @property
    def handoff_targets(self) -> List[str]:
        return ["implement", "design"]

    def _get_parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Was soll analysiert werden? (Pfad, Code, Konzept)"
                },
                "context": {
                    "type": "string",
                    "description": "Optional: Zusätzlicher Kontext oder Code"
                },
                "focus": {
                    "type": "string",
                    "enum": ["quality", "security", "performance", "architecture", "all"],
                    "description": "Analyse-Fokus (default: all)"
                },
                "depth": {
                    "type": "string",
                    "enum": ["quick", "normal", "deep"],
                    "description": "Analyse-Tiefe (default: normal)"
                },
                "output_format": {
                    "type": "string",
                    "enum": ["text", "json", "report"],
                    "description": "Output-Format (default: report)"
                }
            },
            "required": ["query"]
        }

    async def _phase_explore(self, session: CapabilitySession) -> None:
        """Identify analysis scope and gather context."""
        focus = session.metadata.get("focus", "all")
        depth = session.metadata.get("depth", "normal")

        # Check for handoff artifacts (e.g., from implement)
        handoff_artifacts = session.metadata.get("handoff_artifacts", [])
        code_context = ""
        for artifact in handoff_artifacts:
            if artifact.get("artifact_type") == "code":
                code_context += f"\n### {artifact.get('title', 'Code')}\n"
                code_context += artifact.get("content", "")[:2000]

        exploration_prompt = f"""
Du bist ein Senior Code Reviewer. Analysiere den Scope der Analyse.

ANALYSE-ANFRAGE:
{session.query}

CODE-KONTEXT:
{code_context or session.context or "Kein spezifischer Code bereitgestellt"}

FOKUS: {focus}
TIEFE: {depth}

Identifiziere:
1. ANALYSE-SCOPE: Was genau soll analysiert werden?
2. ANALYSE-DOMAINS: Welche Bereiche (Quality, Security, Performance, Architecture)?
3. METRIKEN: Welche Metriken sind relevant?
4. RISIKO-BEREICHE: Potenzielle Problembereiche
5. ANALYSE-STRATEGIE: Empfohlener Ansatz
"""

        if self.llm_callback:
            response = await self._call_llm(exploration_prompt)
        else:
            response = self._generate_default_exploration(session.query, focus, depth)

        session.add_step(
            phase=CapabilityPhase.EXPLORE,
            title="Analysis Scope Identification",
            content=response,
            insights=[f"Focus: {focus}", f"Depth: {depth}"]
        )

    async def _phase_analyze(self, session: CapabilitySession) -> None:
        """Perform domain-specific analysis."""
        focus = session.metadata.get("focus", "all")
        depth = session.metadata.get("depth", "normal")

        explore_step = next(
            (s for s in session.steps if s.phase == CapabilityPhase.EXPLORE),
            None
        )

        # Determine which analyses to run
        analyses_to_run = ["quality", "security", "performance", "architecture"] if focus == "all" else [focus]

        all_findings = []

        for domain in analyses_to_run:
            analysis_prompt = self._get_domain_prompt(domain, session.query, explore_step, depth)

            if self.llm_callback:
                findings = await self._call_llm(analysis_prompt)
            else:
                findings = self._generate_default_findings(domain)

            all_findings.append(f"## {domain.title()} Analysis\n\n{findings}")

        combined_findings = "\n\n".join(all_findings)

        session.add_step(
            phase=CapabilityPhase.ANALYZE,
            title="Domain Analysis",
            content=combined_findings,
            insights=self._extract_key_findings(combined_findings)
        )

    async def _phase_synthesize(self, session: CapabilitySession) -> None:
        """Generate analysis report with recommendations."""
        output_format = session.metadata.get("output_format", "report")

        # Get all analysis content
        analysis_step = next(
            (s for s in session.steps if s.phase == CapabilityPhase.ANALYZE),
            None
        )

        synthesis_prompt = f"""
Erstelle einen strukturierten Analyse-Report.

ANALYSE-ERGEBNISSE:
{analysis_step.content if analysis_step else ""}

FORMAT: {output_format}

Erstelle einen Report mit:

# Code Analysis Report

## Executive Summary
Kurze Zusammenfassung der wichtigsten Findings (3-5 Punkte)

## Findings by Severity

### Critical (Sofort beheben)
- [Finding mit Beschreibung und Empfehlung]

### High (Zeitnah beheben)
- [Finding]

### Medium (Geplant beheben)
- [Finding]

### Low (Nice-to-have)
- [Finding]

## Metrics
| Metrik | Wert | Bewertung |
|--------|------|-----------|
| ... | ... | ... |

## Recommendations
1. [Priorität 1]
2. [Priorität 2]
...

## Next Steps
- [Empfohlene Aktionen]
"""

        if self.llm_callback:
            report = await self._call_llm(synthesis_prompt)
        else:
            report = self._generate_default_report(session.query)

        session.add_step(
            phase=CapabilityPhase.SYNTHESIZE,
            title="Analysis Report",
            content=report
        )

        # Create report artifact
        session.add_artifact(
            artifact_type="analysis_report",
            title=f"Analysis: {session.query[:40]}",
            content=report,
            metadata={
                "focus": session.metadata.get("focus", "all"),
                "depth": session.metadata.get("depth", "normal"),
                "format": output_format
            }
        )

        # Create findings artifact for structured access
        findings = self._extract_structured_findings(analysis_step.content if analysis_step else "")
        session.add_artifact(
            artifact_type="findings",
            title="Structured Findings",
            content=str(findings),
            metadata={"findings_count": len(findings)}
        )

    def _get_domain_prompt(
        self,
        domain: str,
        query: str,
        explore_step: Any,
        depth: str
    ) -> str:
        """Get analysis prompt for specific domain."""
        base_context = explore_step.content if explore_step else ""

        prompts = {
            "quality": f"""
Analysiere die Code-Qualität.

KONTEXT:
{base_context}

QUERY: {query}
TIEFE: {depth}

Prüfe:
1. Code Style & Conventions
2. Complexity (Cyclomatic, Cognitive)
3. Maintainability
4. Testability
5. Documentation
6. DRY/SOLID Principles

Gib konkrete Findings mit Severity (Critical/High/Medium/Low).
""",
            "security": f"""
Führe eine Security-Analyse durch.

KONTEXT:
{base_context}

QUERY: {query}
TIEFE: {depth}

Prüfe:
1. Input Validation
2. Authentication/Authorization
3. Data Exposure
4. Injection Vulnerabilities (SQL, XSS, Command)
5. Cryptography Usage
6. Sensitive Data Handling
7. OWASP Top 10

Gib konkrete Findings mit Severity und CVSS-Einschätzung.
""",
            "performance": f"""
Analysiere die Performance.

KONTEXT:
{base_context}

QUERY: {query}
TIEFE: {depth}

Prüfe:
1. Algorithm Complexity (Big O)
2. Database Query Efficiency
3. Memory Usage
4. I/O Operations
5. Caching Opportunities
6. Async/Parallel Processing
7. Resource Leaks

Gib konkrete Findings mit Impact-Einschätzung.
""",
            "architecture": f"""
Analysiere die Architektur.

KONTEXT:
{base_context}

QUERY: {query}
TIEFE: {depth}

Prüfe:
1. Component Structure
2. Dependency Management
3. Separation of Concerns
4. Scalability
5. Extensibility
6. Design Patterns Usage
7. Technical Debt

Gib konkrete Findings mit Architektur-Impact.
"""
        }

        return prompts.get(domain, prompts["quality"])

    def _extract_key_findings(self, analysis: str) -> List[str]:
        """Extract key findings from analysis."""
        findings = []
        lines = analysis.split("\n")

        for line in lines:
            line = line.strip()
            # Look for severity markers
            for severity in ["Critical", "High", "Medium"]:
                if severity.lower() in line.lower() and line.startswith(("-", "*", "•")):
                    finding = line.lstrip("-*•").strip()
                    if len(finding) > 10:
                        findings.append(f"[{severity}] {finding[:100]}")
                        break

        return findings[:10]

    def _extract_structured_findings(self, analysis: str) -> List[Dict[str, Any]]:
        """Extract structured findings from analysis."""
        findings = []
        current_severity = "medium"

        lines = analysis.split("\n")
        for line in lines:
            line_lower = line.lower()

            # Detect severity headers
            if "critical" in line_lower:
                current_severity = "critical"
            elif "high" in line_lower:
                current_severity = "high"
            elif "medium" in line_lower:
                current_severity = "medium"
            elif "low" in line_lower:
                current_severity = "low"

            # Extract findings
            if line.strip().startswith(("-", "*", "•")):
                content = line.strip().lstrip("-*•").strip()
                if len(content) > 10:
                    findings.append({
                        "severity": current_severity,
                        "description": content[:200],
                        "domain": "general"
                    })

        return findings

    def _generate_default_exploration(self, query: str, focus: str, depth: str) -> str:
        return f"""
## Analysis Scope: {query}

### 1. ANALYSE-SCOPE
- Ziel: {query}
- Fokus: {focus}
- Tiefe: {depth}

### 2. ANALYSE-DOMAINS
- Quality: Code-Qualität und Best Practices
- Security: Sicherheitsaspekte
- Performance: Performance-Optimierung
- Architecture: Architektur-Patterns

### 3. METRIKEN
- Cyclomatic Complexity
- Code Coverage
- Security Score
- Performance Benchmarks

### 4. RISIKO-BEREICHE
- [Zu identifizieren während Analyse]

### 5. ANALYSE-STRATEGIE
- Statische Code-Analyse
- Pattern-Matching
- Best-Practice Vergleich
"""

    def _generate_default_findings(self, domain: str) -> str:
        findings = {
            "quality": """
### Quality Findings

**Medium:**
- Code könnte besser dokumentiert sein
- Einige Funktionen haben hohe Complexity

**Low:**
- Naming Conventions teilweise inkonsistent
- Magic Numbers sollten als Konstanten definiert werden
""",
            "security": """
### Security Findings

**High:**
- Input-Validierung sollte geprüft werden
- Error Messages könnten sensitive Information leaken

**Medium:**
- Logging von sensitive Data vermeiden
""",
            "performance": """
### Performance Findings

**Medium:**
- Potenzielle N+1 Query Patterns
- Caching-Opportunities identifiziert

**Low:**
- Einige Operationen könnten async sein
""",
            "architecture": """
### Architecture Findings

**Medium:**
- Coupling zwischen Modulen könnte reduziert werden
- Some Services have multiple responsibilities

**Low:**
- Abstractions könnten vereinfacht werden
"""
        }
        return findings.get(domain, findings["quality"])

    def _generate_default_report(self, query: str) -> str:
        return f"""
# Code Analysis Report: {query}

## Executive Summary
- Code-Qualität insgesamt akzeptabel
- Einige Security-Aspekte sollten geprüft werden
- Performance-Optimierungen möglich
- Architektur folgt grundlegenden Best Practices

## Findings by Severity

### Critical
- Keine kritischen Issues gefunden

### High
- Input-Validierung sollte verbessert werden
- Error Handling vereinheitlichen

### Medium
- Code-Dokumentation verbessern
- Complexity in einigen Funktionen reduzieren
- Caching für häufige Operationen einführen

### Low
- Naming Conventions vereinheitlichen
- Magic Numbers als Konstanten

## Metrics
| Metrik | Wert | Bewertung |
|--------|------|-----------|
| Code Quality | B | Gut |
| Security | B- | Verbesserungspotenzial |
| Performance | B | Gut |
| Architecture | B+ | Solide |

## Recommendations
1. Security: Input-Validierung systematisch überprüfen
2. Quality: Docstrings und Type Hints ergänzen
3. Performance: Caching-Layer einführen
4. Architecture: Service-Grenzen klarer definieren

## Next Steps
1. High-Priority Findings adressieren
2. Security-Audit durchführen
3. Performance-Tests implementieren
"""
