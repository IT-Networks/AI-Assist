"""
Intent Classifier - Classifies user messages before pipeline decisions.

Provides a fast, local (no LLM call) classification of user intent
to determine which pipeline stages should be activated.

This prevents premature tool calls and unnecessary Enhancement/TaskDecomposition
for simple questions or advisory requests.
"""

import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Set

logger = logging.getLogger(__name__)


class Intent(str, Enum):
    """User intent classification."""
    DIRECT = "direct"       # Concept/advice/smalltalk — no tools needed
    LOOKUP = "lookup"       # Simple question — read-only tools at most
    GUIDED = "guided"       # Single change/task — standard tool access
    COMPLEX = "complex"     # Multi-step task — full pipeline


@dataclass
class PipelineConfig:
    """Determines which pipeline stages are active for a given intent."""
    enhancement: bool           # MCP context collection
    task_decomposition: bool    # Task planner
    first_call_with_tools: bool # Whether first LLM call includes tools
    tool_restriction: str       # "none" | "read_only" | "all"

    @staticmethod
    def for_intent(intent: Intent) -> "PipelineConfig":
        """Create PipelineConfig based on intent."""
        return _PIPELINE_CONFIGS[intent]


@dataclass
class IntentResult:
    """Result of intent classification."""
    intent: Intent
    confidence: float       # 0.0-1.0
    reasoning: str          # Debug info
    pipeline: PipelineConfig

    @property
    def skip_enhancement(self) -> bool:
        return not self.pipeline.enhancement

    @property
    def skip_task_decomposition(self) -> bool:
        return not self.pipeline.task_decomposition


# ══════════════════════════════════════════════════════════════════════════════
# Pipeline configs per intent
# ══════════════════════════════════════════════════════════════════════════════

_PIPELINE_CONFIGS = {
    Intent.DIRECT: PipelineConfig(
        enhancement=False,
        task_decomposition=False,
        first_call_with_tools=False,
        tool_restriction="none",
    ),
    Intent.LOOKUP: PipelineConfig(
        enhancement=False,
        task_decomposition=False,
        first_call_with_tools=True,
        tool_restriction="read_only",
    ),
    Intent.GUIDED: PipelineConfig(
        enhancement=False,
        task_decomposition=False,
        first_call_with_tools=True,
        tool_restriction="all",
    ),
    Intent.COMPLEX: PipelineConfig(
        enhancement=True,
        task_decomposition=True,
        first_call_with_tools=True,
        tool_restriction="all",
    ),
}


# ══════════════════════════════════════════════════════════════════════════════
# Pattern definitions
# ══════════════════════════════════════════════════════════════════════════════

# DIRECT: Pure advice, concepts, greetings, confirmations
_DIRECT_PATTERNS: List[re.Pattern] = [
    # Questions about concepts (no code reference)
    re.compile(
        r"^(wie\s+w[uü]rde|wie\s+wuerde|was\s+ist\s+(besser|der\s+unterschied|best\s+practice)|"
        r"was\s+meinst\s+du|was\s+empfiehlst|was\s+denkst\s+du|"
        r"kannst\s+du\s+erkl[aä]r|was\s+h[aä]ltst\s+du|"
        r"how\s+would|what\s+is\s+(better|the\s+difference|best\s+practice)|"
        r"what\s+do\s+you\s+(think|recommend)|can\s+you\s+explain)",
        re.IGNORECASE,
    ),
    # Greetings, smalltalk, and casual questions
    re.compile(
        r"^(hallo|hi|hey|moin|guten\s+(morgen|tag|abend)|"
        r"danke|vielen\s+dank|super|perfekt|ok|alles\s+klar|"
        r"hello|thanks|thank\s+you|great|perfect|okay)"
        r"(\s*[!.,?]|\s+wie\s+geht).*$",
        re.IGNORECASE,
    ),
]

# LOOKUP: Questions about existing code, explanations, analysis
_LOOKUP_PATTERNS: List[re.Pattern] = [
    # "What does X do", "Explain Y", "Show me Z"
    re.compile(
        r"(was\s+macht|erkl[aä]r[e]?\s+(mir\s+)?(die|den|das|diese|diesen)?|"
        r"zeig\s+(mir\s+)?|beschreib[e]?\s+|wie\s+funktioniert|"
        r"what\s+does|explain|show\s+me|describe|how\s+does)\s+",
        re.IGNORECASE,
    ),
    # Questions with "?" that reference code artifacts
    re.compile(
        r"(class|klasse|methode|method|funktion|function|datei|file|"
        r"modul|module|service|controller|api|endpoint|"
        r"tabelle|table|schema|interface|komponente|component).*\?",
        re.IGNORECASE,
    ),
    # "Analysiere" without change intent
    re.compile(
        r"^(analysiere|analyze|review[e]?|pr[uü]fe|check)\s+",
        re.IGNORECASE,
    ),
]

