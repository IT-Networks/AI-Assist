"""
MCP Capabilities Module.

Provides local implementation of structured capabilities:
- Analyze: Code analysis and quality assessment
- Research: Deep research with parallel sources

NOTE: brainstorm, design, implement wurden zu Skills migriert.
Siehe ~/.claude/commands/sc/brainstorm.md, design.md, implement.md
"""

from app.mcp.capabilities.base import (
    BaseCapability,
    CapabilitySession,
    CapabilityPhase,
    CapabilityStatus,
    CapabilityStep,
    CapabilityArtifact
)
from app.mcp.capabilities.analyze import AnalyzeCapability
from app.mcp.capabilities.research import ResearchCapability, get_research_capability

__all__ = [
    # Base
    "BaseCapability",
    "CapabilitySession",
    "CapabilityPhase",
    "CapabilityStatus",
    "CapabilityStep",
    "CapabilityArtifact",
    # Capabilities (analyze, research - brainstorm/design/implement sind Skills)
    "AnalyzeCapability",
    "ResearchCapability",
    "get_research_capability",
]
