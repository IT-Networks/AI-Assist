"""
Change Tracker – Verfolgt Datei-Änderungen während Team-Ausführung.

Unterstützt:
- Tracking von CREATE/MODIFY/DELETE Operationen
- Manifest-Speicherung für Rollback
- Checksummen für Datei-Integrität
"""

import hashlib
import json
import logging
import os
import shutil
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class FileChange:
    """Einzelne Dateiänderung."""
    type: str  # CREATE, MODIFY, DELETE
    file: str
    size_bytes: int = 0
    size_bytes_before: int = 0  # Für MODIFY
    size_bytes_after: int = 0   # Für MODIFY
    agent: str = ""
    backup_path: Optional[str] = None  # Pfad zu Backup bei MODIFY
    checksum: str = ""  # SHA256 der Datei
    diff_lines: str = ""  # +X, -Y format für MODIFY
    timestamp: str = ""


class ChangeTracker:
    """Verfolgt Dateiänderungen während Team-Ausführung."""

    def __init__(self, feature_id: str, backup_dir: str = ".backups"):
        """
        Initialize tracker.

        Args:
            feature_id: Eindeutige Kennung der Feature (z.B. feat_auth_20260415_001)
            backup_dir: Verzeichnis für Backups
        """
        self.feature_id = feature_id
        self.backup_dir = Path(backup_dir)
        self.backup_dir.mkdir(exist_ok=True)

        self.changes: List[FileChange] = []
        self.manifest_path = self.backup_dir / f"{feature_id}_manifest.json"

    def track_create(self, file_path: str, agent: str) -> FileChange:
        """Track neue Datei-Erstellung."""
        file_p = Path(file_path)

        change = FileChange(
            type="CREATE",
            file=file_path,
            agent=agent,
            timestamp=datetime.utcnow().isoformat(),
        )

        # Checksum wird gespeichert wenn Datei tatsächlich existiert
        if file_p.exists():
            change.size_bytes = file_p.stat().st_size
            change.checksum = self._compute_checksum(file_path)

        self.changes.append(change)
        logger.info(f"[ChangeTracker] CREATE tracked: {file_path} (by {agent})")
        return change

    def track_modify(self, file_path: str, agent: str, diff_lines: Optional[str] = None) -> FileChange:
        """
        Track Datei-Modifikation.

        Args:
            file_path: Zu ändernde Datei
            agent: Agent, der die Änderung macht
            diff_lines: Optional: "+X, -Y" Format
        """
        file_p = Path(file_path)

        # Backup des Original-Inhalts erstellen
        backup_path = None
        if file_p.exists():
            backup_path = self._create_backup(file_path)
            size_before = file_p.stat().st_size
        else:
            size_before = 0

        change = FileChange(
            type="MODIFY",
            file=file_path,
            agent=agent,
            backup_path=backup_path,
            size_bytes_before=size_before,
            diff_lines=diff_lines or "",
            timestamp=datetime.utcnow().isoformat(),
        )

        # Neuer Checksum nach Änderung
        if file_p.exists():
            change.size_bytes_after = file_p.stat().st_size
            change.checksum = self._compute_checksum(file_path)

        self.changes.append(change)
        logger.info(f"[ChangeTracker] MODIFY tracked: {file_path} (by {agent}) → {backup_path}")
        return change

    def track_delete(self, file_path: str, agent: str) -> FileChange:
        """Track Datei-Löschung."""
        file_p = Path(file_path)

        # Backup des zu löschenden Inhalts
        backup_path = None
        if file_p.exists():
            backup_path = self._create_backup(file_path)

        change = FileChange(
            type="DELETE",
            file=file_path,
            agent=agent,
            backup_path=backup_path,
            timestamp=datetime.utcnow().isoformat(),
        )

        self.changes.append(change)
        logger.info(f"[ChangeTracker] DELETE tracked: {file_path} (by {agent}) → backup: {backup_path}")
        return change

    def save_manifest(self, user_request: str, status: str = "IN_PROGRESS",
                     test_results: Optional[Dict] = None, git_commit: str = "") -> Dict:
        """
        Speichere Manifest mit allen Änderungen.

        Args:
            user_request: Original-Anfrage des Users
            status: IN_PROGRESS, COMPLETED, ROLLED_BACK
            test_results: Test-Ergebnisse (pytest, npm test)
            git_commit: Git-Commit-Hash nach Merge

        Returns:
            Manifest Dictionary
        """
        manifest = {
            "feature_id": self.feature_id,
            "user_request": user_request,
            "started_at": datetime.utcnow().isoformat(),
            "status": status,
            "changes": [asdict(c) for c in self.changes],
            "test_results": test_results or {},
            "git_commit": git_commit,
        }

        with open(self.manifest_path, 'w') as f:
            json.dump(manifest, f, indent=2)

        logger.info(f"[ChangeTracker] Manifest saved: {self.manifest_path}")
        return manifest

    def load_manifest(self) -> Optional[Dict]:
        """Lade gespeichertea Manifest."""
        if not self.manifest_path.exists():
            return None

        with open(self.manifest_path, 'r') as f:
            return json.load(f)

    def rollback(self, dry_run: bool = False) -> Dict[str, int]:
        """
        Rollback aller Änderungen.

        Args:
            dry_run: Nur zeigen was geändert werden würde

        Returns:
            Statistics: {restored: 3, deleted: 2, failed: 0}
        """
        stats = {"restored": 0, "deleted": 0, "failed": 0}

        # Changes in reverse order (LIFO)
        for change in reversed(self.changes):
            try:
                if change.type == "CREATE":
                    # Lösche erstellte Datei
                    file_p = Path(change.file)
                    if file_p.exists():
                        if not dry_run:
                            file_p.unlink()
                        logger.info(f"[Rollback] Deleted created file: {change.file}")
                        stats["deleted"] += 1

                elif change.type == "MODIFY":
                    # Stelle von Backup wieder her
                    if change.backup_path and Path(change.backup_path).exists():
                        if not dry_run:
                            shutil.copy(change.backup_path, change.file)
                        logger.info(f"[Rollback] Restored from backup: {change.file}")
                        stats["restored"] += 1
                    else:
                        logger.warning(f"[Rollback] No backup found for: {change.file}")
                        stats["failed"] += 1

                elif change.type == "DELETE":
                    # Stelle von Backup wieder her
                    if change.backup_path and Path(change.backup_path).exists():
                        if not dry_run:
                            shutil.copy(change.backup_path, change.file)
                        logger.info(f"[Rollback] Restored deleted file: {change.file}")
                        stats["restored"] += 1

            except Exception as e:
                logger.error(f"[Rollback] Error rolling back {change.file}: {e}")
                stats["failed"] += 1

        logger.info(f"[Rollback] Complete: {stats}")
        return stats

    def get_summary(self) -> Dict:
        """
        Gib Zusammenfassung der Änderungen zurück.

        Returns:
            Summary mit Create/Modify/Delete counts
        """
        summary = {
            "total": len(self.changes),
            "created": sum(1 for c in self.changes if c.type == "CREATE"),
            "modified": sum(1 for c in self.changes if c.type == "MODIFY"),
            "deleted": sum(1 for c in self.changes if c.type == "DELETE"),
            "files": [c.file for c in self.changes],
            "by_agent": {}
        }

        # Group by agent
        for change in self.changes:
            if change.agent not in summary["by_agent"]:
                summary["by_agent"][change.agent] = []
            summary["by_agent"][change.agent].append({
                "type": change.type,
                "file": change.file
            })

        return summary

    # === Private Methods ===

    def _create_backup(self, file_path: str) -> str:
        """Erstelle Backup einer Datei."""
        file_p = Path(file_path)
        if not file_p.exists():
            return ""

        # Backup-Pfad: .backups/feature_id/original_name.timestamp
        feature_backup = self.backup_dir / self.feature_id
        feature_backup.mkdir(exist_ok=True)

        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        # Preserve original filename structure
        safe_name = str(file_p).replace('/', '_').replace('\\', '_')
        backup_name = f"{safe_name}.{timestamp}"
        backup_path = feature_backup / backup_name

        shutil.copy2(file_p, backup_path)
        logger.debug(f"[ChangeTracker] Backup created: {backup_path}")
        return str(backup_path)

    @staticmethod
    def _compute_checksum(file_path: str) -> str:
        """Berechne SHA256 Checksum einer Datei."""
        file_p = Path(file_path)
        if not file_p.exists():
            return ""

        sha256_hash = hashlib.sha256()
        with open(file_p, 'rb') as f:
            for byte_block in iter(lambda: f.read(4096), b''):
                sha256_hash.update(byte_block)

        return sha256_hash.hexdigest()
