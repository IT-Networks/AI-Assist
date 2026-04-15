"""
Approval Manager – Verwaltet 3-stufigen Approval Flow.

Stages:
1. PLAN - Zeige Aufgabenplan, frage "Start Implementation?"
2. EXECUTE - Agenten führen aus (keine Unterbrechungen)
3. VERIFY - Zeige Ergebnisse + Diff, frage "Merge?" oder "Changes?"
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class ApprovalStage(Enum):
    """Approval Workflow Stages."""
    PLANNING = "planning"        # Coordinator hat Plan erstellt
    PLAN_READY = "plan_ready"    # Plan zeigt dem User → warte auf Genehmigung
    EXECUTING = "executing"      # Agenten führen aus
    EXECUTION_DONE = "execution_done"  # Agenten fertig, Tests gelaufen
    REVIEWING = "reviewing"      # Zeige Ergebnisse → warte auf Genehmigung
    APPROVED = "approved"        # User genehmigt → merge
    REJECTED = "rejected"        # User lehnt ab → rollback
    ROLLED_BACK = "rolled_back"  # Changes wurden rückgängig gemacht


class ApprovalDecision(Enum):
    """Benutzer-Entscheidung."""
    APPROVE = "approve"          # Merge zur git
    CHANGES_REQUESTED = "changes_requested"  # Änderungen nötig
    DISCARD = "discard"          # Alles verwerfen


@dataclass
class ApprovalRequest:
    """Request für User-Genehmigung."""
    feature_id: str
    stage: ApprovalStage
    title: str
    description: str
    plan_summary: Optional[Dict] = None  # Für PLAN_READY
    execution_summary: Optional[Dict] = None  # Für REVIEWING
    changes: List[Dict] = None  # Liste der Datei-Änderungen
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.utcnow().isoformat()
        if self.changes is None:
            self.changes = []


@dataclass
class ApprovalResponse:
    """Antwort von User auf Genehmigungsanfrage."""
    feature_id: str
    stage: ApprovalStage
    decision: ApprovalDecision
    feedback: str = ""
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.utcnow().isoformat()


class ApprovalManager:
    """Verwaltet 3-stufigen Approval Flow."""

    def __init__(
        self,
        on_approval_request: Optional[Callable[[ApprovalRequest], Awaitable[None]]] = None,
    ):
        """
        Initialize approval manager.

        Args:
            on_approval_request: Callback wenn User-Genehmigung nötig ist
        """
        self._on_approval_request = on_approval_request
        self._pending_request: Optional[ApprovalRequest] = None
        self._response_event: asyncio.Event = asyncio.Event()
        self._last_response: Optional[ApprovalResponse] = None

    async def request_plan_approval(
        self,
        feature_id: str,
        title: str,
        plan: Dict
    ) -> ApprovalDecision:
        """
        Stage 1: Zeige Plan und frage ob Start.

        Args:
            feature_id: Feature-ID
            title: Feature-Titel
            plan: Task-Plan mit Agents, Files, Estimate

        Returns:
            User's decision (APPROVE or DISCARD)
        """
        description = (
            f"Die folgende Feature wird implementiert:\n\n"
            f"**Beteiligte Agenten:** {', '.join(plan.get('agents', []))}\n"
            f"**Zu erstellende/ändernde Dateien:** {plan.get('file_count', 0)}\n"
            f"**Geschätzte Dauer:** {plan.get('estimated_duration_minutes', 5)}-{plan.get('estimated_duration_minutes', 5) + 5} Minuten\n\n"
            f"Möchten Sie die Implementierung starten?"
        )

        request = ApprovalRequest(
            feature_id=feature_id,
            stage=ApprovalStage.PLAN_READY,
            title=title,
            description=description,
            plan_summary=plan,
            changes=plan.get('files_affected', [])
        )

        return await self._wait_for_approval(request)

    async def request_verification_approval(
        self,
        feature_id: str,
        title: str,
        test_results: Dict,
        files_changed: List[str],
        diff_preview: str = ""
    ) -> ApprovalDecision:
        """
        Stage 3: Zeige Ergebnisse und frage ob Merge.

        Args:
            feature_id: Feature-ID
            title: Feature-Titel
            test_results: Testergebnisse (pytest, npm test, coverage)
            files_changed: Liste der geänderten Dateien
            diff_preview: Git diff preview

        Returns:
            User's decision (APPROVE, CHANGES_REQUESTED, DISCARD)
        """
        # Build description
        backend_tests = test_results.get('backend_tests', {})
        frontend_tests = test_results.get('frontend_tests', {})
        coverage = test_results.get('coverage', {})

        backend_status = "✅ bestanden" if backend_tests.get('passed', 0) > 0 and backend_tests.get('failed', 0) == 0 else "❌ fehler"
        frontend_status = "✅ bestanden" if frontend_tests.get('passed', 0) > 0 and frontend_tests.get('failed', 0) == 0 else "❌ fehler"

        description = (
            f"**Implementierung abgeschlossen!**\n\n"
            f"Backend-Tests: {backend_status} ({backend_tests.get('passed', 0)} bestanden, {backend_tests.get('failed', 0)} fehler)\n"
            f"Frontend-Tests: {frontend_status} ({frontend_tests.get('passed', 0)} bestanden, {frontend_tests.get('failed', 0)} fehler)\n"
            f"Code-Abdeckung: {coverage.get('percentage', 0)}%\n\n"
            f"**Geänderte Dateien:**\n"
        )

        for file in files_changed:
            description += f"- `{file}`\n"

        if diff_preview:
            description += f"\n**Diff Preview:**\n```\n{diff_preview[:500]}...\n```"

        request = ApprovalRequest(
            feature_id=feature_id,
            stage=ApprovalStage.REVIEWING,
            title=f"{title} - Überprüfung",
            description=description,
            execution_summary=test_results,
            changes=[{"file": f, "type": "UNKNOWN"} for f in files_changed]
        )

        return await self._wait_for_approval(request)

    async def submit_response(self, response: ApprovalResponse):
        """
        User submittet Antwort auf Genehmigungsanfrage.

        Args:
            response: ApprovalResponse mit decision
        """
        if self._pending_request is None:
            logger.warning(f"[ApprovalManager] Received response but no pending request: {response.feature_id}")
            return

        logger.info(f"[ApprovalManager] User decision: {response.decision.value} for {response.feature_id}")
        self._last_response = response
        self._response_event.set()

    async def _wait_for_approval(self, request: ApprovalRequest) -> ApprovalDecision:
        """
        Warte auf User-Antwort auf Genehmigungsanfrage.

        Args:
            request: ApprovalRequest

        Returns:
            User's decision
        """
        self._pending_request = request
        self._last_response = None
        self._response_event.clear()

        # Emit approval request to frontend
        if self._on_approval_request:
            try:
                await self._on_approval_request(request)
            except Exception as e:
                logger.error(f"[ApprovalManager] Error emitting approval request: {e}")
                return ApprovalDecision.DISCARD

        # Wait for response (with 30 minute timeout)
        try:
            await asyncio.wait_for(self._response_event.wait(), timeout=1800)
        except asyncio.TimeoutError:
            logger.error(f"[ApprovalManager] Approval request timed out for {request.feature_id}")
            return ApprovalDecision.DISCARD

        if not self._last_response:
            return ApprovalDecision.DISCARD

        # Convert response decision to return type
        if self._last_response.decision == ApprovalDecision.APPROVE:
            return ApprovalDecision.APPROVE
        elif self._last_response.decision == ApprovalDecision.CHANGES_REQUESTED:
            return ApprovalDecision.CHANGES_REQUESTED
        else:
            return ApprovalDecision.DISCARD

    def clear_pending(self):
        """Clear pending approval request."""
        self._pending_request = None
        self._last_response = None
        self._response_event.clear()

    def get_pending_request(self) -> Optional[ApprovalRequest]:
        """Get current pending approval request."""
        return self._pending_request

    def get_last_response(self) -> Optional[ApprovalResponse]:
        """Get last user response."""
        return self._last_response


# ══════════════════════════════════════════════════════════════════════════════
# Globale Registry - ermöglicht HTTP-Endpoint Zugriff auf aktive Manager
# ══════════════════════════════════════════════════════════════════════════════

_pending_managers: Dict[str, "ApprovalManager"] = {}


def register_approval_manager(feature_id: str, manager: "ApprovalManager") -> None:
    """Registriert einen aktiven ApprovalManager per feature_id."""
    _pending_managers[feature_id] = manager
    logger.debug(f"[ApprovalManager] Registered for feature {feature_id} (total: {len(_pending_managers)})")


def get_approval_manager(feature_id: str) -> Optional["ApprovalManager"]:
    """Lookup des ApprovalManager per feature_id (für HTTP-Endpoint)."""
    return _pending_managers.get(feature_id)


def unregister_approval_manager(feature_id: str) -> None:
    """Räumt Manager nach Ende des Runs auf."""
    if feature_id in _pending_managers:
        del _pending_managers[feature_id]
        logger.debug(f"[ApprovalManager] Unregistered {feature_id} (remaining: {len(_pending_managers)})")


def list_pending_feature_ids() -> List[str]:
    """Debug: Liste aller aktuell wartenden feature_ids."""
    return list(_pending_managers.keys())
