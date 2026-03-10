"""
MCP Capabilities Module.

Provides local implementation of structured capabilities inspired by SuperClaude:
- Brainstorm: Interactive requirements discovery
- Design: System and component design
- Implement: Code generation
- Analyze: Code analysis and quality assessment
"""

from app.mcp.capabilities.base import (
    BaseCapability,
    CapabilitySession,
    CapabilityPhase,
    CapabilityStatus,
    CapabilityStep,
    CapabilityArtifact
)
from app.mcp.capabilities.brainstorm import BrainstormCapability
from app.mcp.capabilities.design import DesignCapability
from app.mcp.capabilities.implement import ImplementCapability
from app.mcp.capabilities.analyze import AnalyzeCapability

__all__ = [
    # Base
    "BaseCapability",
    "CapabilitySession",
    "CapabilityPhase",
    "CapabilityStatus",
    "CapabilityStep",
    "CapabilityArtifact",
    # Capabilities
    "BrainstormCapability",
    "DesignCapability",
    "ImplementCapability",
    "AnalyzeCapability",
]
