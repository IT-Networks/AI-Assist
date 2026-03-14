"""
Analytics Logger - Loggt anonymisierte Tool-Nutzung und KI-Entscheidungen.

Zweck:
- Tool-Auswahl-Entscheidungen tracken
- Tool-Ausführungs-Erfolg/Fehler loggen
- User-Feedback inferieren (Wiederholungen, Themenwechsel)
- Daten für Claude-Analyse zur Programmverbesserung bereitstellen

Alle Daten werden vor dem Speichern anonymisiert.
"""

import asyncio
import gzip
import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import aiofiles
    HAS_AIOFILES = True
except ImportError:
    HAS_AIOFILES = False

from app.core.config import settings
from app.services.anonymizer import Anonymizer, AnonymizerConfig, get_anonymizer

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# Datenstrukturen
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class ToolDecision:
    """Warum wurde ein Tool gewählt?"""
    step: int
    tool: str
    reason: str
    alternatives: List[str] = field(default_factory=list)
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.utcnow().isoformat()


@dataclass
class ToolExecution:
    """Ergebnis einer Tool-Ausführung."""
    step: int
    tool: str
    status: str  # success | error | timeout | partial
    duration_ms: int
    error_type: Optional[str] = None  # connection | validation | permission | none
    result_tokens: int = 0
    reason: str = ""
    alternatives: List[str] = field(default_factory=list)


@dataclass
class UserFeedback:
    """Inferiertes oder explizites User-Feedback."""
    type: str  # inferred_success | inferred_retry | inferred_clarification | explicit_negative | explicit_positive
    signal: str  # topic_change | same_question | why_question | negative_words | positive_words
    details: Optional[str] = None