# COMPLEX: Multi-step, external references, multi-system
_COMPLEX_PATTERNS: List[re.Pattern] = [
    # Multi-task indicators
    re.compile(
        r"(und\s+dann|au[sß]erdem|zus[aä]tzlich|danach|als\s+n[aä]chstes|"
        r"erstens.*zweitens|1\.\s+.*2\.\s+|"
        r"and\s+then|additionally|furthermore|after\s+that|"
        r"first.*second|step\s+1.*step\s+2)",
        re.IGNORECASE | re.DOTALL,
    ),
    # External source references
    re.compile(
        r"(laut\s+(wiki|confluence|handbuch|doku)|"
        r"wie\s+(im|in\s+der)\s+(wiki|confluence|handbuch|doku)|"
        r"gem[aä][sß]\s+(der\s+)?spezifikation|"
        r"according\s+to\s+(the\s+)?(wiki|docs|handbook)|"
        r"as\s+(described|specified)\s+in)",
        re.IGNORECASE,
    ),
    # Multi-system references
    re.compile(
        r"(frontend\s+.{0,30}backend|backend\s+.{0,30}frontend|"
        r"api\s+.{0,30}(datenbank|database|db)|"
        r"(datenbank|database|db)\s+.{0,30}api|"
        r"client\s+.{0,30}server|server\s+.{0,30}client)",
        re.IGNORECASE,
    ),
    # Explicit research/investigation
    re.compile(
        r"^(recherchiere|recherche|sammle\s+wissen|"
        r"untersuche|investigat[e]?|research)\s+",
        re.IGNORECASE,
    ),
    # Debug with stacktrace/error details
    re.compile(
        r"(stacktrace|traceback|exception\s+at\s+line|"
        r"error:\s+|fehler:\s+|caused\s+by:)",
        re.IGNORECASE,
    ),
]

# GUIDED signals: Single change, clear task
_GUIDED_PATTERNS: List[re.Pattern] = [
    # Imperative change requests
    re.compile(
        r"^(fix[e]?\s+|behebe\s+|[aä]nder[e]?\s+|f[uü]ge?\s+.{1,40}\s+hinzu|"
        r"erstell[e]?\s+|implementier[e]?\s+|schreib[e]?\s+|"
        r"entfern[e]?\s+|l[oö]sch[e]?\s+|"
        r"add\s+|create\s+|implement\s+|write\s+|remove\s+|delete\s+|"
        r"update\s+|change\s+|modify\s+|refactor\s+)",
        re.IGNORECASE,
    ),
]


# ══════════════════════════════════════════════════════════════════════════════
# Complexity scoring for borderline cases
# ══════════════════════════════════════════════════════════════════════════════

_MULTI_MARKERS = [
    "und dann", "außerdem", "zusätzlich", "danach", "als nächstes",
    "erstens", "zweitens", "drittens",
    "and then", "additionally", "furthermore", "after that",
    "1.", "2.", "3.",
]

# Pattern: 3+ distinct nouns connected by "und"/"and" → multi-task
_RE_MULTI_AND = re.compile(
    r"\b\w+\s+und\s+\w+\s+und\s+\w+",
    re.IGNORECASE,
)

_SYSTEM_MARKERS = [
    "frontend", "backend", "datenbank", "database", "api",
    "deployment", "ci/cd", "docker", "kubernetes",
]


def _compute_complexity_score(message: str) -> float:
    """
    Compute a complexity score for borderline GUIDED vs COMPLEX decisions.

    Returns:
        Score between 0.0 and 1.0. Above 0.4 suggests COMPLEX.
    """
    msg = message.lower()
    score = 0.0

    # Length contribution (normalized, max 0.25)
    score += min(len(msg) / 600, 0.25)

    # Multi-task markers
    for marker in _MULTI_MARKERS:
        if marker in msg:
            score += 0.15

    # Multi-system breadth
    systems_mentioned = sum(1 for m in _SYSTEM_MARKERS if m in msg)
    if systems_mentioned >= 2:
        score += 0.2

    # Sentence count as proxy for complexity
    sentences = len(re.split(r'[.!?]\s+', msg))
    if sentences >= 4:
        score += 0.1

    # "X und Y und Z" pattern — 3+ items connected by "und"/"and"
    if _RE_MULTI_AND.search(msg):
        score += 0.2

    return min(score, 1.0)


