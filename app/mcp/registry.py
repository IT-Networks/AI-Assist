"""
Capability Registry - Central management for all MCP capabilities.

Handles:
- Capability registration and discovery
- Session management across capabilities
- Handoff routing between capabilities
- Tool definition aggregation
"""

import logging
from typing import Any, Callable, Dict, List, Optional, Type

from app.mcp.capabilities.base import (
    BaseCapability,
    CapabilitySession,
    CapabilityStatus
)

logger = logging.getLogger(__name__)


class CapabilityRegistry:
    """
    Central registry for all MCP capabilities.

    Manages capability lifecycle, routing, and cross-capability communication.
    """

    def __init__(self, llm_callback: Optional[Callable] = None):
        """
        Args:
            llm_callback: Callback for LLM calls, passed to capabilities
        """
        self.llm_callback = llm_callback
        self._capabilities: Dict[str, BaseCapability] = {}
        self._sessions: Dict[str, CapabilitySession] = {}
        self._handoff_chain: List[str] = []  # Track handoff history

    def register(self, capability: BaseCapability) -> None:
        """
        Register a capability.

        Args:
            capability: The capability instance to register
        """
        if capability.name in self._capabilities:
            logger.warning(f"[Registry] Overwriting capability: {capability.name}")

        self._capabilities[capability.name] = capability
        logger.info(f"[Registry] Registered capability: {capability.name}")

    def register_class(self, capability_class: Type[BaseCapability]) -> None:
        """
        Register a capability by class (instantiates with llm_callback).

        Args:
            capability_class: The capability class to instantiate and register
        """
        capability = capability_class(llm_callback=self.llm_callback)
        self.register(capability)

    def get(self, name: str) -> Optional[BaseCapability]:
        """Get a capability by name."""
        return self._capabilities.get(name)

    def list_capabilities(self) -> List[str]:
        """List all registered capability names."""
        return list(self._capabilities.keys())

    def get_all_tool_definitions(self) -> List[Dict[str, Any]]:
        """Get tool definitions for all registered capabilities."""
        definitions = []
        for capability in self._capabilities.values():
            definitions.append(capability.get_tool_definition())
        return definitions

    async def execute(
        self,
        capability_name: str,
        query: str,
        context: Optional[str] = None,
        **kwargs
    ) -> CapabilitySession:
        """
        Execute a capability by name.

        Args:
            capability_name: Name of the capability to execute
            query: The query/request
            context: Optional context
            **kwargs: Additional arguments for the capability

        Returns:
            The completed CapabilitySession

        Raises:
            ValueError: If capability not found
        """
        capability = self.get(capability_name)
        if capability is None:
            raise ValueError(f"Unknown capability: {capability_name}")

        session = await capability.execute(query, context, **kwargs)
        self._sessions[session.session_id] = session

        return session

    async def handoff(
        self,
        source_session_id: str,
        target_capability: str,
        additional_context: Optional[str] = None
    ) -> CapabilitySession:
        """
        Hand off from one capability session to another.

        Args:
            source_session_id: ID of the source session
            target_capability: Name of the target capability
            additional_context: Additional context for the target

        Returns:
            The new session from the target capability

        Raises:
            ValueError: If source session or target capability not found
        """
        # Find source session
        source_session = self._sessions.get(source_session_id)
        if source_session is None:
            # Try to find in capability-specific sessions
            for cap in self._capabilities.values():
                source_session = cap.get_session(source_session_id)
                if source_session:
                    break

        if source_session is None:
            raise ValueError(f"Source session not found: {source_session_id}")

        # Get source capability
        source_capability = self.get(source_session.capability_name)
        if source_capability is None:
            raise ValueError(f"Source capability not found: {source_session.capability_name}")

        # Get target capability
        target = self.get(target_capability)
        if target is None:
            raise ValueError(f"Target capability not found: {target_capability}")

        # Prepare handoff data
        handoff_data = source_capability.prepare_handoff(source_session, target_capability)

        # Build context for target
        context_parts = [handoff_data["summary"]]
        if additional_context:
            context_parts.append(f"\n\n## Additional Context\n{additional_context}")

        combined_context = "\n".join(context_parts)

        # Track handoff
        self._handoff_chain.append(f"{source_session.capability_name} -> {target_capability}")

        # Execute target capability
        new_session = await target.execute(
            query=handoff_data["query"],
            context=combined_context,
            parent_session_id=source_session_id,
            handoff_artifacts=handoff_data["artifacts"]
        )

        # Link sessions
        source_session.child_session_ids.append(new_session.session_id)
        self._sessions[new_session.session_id] = new_session

        logger.info(
            f"[Registry] Handoff: {source_session.capability_name} "
            f"({source_session_id}) -> {target_capability} ({new_session.session_id})"
        )

        return new_session

    def get_session(self, session_id: str) -> Optional[CapabilitySession]:
        """Get a session by ID from any capability."""
        if session_id in self._sessions:
            return self._sessions[session_id]

        # Search in capability-specific sessions
        for cap in self._capabilities.values():
            session = cap.get_session(session_id)
            if session:
                return session

        return None

    def get_active_sessions(self) -> List[CapabilitySession]:
        """Get all active (running or paused) sessions."""
        active = []
        for session in self._sessions.values():
            if session.status in (CapabilityStatus.RUNNING, CapabilityStatus.PAUSED):
                active.append(session)
        return active

    def get_handoff_chain(self) -> List[str]:
        """Get the history of handoffs in this registry."""
        return self._handoff_chain.copy()

    def get_session_lineage(self, session_id: str) -> List[CapabilitySession]:
        """
        Get the full lineage of a session (parent chain).

        Returns sessions from oldest ancestor to the given session.
        """
        lineage = []
        current = self.get_session(session_id)

        while current:
            lineage.insert(0, current)
            if current.parent_session_id:
                current = self.get_session(current.parent_session_id)
            else:
                break

        return lineage

    def suggest_next_capability(
        self,
        session_id: str
    ) -> Optional[str]:
        """
        Suggest the next capability based on session state.

        Args:
            session_id: Current session ID

        Returns:
            Suggested capability name or None
        """
        session = self.get_session(session_id)
        if not session:
            return None

        capability = self.get(session.capability_name)
        if not capability:
            return None

        # Check if capability has handoff targets
        if capability.handoff_targets:
            # Return first available target
            for target in capability.handoff_targets:
                if target in self._capabilities:
                    return target

        return None


# Singleton instance
_registry: Optional[CapabilityRegistry] = None


def get_capability_registry(llm_callback: Optional[Callable] = None) -> CapabilityRegistry:
    """Get the singleton CapabilityRegistry instance."""
    global _registry
    if _registry is None:
        _registry = CapabilityRegistry(llm_callback)
    elif llm_callback and _registry.llm_callback is None:
        _registry.llm_callback = llm_callback
    return _registry


def register_default_capabilities(registry: CapabilityRegistry) -> None:
    """Register the default set of capabilities."""
    from app.mcp.capabilities.brainstorm import BrainstormCapability
    from app.mcp.capabilities.design import DesignCapability
    from app.mcp.capabilities.implement import ImplementCapability
    from app.mcp.capabilities.analyze import AnalyzeCapability
    from app.mcp.capabilities.research import ResearchCapability

    registry.register_class(BrainstormCapability)
    registry.register_class(DesignCapability)
    registry.register_class(ImplementCapability)
    registry.register_class(AnalyzeCapability)
    registry.register_class(ResearchCapability)

    logger.info("[Registry] Default capabilities registered")
