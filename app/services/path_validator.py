"""
Path Validator - Validates file paths for script execution safety.

Provides:
- Path validation against whitelisted directories
- System-critical path detection and blocking
- Path normalization and security checks
- Audit logging for path access requests
"""

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class PathValidationResult:
    """Result of path validation."""
    approved: bool
    reason: str  # "ok" | "system_critical" | "invalid_path" | "access_denied"
    suggestion: Optional[str] = None
    normalized_path: Optional[str] = None


class PathValidator:
    """Validates file paths for script execution."""

    # System-critical paths that can never be approved
    SYSTEM_CRITICAL_PATHS = [
        r'C:\\Windows',
        r'C:\\Program Files',
        r'C:\\Program Files (x86)',
        r'C:\\ProgramData',
        r'C:\\$Recycle.Bin',
        r'C:\\System Volume Information',
        r'C:\\boot',
        r'/etc',
        r'/bin',
        r'/sbin',
        r'/usr/bin',
        r'/usr/sbin',
        r'/System',
        r'/Library',
        r'/Applications',
    ]

    def __init__(self):
        """Initialize path validator."""
        pass

    @staticmethod
    def normalize_path(path: str) -> str:
        """
        Normalize path to prevent bypass via ../ or mixed case.

        Args:
            path: Path to normalize

        Returns:
            Normalized absolute path (lowercase on Windows)
        """
        normalized = os.path.normpath(os.path.abspath(path))
        # Lowercase on Windows to prevent case-based bypass
        if os.name == 'nt':
            normalized = normalized.lower()
        return normalized

    def is_system_critical(self, path: str) -> bool:
        """
        Check if path is system directory that cannot be approved.

        Args:
            path: Path to check

        Returns:
            True if path is system-critical
        """
        normalized = self.normalize_path(path)

        # Check against system critical paths
        for critical_path in self.SYSTEM_CRITICAL_PATHS:
            # Normalize critical path for comparison
            critical_normalized = self.normalize_path(critical_path)
            # Check if path starts with critical path
            if normalized.startswith(critical_normalized):
                return True

        return False

    def validate_approval(
        self,
        requested_path: str,
        access_type: str = "write",  # "read" | "write" | "delete"
        whitelisted_paths: Optional[list] = None
    ) -> PathValidationResult:
        """
        Validate if a path can be approved by user.

        Args:
            requested_path: Path being requested
            access_type: Type of access (read/write/delete)
            whitelisted_paths: List of pre-approved paths (if None, treats as empty)

        Returns:
            PathValidationResult with approval decision
        """
        try:
            # Normalize requested path
            normalized = self.normalize_path(requested_path)

            # Check if already whitelisted
            if whitelisted_paths:
                whitelisted_normalized = [
                    self.normalize_path(p) for p in whitelisted_paths
                ]
                if normalized in whitelisted_normalized:
                    return PathValidationResult(
                        approved=True,
                        reason="ok",
                        normalized_path=normalized
                    )

            # Check if system-critical
            if self.is_system_critical(normalized):
                return PathValidationResult(
                    approved=False,
                    reason="system_critical",
                    suggestion="System-Verzeichnisse dürfen nicht modifiziert werden",
                    normalized_path=normalized
                )

            # Path is not whitelisted and not system-critical
            # → Can be approved by user
            return PathValidationResult(
                approved=False,
                reason="not_whitelisted",
                suggestion=f"Path kann zu Whitelist hinzugefügt werden",
                normalized_path=normalized
            )

        except Exception as e:
            logger.error(f"Path validation error: {e}")
            return PathValidationResult(
                approved=False,
                reason="access_denied",
                suggestion=f"Fehler bei Pfad-Validierung: {str(e)}"
            )

    def validate_path_exists(self, path: str) -> bool:
        """
        Check if path or parent directory exists.

        Args:
            path: Path to check

        Returns:
            True if path or parent exists
        """
        normalized = self.normalize_path(path)
        path_obj = Path(normalized)

        # Check if path itself exists
        if path_obj.exists():
            return True

        # Check if parent exists (for file write operations)
        if path_obj.parent.exists():
            return True

        return False

    def log_path_access_request(
        self,
        script_id: str,
        path: str,
        access_type: str,
        approved: bool,
        user: Optional[str] = None
    ) -> None:
        """
        Log path access request for audit trail.

        Args:
            script_id: ID of script requesting access
            path: Path being accessed
            access_type: Type of access (read/write/delete)
            approved: Whether access was approved
            user: Optional user identifier
        """
        status = "APPROVED" if approved else "DENIED"
        log_msg = f"[PATH_ACCESS] {status} script={script_id} type={access_type} path={path}"
        if user:
            log_msg += f" user={user}"

        if approved:
            logger.info(log_msg)
        else:
            logger.warning(log_msg)