@dataclass
class AnalyticsChain:
    """Eine vollständige Anfrage-Kette."""
    chain_id: str
    timestamp: str
    query_hash: str
    query_categories: List[str]
    model: str
    model_settings: Dict[str, Any]
    tool_chain: List[ToolExecution] = field(default_factory=list)
    total_iterations: int = 0
    final_status: str = "pending"  # pending | resolved | failed | timeout | user_abort
    user_feedback: Optional[UserFeedback] = None
    duration_ms: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Konvertiert zu Dictionary für JSON-Export."""
        result = {
            "chain_id": self.chain_id,
            "ts": self.timestamp,
            "query_hash": self.query_hash,
            "query_categories": self.query_categories,
            "model": self.model,
            "settings": self.model_settings,
            "tool_chain": [asdict(t) for t in self.tool_chain],
            "total_iterations": self.total_iterations,
            "final_status": self.final_status,
            "duration_ms": self.duration_ms,
        }
        if self.user_feedback:
            result["user_feedback"] = asdict(self.user_feedback)
        return result


# ═══════════════════════════════════════════════════════════════════════════════
# Feedback Inference
# ═══════════════════════════════════════════════════════════════════════════════

class FeedbackInferrer:
    """Inferiert User-Feedback aus Follow-up-Anfragen."""

    # Patterns für explizites negatives Feedback
    NEGATIVE_PATTERNS = [
        "nein", "falsch", "nicht das", "stimmt nicht", "das ist falsch",
        "no", "wrong", "incorrect", "that's not", "not what i",
        "das war nicht", "ich meinte nicht", "versuch nochmal",
    ]

    # Patterns für explizites positives Feedback
    POSITIVE_PATTERNS = [
        "danke", "perfekt", "genau", "richtig", "super", "gut",
        "thanks", "perfect", "exactly", "correct", "great", "good",
    ]

    # Patterns für Klärungsbedarf
    CLARIFICATION_PATTERNS = [
        "warum", "erkläre", "was bedeutet", "verstehe nicht",
        "why", "explain", "what does", "don't understand",
        "wie meinst du", "kannst du erklären",
    ]

    def infer(
        self,
        current_query: str,
        previous_query: str,
        previous_response: str,
    ) -> UserFeedback:
        """
        Inferiert User-Feedback basierend auf Follow-up-Anfrage.

        Args:
            current_query: Aktuelle User-Anfrage
            previous_query: Vorherige User-Anfrage
            previous_response: KI-Antwort auf vorherige Anfrage

        Returns:
            UserFeedback mit Typ und Signal
        """
        current_lower = current_query.lower()
        previous_lower = previous_query.lower()

        # 1. Explizites negatives Feedback
        if any(p in current_lower for p in self.NEGATIVE_PATTERNS):
            return UserFeedback(
                type="explicit_negative",
                signal="negative_words",
                details=None
            )

        # 2. Explizites positives Feedback
        if any(p in current_lower for p in self.POSITIVE_PATTERNS):
            return UserFeedback(
                type="explicit_positive",
                signal="positive_words",
                details=None
            )

        # 3. Klärungsbedarf
        if any(p in current_lower for p in self.CLARIFICATION_PATTERNS):
            return UserFeedback(
                type="inferred_clarification",
                signal="why_question",
                details=None
            )

        # 4. Gleiche Frage wiederholt (Retry)
        similarity = self._calculate_similarity(current_lower, previous_lower)
        if similarity > 0.7:
            return UserFeedback(
                type="inferred_retry",
                signal="same_question",
                details=f"similarity={similarity:.2f}"
            )

        # 5. Themenwechsel = Erfolg
        if similarity < 0.3:
            return UserFeedback(
                type="inferred_success",
                signal="topic_change",
                details=None
            )

        # 6. Unbestimmt
        return UserFeedback(
            type="unknown",
            signal="mixed",
            details=f"similarity={similarity:.2f}"
        )

    def _calculate_similarity(self, text1: str, text2: str) -> float:
        """Berechnet Jaccard-Ähnlichkeit zwischen zwei Texten."""
        if not text1 or not text2:
            return 0.0

        words1 = set(text1.split())
        words2 = set(text2.split())

        if not words1 or not words2:
            return 0.0

        intersection = len(words1 & words2)
        union = len(words1 | words2)

        return intersection / union if union > 0 else 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# Analytics Logger
# ═══════════════════════════════════════════════════════════════════════════════

class AnalyticsLogger:
    """
    Loggt anonymisierte Analytics-Daten für Programmverbesserung.

    Features:
    - Tool-Selection & Execution Tracking
    - User-Feedback Inference
    - Automatische Anonymisierung
    - JSONL-Export für Claude-Analyse
    - Automatische Komprimierung alter Daten
    """

    def __init__(self):
        # Config laden
        self._enabled = settings.analytics.enabled
        self._storage_path = Path(settings.analytics.storage_path)
        self._include = settings.analytics.include
        self._log_level = settings.analytics.log_level

        # Anonymizer mit Config initialisieren
        anon_config = settings.analytics.anonymize
        self._anonymizer = Anonymizer(AnonymizerConfig(
            enabled=anon_config.enabled,
            mask_ips=anon_config.mask_ips,
            mask_paths=anon_config.mask_paths,
            mask_credentials=anon_config.mask_credentials,
            mask_emails=anon_config.mask_emails,
            mask_urls_with_auth=anon_config.mask_urls_with_auth,
            mask_company_data=anon_config.mask_company_data,
            company_patterns=list(anon_config.company_patterns),
            path_whitelist=list(anon_config.path_whitelist),
        ))

        # Feedback Inferrer
        self._feedback_inferrer = FeedbackInferrer()

        # Aktuelle Chain
        self._current_chain: Optional[AnalyticsChain] = None
        self._chain_start_time: float = 0

        # Vorherige Query (für Feedback-Inference)
        self._previous_query: str = ""
        self._previous_response: str = ""

        # Storage initialisieren
        if self._enabled:
            self._storage_path.mkdir(parents=True, exist_ok=True)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def enable(self) -> None:
        """Aktiviert Analytics."""
        self._enabled = True
        self._storage_path.mkdir(parents=True, exist_ok=True)
        logger.info("[analytics] Analytics aktiviert")

    def disable(self) -> None:
        """Deaktiviert Analytics."""
        self._enabled = False
        logger.info("[analytics] Analytics deaktiviert")

    # ═══════════════════════════════════════════════════════════════════════════
    # Chain Lifecycle
    # ═══════════════════════════════════════════════════════════════════════════

    async def start_chain(
        self,
        query: str,
        model: str,
        model_settings: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Startet eine neue Analytics-Kette.

        Args:
            query: User-Query (wird anonymisiert)
            model: Verwendetes Modell
            model_settings: Modell-Einstellungen (temperature etc.)

        Returns:
            chain_id für Referenz
        """
        if not self._enabled:
            return ""

        # Feedback von vorheriger Chain inferieren
        if self._current_chain and self._include.user_feedback:
            feedback = self._feedback_inferrer.infer(
                current_query=query,
                previous_query=self._previous_query,
                previous_response=self._previous_response,
            )
            self._current_chain.user_feedback = feedback
            # Vorherige Chain speichern
            await self._save_chain(self._current_chain)

        # Query anonymisieren
        query_hash, categories = self._anonymizer.hash_query(query)

        # Settings anonymisieren
        safe_settings = {}
        if model_settings and self._include.model_settings:
            safe_settings = self._anonymizer.anonymize_dict(model_settings)

        # Neue Chain erstellen
        chain_id = f"c_{uuid.uuid4().hex[:8]}"
        self._current_chain = AnalyticsChain(
            chain_id=chain_id,
            timestamp=datetime.utcnow().isoformat(),
            query_hash=query_hash,
            query_categories=categories,
            model=model,
            model_settings=safe_settings,
        )
        self._chain_start_time = time.time()
        self._previous_query = query

        logger.debug(f"[analytics] Chain gestartet: {chain_id}")
        return chain_id

    async def log_tool_decision(
        self,
        tool_name: str,
        reason: str = "",
        alternatives: Optional[List[str]] = None,
        iteration: int = 0,
    ) -> None:
        """
        Loggt eine Tool-Auswahl-Entscheidung.

        Args:
            tool_name: Gewähltes Tool
            reason: Begründung (optional)
            alternatives: Alternative Tools die möglich wären
            iteration: Iteration-Nummer im Loop
        """
        if not self._enabled or not self._current_chain:
            return

        if not self._include.tool_selection:
            return

        # Reason anonymisieren wenn vorhanden
        safe_reason = ""
        if reason and self._include.decision_reasoning:
            safe_reason = self._anonymizer.anonymize(reason)

        decision = ToolDecision(
            step=len(self._current_chain.tool_chain) + 1,
            tool=tool_name,
            reason=safe_reason,
            alternatives=alternatives or [],
        )

        logger.debug(f"[analytics] Tool-Decision: {tool_name}")

    async def log_tool_execution(
        self,
        tool_name: str,
        success: bool,
        duration_ms: int,
        error: Optional[str] = None,
        result_size: int = 0,
        reason: str = "",
        alternatives: Optional[List[str]] = None,
    ) -> None:
        """
        Loggt eine Tool-Ausführung.

        Args:
            tool_name: Ausgeführtes Tool
            success: War die Ausführung erfolgreich?
            duration_ms: Dauer in Millisekunden
            error: Fehlermeldung (wird anonymisiert)
            result_size: Größe des Ergebnisses in Bytes
            reason: Begründung für Tool-Wahl
            alternatives: Alternative Tools
        """
        if not self._enabled or not self._current_chain:
            return

        if not self._include.tool_execution:
            return

        # Status bestimmen
        if success:
            status = "success"
            error_type = None
        else:
            status = "error"
            error_type = self._classify_error(error or "")

        # Error anonymisieren
        if error and self._include.tool_errors:
            error = self._anonymizer.anonymize(error)

        # Reason anonymisieren
        safe_reason = ""
        if reason and self._include.decision_reasoning:
            safe_reason = self._anonymizer.anonymize(reason)

        execution = ToolExecution(
            step=len(self._current_chain.tool_chain) + 1,
            tool=tool_name,
            status=status,
            duration_ms=duration_ms,
            error_type=error_type,
            result_tokens=result_size // 4,  # Grobe Token-Schätzung
            reason=safe_reason,
            alternatives=alternatives or [],
        )

        self._current_chain.tool_chain.append(execution)
        self._current_chain.total_iterations = len(self._current_chain.tool_chain)

        logger.debug(f"[analytics] Tool-Execution: {tool_name} -> {status}")

    async def end_chain(
        self,
        status: str,
        response: str = "",
    ) -> None:
        """
        Beendet die aktuelle Kette.

        Args:
            status: Endstatus (resolved, failed, timeout, user_abort)
            response: KI-Antwort (für Feedback-Inference)
        """
        if not self._enabled or not self._current_chain:
            return

        self._current_chain.final_status = status
        self._current_chain.duration_ms = int((time.time() - self._chain_start_time) * 1000)
        self._previous_response = response

        logger.debug(f"[analytics] Chain beendet: {self._current_chain.chain_id} -> {status}")

        # NICHT sofort speichern - warten auf nächste Query für Feedback

    async def force_save(self) -> None:
        """Erzwingt Speichern der aktuellen Chain (z.B. bei Session-Ende)."""
        if self._current_chain:
            await self._save_chain(self._current_chain)
            self._current_chain = None

    # ═══════════════════════════════════════════════════════════════════════════
    # Error Classification
    # ═══════════════════════════════════════════════════════════════════════════

    def _classify_error(self, error: str) -> str:
        """Klassifiziert einen Fehler in Kategorien."""
        error_lower = error.lower()

        if any(w in error_lower for w in ["connection", "timeout", "unreachable", "refused"]):
            return "connection"
        if any(w in error_lower for w in ["permission", "access", "denied", "forbidden"]):
            return "permission"
        if any(w in error_lower for w in ["invalid", "validation", "required", "missing"]):
            return "validation"
        if any(w in error_lower for w in ["not found", "404", "does not exist"]):
            return "not_found"
        if any(w in error_lower for w in ["rate limit", "throttl", "too many"]):
            return "rate_limit"

        return "other"

    # ═══════════════════════════════════════════════════════════════════════════
    # Storage
    # ═══════════════════════════════════════════════════════════════════════════

    def _get_log_file(self, date: Optional[datetime] = None) -> Path:
        """Gibt den Pfad zur Log-Datei für ein Datum zurück."""
        if date is None:
            date = datetime.utcnow()

        date_str = date.strftime("%Y-%m-%d")
        date_dir = self._storage_path / date_str
        date_dir.mkdir(parents=True, exist_ok=True)

        return date_dir / "chains.jsonl"

    async def _save_chain(self, chain: AnalyticsChain) -> None:
        """Speichert eine Chain in die JSONL-Datei."""
        if not self._enabled:
            return

        log_file = self._get_log_file()
        line = json.dumps(chain.to_dict(), ensure_ascii=False) + "\n"

        try:
            if HAS_AIOFILES:
                async with aiofiles.open(log_file, "a", encoding="utf-8") as f:
                    await f.write(line)
            else:
                with open(log_file, "a", encoding="utf-8") as f:
                    f.write(line)

            logger.debug(f"[analytics] Chain gespeichert: {chain.chain_id}")

        except Exception as e:
            logger.warning(f"[analytics] Fehler beim Speichern: {e}")

    # ═══════════════════════════════════════════════════════════════════════════
    # Export & Analysis
    # ═══════════════════════════════════════════════════════════════════════════

    async def get_summary(self, days: int = 7) -> Dict[str, Any]:
        """
        Erstellt eine Zusammenfassung der letzten N Tage.

        Returns:
            Dictionary mit aggregierten Statistiken
        """
        if not self._enabled:
            return {"enabled": False}

        summary = {
            "enabled": True,
            "period_days": days,
            "total_chains": 0,
            "tools_used": {},
            "tool_success_rate": {},
            "error_types": {},
            "avg_iterations": 0,
            "feedback_distribution": {},
            "model_usage": {},
        }

        total_iterations = 0
        chains_with_iterations = 0

        # Letzte N Tage durchgehen
        for i in range(days):
            date = datetime.utcnow() - timedelta(days=i)
            log_file = self._get_log_file(date)

            if not log_file.exists():
                continue

            try:
                with open(log_file, "r", encoding="utf-8") as f:
                    for line in f:
                        try:
                            data = json.loads(line.strip())
                            summary["total_chains"] += 1

                            # Tools zählen
                            for tool_exec in data.get("tool_chain", []):
                                tool = tool_exec.get("tool", "unknown")
                                status = tool_exec.get("status", "unknown")

                                summary["tools_used"][tool] = summary["tools_used"].get(tool, 0) + 1

                                if tool not in summary["tool_success_rate"]:
                                    summary["tool_success_rate"][tool] = {"success": 0, "total": 0}
                                summary["tool_success_rate"][tool]["total"] += 1
                                if status == "success":
                                    summary["tool_success_rate"][tool]["success"] += 1

                                # Error-Typen
                                if status == "error":
                                    error_type = tool_exec.get("error_type", "other")
                                    summary["error_types"][error_type] = summary["error_types"].get(error_type, 0) + 1

                            # Iterationen
                            iterations = data.get("total_iterations", 0)
                            if iterations > 0:
                                total_iterations += iterations
                                chains_with_iterations += 1

                            # Feedback
                            feedback = data.get("user_feedback", {})
                            if feedback:
                                fb_type = feedback.get("type", "unknown")
                                summary["feedback_distribution"][fb_type] = \
                                    summary["feedback_distribution"].get(fb_type, 0) + 1

                            # Model
                            model = data.get("model", "unknown")
                            summary["model_usage"][model] = summary["model_usage"].get(model, 0) + 1

                        except json.JSONDecodeError:
                            continue

            except Exception as e:
                logger.warning(f"[analytics] Fehler beim Lesen von {log_file}: {e}")

        # Durchschnitte berechnen
        if chains_with_iterations > 0:
            summary["avg_iterations"] = round(total_iterations / chains_with_iterations, 2)

        # Success-Rate als Prozent
        for tool, stats in summary["tool_success_rate"].items():
            if stats["total"] > 0:
                stats["rate"] = round(100 * stats["success"] / stats["total"], 1)

        return summary

    async def export_for_analysis(
        self,
        days: int = 30,
        output_path: Optional[str] = None,
    ) -> str:
        """
        Exportiert Daten für Claude-Analyse.

        Args:
            days: Anzahl der Tage
            output_path: Optionaler Output-Pfad

        Returns:
            Pfad zur Export-Datei
        """
        if output_path:
            export_file = Path(output_path)
        else:
            export_file = self._storage_path / f"export_{datetime.utcnow().strftime('%Y%m%d')}.jsonl"

        chains = []

        for i in range(days):
            date = datetime.utcnow() - timedelta(days=i)
            log_file = self._get_log_file(date)

            if not log_file.exists():
                continue

            try:
                with open(log_file, "r", encoding="utf-8") as f:
                    for line in f:
                        chains.append(line)
            except Exception:
                continue

        with open(export_file, "w", encoding="utf-8") as f:
            f.writelines(chains)

        return str(export_file)

    # ═══════════════════════════════════════════════════════════════════════════
    # Maintenance
    # ═══════════════════════════════════════════════════════════════════════════

    async def compress_old_data(self) -> int:
        """
        Komprimiert Daten älter als compress_after_days.

        Returns:
            Anzahl komprimierter Dateien
        """
        if not self._enabled:
            return 0

        compress_after = settings.analytics.compress_after_days
        cutoff = datetime.utcnow() - timedelta(days=compress_after)
        compressed = 0

        for date_dir in self._storage_path.iterdir():
            if not date_dir.is_dir():
                continue

            try:
                dir_date = datetime.strptime(date_dir.name, "%Y-%m-%d")
            except ValueError:
                continue

            if dir_date >= cutoff:
                continue

            # .jsonl Dateien komprimieren
            for jsonl_file in date_dir.glob("*.jsonl"):
                if jsonl_file.suffix == ".jsonl":
                    gz_file = jsonl_file.with_suffix(".jsonl.gz")

                    with open(jsonl_file, "rb") as f_in:
                        with gzip.open(gz_file, "wb") as f_out:
                            f_out.writelines(f_in)

                    jsonl_file.unlink()
                    compressed += 1

        logger.info(f"[analytics] {compressed} Dateien komprimiert")
        return compressed

    async def cleanup_old_data(self) -> int:
        """
        Löscht Daten älter als retention_days.

        Returns:
            Anzahl gelöschter Verzeichnisse
        """
        if not self._enabled:
            return 0

        retention = settings.analytics.retention_days
        cutoff = datetime.utcnow() - timedelta(days=retention)
        deleted = 0

        for date_dir in self._storage_path.iterdir():
            if not date_dir.is_dir():
                continue

            try:
                dir_date = datetime.strptime(date_dir.name, "%Y-%m-%d")
            except ValueError:
                continue

            if dir_date < cutoff:
                # Verzeichnis löschen
                for f in date_dir.iterdir():
                    f.unlink()
                date_dir.rmdir()
                deleted += 1

        logger.info(f"[analytics] {deleted} alte Verzeichnisse gelöscht")
        return deleted


# ═══════════════════════════════════════════════════════════════════════════════
# Singleton
# ═══════════════════════════════════════════════════════════════════════════════

_analytics_logger: Optional[AnalyticsLogger] = None


def get_analytics_logger() -> AnalyticsLogger:
    """Gibt Singleton-Instanz zurück."""
    global _analytics_logger
    if _analytics_logger is None:
        _analytics_logger = AnalyticsLogger()
    return _analytics_logger
