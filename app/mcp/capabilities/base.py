"""
Base classes for MCP Capabilities.

Provides the foundation for Brainstorm, Design, Implement, and Analyze capabilities.
"""

import uuid
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Callable

logger = logging.getLogger(__name__)


class CapabilityPhase(Enum):
    """Standard phases for all capabilities."""
    INIT = "init"
    EXPLORE = "explore"
    ANALYZE = "analyze"
    SYNTHESIZE = "synthesize"
    VALIDATE = "validate"
    OUTPUT = "output"
    COMPLETE = "complete"


class CapabilityStatus(Enum):
    """Status of a capability session."""
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    HANDED_OFF = "handed_off"


@dataclass
class CapabilityArtifact:
    """An artifact produced by a capability."""
    artifact_id: str
    artifact_type: str  # "requirements", "design", "code", "analysis"
    title: str
    content: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "artifact_type": self.artifact_type,
            "title": self.title,
            "content": self.content,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat()
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CapabilityArtifact":
        return cls(
            artifact_id=data["artifact_id"],
            artifact_type=data["artifact_type"],
            title=data["title"],
            content=data["content"],
            metadata=data.get("metadata", {}),
            created_at=datetime.fromisoformat(data["created_at"])
        )


@dataclass
class CapabilityStep:
    """A single step within a capability execution."""
    step_id: str
    phase: CapabilityPhase
    title: str
    content: str
    insights: List[str] = field(default_factory=list)
    questions: List[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_id": self.step_id,
            "phase": self.phase.value,
            "title": self.title,
            "content": self.content,
            "insights": self.insights,
            "questions": self.questions,
            "created_at": self.created_at.isoformat()
        }