# ══════════════════════════════════════════════════════════════════════════════
# Main classifier
# ══════════════════════════════════════════════════════════════════════════════

def classify_intent(message: str) -> IntentResult:
    """
    Classify user message intent to determine pipeline configuration.

    This is a fast, local classification (no LLM call, ~0ms).
    The intent determines which pipeline stages are activated.

    Args:
        message: The user's message

    Returns:
        IntentResult with intent, confidence, and pipeline config
    """
    msg = message.strip()

    # Very short messages → DIRECT (confirmations, greetings)
    if len(msg) < 15:
        return IntentResult(
            intent=Intent.DIRECT,
            confidence=0.9,
            reasoning=f"Very short message ({len(msg)} chars)",
            pipeline=PipelineConfig.for_intent(Intent.DIRECT),
        )

    # Check COMPLEX patterns first (highest specificity)
    for pattern in _COMPLEX_PATTERNS:
        if pattern.search(msg):
            return IntentResult(
                intent=Intent.COMPLEX,
                confidence=0.85,
                reasoning=f"Matched complex pattern: {pattern.pattern[:60]}",
                pipeline=PipelineConfig.for_intent(Intent.COMPLEX),
            )

    # Check DIRECT patterns
    for pattern in _DIRECT_PATTERNS:
        if pattern.search(msg):
            return IntentResult(
                intent=Intent.DIRECT,
                confidence=0.8,
                reasoning=f"Matched direct pattern: {pattern.pattern[:60]}",
                pipeline=PipelineConfig.for_intent(Intent.DIRECT),
            )

    # Check LOOKUP patterns
    for pattern in _LOOKUP_PATTERNS:
        if pattern.search(msg):
            # But if it also has GUIDED signals, upgrade to GUIDED
            for guided_pattern in _GUIDED_PATTERNS:
                if guided_pattern.search(msg):
                    return IntentResult(
                        intent=Intent.GUIDED,
                        confidence=0.7,
                        reasoning="Matched lookup + guided patterns → GUIDED",
                        pipeline=PipelineConfig.for_intent(Intent.GUIDED),
                    )
            return IntentResult(
                intent=Intent.LOOKUP,
                confidence=0.75,
                reasoning=f"Matched lookup pattern: {pattern.pattern[:60]}",
                pipeline=PipelineConfig.for_intent(Intent.LOOKUP),
            )

    # Check GUIDED patterns
    for pattern in _GUIDED_PATTERNS:
        if pattern.search(msg):
            # Could be COMPLEX if complexity score is high
            complexity = _compute_complexity_score(msg)
            if complexity >= 0.4:
                return IntentResult(
                    intent=Intent.COMPLEX,
                    confidence=0.65,
                    reasoning=f"Guided pattern but high complexity ({complexity:.2f})",
                    pipeline=PipelineConfig.for_intent(Intent.COMPLEX),
                )
            return IntentResult(
                intent=Intent.GUIDED,
                confidence=0.75,
                reasoning=f"Matched guided pattern: {pattern.pattern[:60]}",
                pipeline=PipelineConfig.for_intent(Intent.GUIDED),
            )

    # Fallback: Use complexity score for ambiguous messages
    complexity = _compute_complexity_score(msg)
    if complexity >= 0.4:
        return IntentResult(
            intent=Intent.COMPLEX,
            confidence=0.5,
            reasoning=f"No pattern match, complexity score {complexity:.2f} → COMPLEX",
            pipeline=PipelineConfig.for_intent(Intent.COMPLEX),
        )

    # Default: Questions (ends with ?) → LOOKUP, otherwise → GUIDED
    if msg.rstrip().endswith("?"):
        return IntentResult(
            intent=Intent.LOOKUP,
            confidence=0.5,
            reasoning="No pattern match, ends with '?' → LOOKUP",
            pipeline=PipelineConfig.for_intent(Intent.LOOKUP),
        )

    return IntentResult(
        intent=Intent.GUIDED,
        confidence=0.5,
        reasoning="No pattern match, default → GUIDED",
        pipeline=PipelineConfig.for_intent(Intent.GUIDED),
    )