@dataclass
class CapabilitySession:
    """
    A session for a capability execution.

    Tracks the entire lifecycle of a capability from start to completion or handoff.
    """
    session_id: str
    capability_name: str
    query: str
    context: Optional[str] = None
    status: CapabilityStatus = CapabilityStatus.PENDING
    current_phase: CapabilityPhase = CapabilityPhase.INIT
    steps: List[CapabilityStep] = field(default_factory=list)
    artifacts: List[CapabilityArtifact] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    parent_session_id: Optional[str] = None  # For handoff tracking
    child_session_ids: List[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    def add_step(
        self,
        phase: CapabilityPhase,
        title: str,
        content: str,
        insights: Optional[List[str]] = None,
        questions: Optional[List[str]] = None
    ) -> CapabilityStep:
        """Add a step to the session."""
        step = CapabilityStep(
            step_id=str(uuid.uuid4())[:8],
            phase=phase,
            title=title,
            content=content,
            insights=insights or [],
            questions=questions or []
        )
        self.steps.append(step)
        self.current_phase = phase
        self.updated_at = datetime.now()
        return step

    def add_artifact(
        self,
        artifact_type: str,
        title: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> CapabilityArtifact:
        """Add an artifact to the session."""
        artifact = CapabilityArtifact(
            artifact_id=str(uuid.uuid4())[:8],
            artifact_type=artifact_type,
            title=title,
            content=content,
            metadata=metadata or {}
        )
        self.artifacts.append(artifact)
        self.updated_at = datetime.now()
        return artifact

    def get_artifacts_by_type(self, artifact_type: str) -> List[CapabilityArtifact]:
        """Get all artifacts of a specific type."""
        return [a for a in self.artifacts if a.artifact_type == artifact_type]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "capability_name": self.capability_name,
            "query": self.query,
            "context": self.context,
            "status": self.status.value,
            "current_phase": self.current_phase.value,
            "steps": [s.to_dict() for s in self.steps],
            "artifacts": [a.to_dict() for a in self.artifacts],
            "metadata": self.metadata,
            "parent_session_id": self.parent_session_id,
            "child_session_ids": self.child_session_ids,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat()
        }

    def format_for_context(self) -> str:
        """Format session for inclusion in LLM context."""
        lines = [
            f"## {self.capability_name.title()} Session",
            f"**Query:** {self.query}",
            f"**Status:** {self.status.value}",
            f"**Phase:** {self.current_phase.value}",
            ""
        ]

        if self.steps:
            lines.append("### Steps")
            for i, step in enumerate(self.steps, 1):
                lines.append(f"\n#### {i}. {step.title} ({step.phase.value})")
                lines.append(step.content)
                if step.insights:
                    lines.append("\n**Insights:**")
                    for insight in step.insights:
                        lines.append(f"- {insight}")
                if step.questions:
                    lines.append("\n**Open Questions:**")
                    for q in step.questions:
                        lines.append(f"- {q}")

        if self.artifacts:
            lines.append("\n### Artifacts")
            for artifact in self.artifacts:
                lines.append(f"\n#### {artifact.title} ({artifact.artifact_type})")
                lines.append(artifact.content[:500] + "..." if len(artifact.content) > 500 else artifact.content)

        return "\n".join(lines)


class BaseCapability(ABC):
    """
    Abstract base class for all capabilities.

    Capabilities follow a standard lifecycle:
    1. INIT: Initialize session, parse query
    2. EXPLORE: Gather information, ask questions
    3. ANALYZE: Process gathered information
    4. SYNTHESIZE: Create outputs
    5. VALIDATE: Verify outputs
    6. OUTPUT: Generate final artifacts
    7. COMPLETE: Finalize or handoff
    """

    def __init__(self, llm_callback: Optional[Callable] = None):
        """
        Args:
            llm_callback: Async function for LLM calls.
                          Signature: async def callback(prompt: str, context: str = None) -> str
        """
        self.llm_callback = llm_callback
        self._sessions: Dict[str, CapabilitySession] = {}

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique name of the capability."""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """Description of what this capability does."""
        pass

    @property
    def handoff_targets(self) -> List[str]:
        """List of capabilities this can hand off to."""
        return []

    def get_tool_definition(self) -> Dict[str, Any]:
        """Get the tool definition for this capability."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self._get_parameters_schema()
            }
        }

    @abstractmethod
    def _get_parameters_schema(self) -> Dict[str, Any]:
        """Get the JSON schema for capability parameters."""
        pass

    async def execute(
        self,
        query: str,
        context: Optional[str] = None,
        parent_session_id: Optional[str] = None,
        **kwargs
    ) -> CapabilitySession:
        """
        Execute the capability.

        Args:
            query: The main query/request
            context: Optional context (e.g., from parent session)
            parent_session_id: ID of parent session (for handoffs)
            **kwargs: Additional capability-specific arguments

        Returns:
            The completed CapabilitySession
        """
        # Create session
        session = CapabilitySession(
            session_id=str(uuid.uuid4())[:12],
            capability_name=self.name,
            query=query,
            context=context,
            parent_session_id=parent_session_id,
            metadata=kwargs
        )
        self._sessions[session.session_id] = session
        session.status = CapabilityStatus.RUNNING

        logger.info(f"[{self.name}] Starting session {session.session_id}")

        try:
            # Execute phases
            await self._phase_init(session)
            await self._phase_explore(session)
            await self._phase_analyze(session)
            await self._phase_synthesize(session)
            await self._phase_validate(session)
            await self._phase_output(session)

            session.status = CapabilityStatus.COMPLETED
            session.current_phase = CapabilityPhase.COMPLETE

        except Exception as e:
            logger.error(f"[{self.name}] Session {session.session_id} failed: {e}")
            session.status = CapabilityStatus.FAILED
            session.metadata["error"] = str(e)
            raise

        return session

    async def _call_llm(self, prompt: str, context: Optional[str] = None) -> str:
        """Call the LLM with a prompt."""
        if self.llm_callback is None:
            logger.warning(f"[{self.name}] No LLM callback configured")
            return ""

        return await self.llm_callback(prompt, context)

    # Phase methods - override in subclasses
    async def _phase_init(self, session: CapabilitySession) -> None:
        """Initialize the capability execution."""
        session.add_step(
            phase=CapabilityPhase.INIT,
            title="Initialization",
            content=f"Starting {self.name} for: {session.query}"
        )

    @abstractmethod
    async def _phase_explore(self, session: CapabilitySession) -> None:
        """Gather information and explore the problem space."""
        pass

    @abstractmethod
    async def _phase_analyze(self, session: CapabilitySession) -> None:
        """Analyze gathered information."""
        pass

    @abstractmethod
    async def _phase_synthesize(self, session: CapabilitySession) -> None:
        """Create outputs from analysis."""
        pass

    async def _phase_validate(self, session: CapabilitySession) -> None:
        """Validate outputs. Override for custom validation."""
        session.add_step(
            phase=CapabilityPhase.VALIDATE,
            title="Validation",
            content="Output validation completed."
        )

    async def _phase_output(self, session: CapabilitySession) -> None:
        """Generate final artifacts. Override for custom output."""
        pass

    def get_session(self, session_id: str) -> Optional[CapabilitySession]:
        """Get a session by ID."""
        return self._sessions.get(session_id)

    def prepare_handoff(
        self,
        session: CapabilitySession,
        target_capability: str
    ) -> Dict[str, Any]:
        """
        Prepare data for handoff to another capability.

        Args:
            session: The current session
            target_capability: Name of the target capability

        Returns:
            Handoff data dict with context for the target capability
        """
        if target_capability not in self.handoff_targets:
            logger.warning(
                f"[{self.name}] Unexpected handoff target: {target_capability}"
            )

        session.status = CapabilityStatus.HANDED_OFF
        session.metadata["handed_off_to"] = target_capability

        return {
            "parent_session_id": session.session_id,
            "capability_name": self.name,
            "query": session.query,
            "artifacts": [a.to_dict() for a in session.artifacts],
            "summary": session.format_for_context(),
            "metadata": session.metadata
        }
