"""
Integration tests for Implementation Execution Team.

Tests the complete flow:
1. Plan approval
2. Execution with change tracking
3. Verification and rollback
"""

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent.multi_agent.approval_manager import (
    ApprovalDecision,
    ApprovalManager,
    ApprovalRequest,
    ApprovalResponse,
    ApprovalStage,
)
from app.agent.multi_agent.change_tracker import ChangeTracker, FileChange


class TestChangeTracker:
    """Test ChangeTracker functionality."""

    def test_track_create(self, tmp_path):
        """Test tracking file creation."""
        tracker = ChangeTracker(feature_id="test_1", backup_dir=str(tmp_path))

        # Create a test file
        test_file = tmp_path / "test.py"
        test_file.write_text("print('hello')")

        change = tracker.track_create(str(test_file), agent="backend-engineer")

        assert change.type == "CREATE"
        assert change.file == str(test_file)
        assert change.agent == "backend-engineer"
        assert change.size_bytes > 0
        assert change.checksum != ""

    def test_track_modify(self, tmp_path):
        """Test tracking file modification."""
        tracker = ChangeTracker(feature_id="test_2", backup_dir=str(tmp_path))

        # Create and modify a file
        test_file = tmp_path / "test.py"
        test_file.write_text("original content")

        change = tracker.track_modify(str(test_file), agent="backend-engineer", diff_lines="+5, -2")

        assert change.type == "MODIFY"
        assert change.agent == "backend-engineer"
        assert change.backup_path is not None
        assert Path(change.backup_path).exists()

        # Verify backup exists
        backup = Path(change.backup_path)
        assert backup.read_text() == "original content"

    def test_track_delete(self, tmp_path):
        """Test tracking file deletion."""
        tracker = ChangeTracker(feature_id="test_3", backup_dir=str(tmp_path))

        test_file = tmp_path / "test.py"
        test_file.write_text("content to be deleted")

        change = tracker.track_delete(str(test_file), agent="test-engineer")

        assert change.type == "DELETE"
        assert change.backup_path is not None

    def test_get_summary(self, tmp_path):
        """Test change summary generation."""
        tracker = ChangeTracker(feature_id="test_4", backup_dir=str(tmp_path))

        test_file1 = tmp_path / "file1.py"
        test_file1.write_text("content1")
        test_file2 = tmp_path / "file2.py"
        test_file2.write_text("content2")

        tracker.track_create(str(test_file1), agent="backend-engineer")
        tracker.track_modify(str(test_file2), agent="frontend-engineer")

        summary = tracker.get_summary()

        assert summary["total"] == 2
        assert summary["created"] == 1
        assert summary["modified"] == 1
        assert len(summary["files"]) == 2
        assert "backend-engineer" in summary["by_agent"]
        assert "frontend-engineer" in summary["by_agent"]

    def test_save_load_manifest(self, tmp_path):
        """Test manifest persistence."""
        tracker = ChangeTracker(feature_id="test_5", backup_dir=str(tmp_path))

        test_file = tmp_path / "test.py"
        test_file.write_text("content")
        tracker.track_create(str(test_file), agent="backend-engineer")

        manifest = tracker.save_manifest(
            user_request="Implement authentication",
            status="COMPLETED",
            test_results={"passed": 10, "failed": 0},
            git_commit="abc123"
        )

        assert manifest["feature_id"] == tracker.feature_id
        assert manifest["user_request"] == "Implement authentication"
        assert manifest["status"] == "COMPLETED"
        assert len(manifest["changes"]) == 1

        # Load and verify
        loaded = tracker.load_manifest()
        assert loaded["feature_id"] == tracker.feature_id
        assert loaded["test_results"]["passed"] == 10

    def test_rollback_create(self, tmp_path):
        """Test rollback of created file."""
        tracker = ChangeTracker(feature_id="test_6", backup_dir=str(tmp_path))

        test_file = tmp_path / "new_file.py"
        test_file.write_text("new content")

        tracker.track_create(str(test_file), agent="backend-engineer")

        # File exists before rollback
        assert test_file.exists()

        # Rollback
        stats = tracker.rollback()

        # File should be deleted
        assert not test_file.exists()
        assert stats["deleted"] == 1

    def test_rollback_modify(self, tmp_path):
        """Test rollback of file modification."""
        tracker = ChangeTracker(feature_id="test_7", backup_dir=str(tmp_path))

        test_file = tmp_path / "existing_file.py"
        original_content = "original content"
        test_file.write_text(original_content)

        # Modify the file
        tracker.track_modify(str(test_file), agent="backend-engineer")
        test_file.write_text("modified content")

        # Verify modification
        assert test_file.read_text() == "modified content"

        # Rollback
        stats = tracker.rollback()

        # Content should be restored
        assert test_file.read_text() == original_content
        assert stats["restored"] == 1

    def test_rollback_delete(self, tmp_path):
        """Test rollback of file deletion."""
        tracker = ChangeTracker(feature_id="test_8", backup_dir=str(tmp_path))

        test_file = tmp_path / "to_delete.py"
        content = "content to restore"
        test_file.write_text(content)

        tracker.track_delete(str(test_file), agent="test-engineer")

        # File is still there (delete is tracked, not executed)
        assert test_file.exists()

        # Rollback
        stats = tracker.rollback()

        # File should still be there (already backed up)
        assert test_file.exists()
        assert test_file.read_text() == content
        assert stats["restored"] == 1


class TestApprovalManager:
    """Test ApprovalManager functionality."""

    @pytest.mark.asyncio
    async def test_plan_approval_approved(self):
        """Test plan approval when user approves."""
        manager = ApprovalManager()

        # Mock the approval request callback
        request_received = None

        async def mock_callback(request: ApprovalRequest):
            nonlocal request_received
            request_received = request
            # Simulate user approval
            response = ApprovalResponse(
                feature_id=request.feature_id,
                stage=request.stage,
                decision=ApprovalDecision.APPROVE
            )
            await manager.submit_response(response)

        manager._on_approval_request = mock_callback

        # Request approval
        plan = {
            "agents": ["backend", "frontend"],
            "file_count": 5,
            "estimated_duration_minutes": 10,
            "files_affected": ["app/api.py", "frontend/App.tsx"]
        }

        decision = await manager.request_plan_approval(
            feature_id="feat_123",
            title="Implement user authentication",
            plan=plan
        )

        assert decision == ApprovalDecision.APPROVE
        assert request_received is not None
        assert request_received.stage == ApprovalStage.PLAN_READY

    @pytest.mark.asyncio
    async def test_plan_approval_rejected(self):
        """Test plan approval when user rejects."""
        manager = ApprovalManager()

        async def mock_callback(request: ApprovalRequest):
            response = ApprovalResponse(
                feature_id=request.feature_id,
                stage=request.stage,
                decision=ApprovalDecision.DISCARD
            )
            await manager.submit_response(response)

        manager._on_approval_request = mock_callback

        plan = {"agents": [], "file_count": 0}

        decision = await manager.request_plan_approval(
            feature_id="feat_456",
            title="Some feature",
            plan=plan
        )

        assert decision == ApprovalDecision.DISCARD

    @pytest.mark.asyncio
    async def test_verification_approval_approved(self):
        """Test verification approval when approved."""
        manager = ApprovalManager()

        async def mock_callback(request: ApprovalRequest):
            response = ApprovalResponse(
                feature_id=request.feature_id,
                stage=request.stage,
                decision=ApprovalDecision.APPROVE
            )
            await manager.submit_response(response)

        manager._on_approval_request = mock_callback

        test_results = {
            "backend_tests": {"passed": 20, "failed": 0},
            "frontend_tests": {"passed": 15, "failed": 0},
            "coverage": {"percentage": 92}
        }

        decision = await manager.request_verification_approval(
            feature_id="feat_789",
            title="Test Feature",
            test_results=test_results,
            files_changed=["app/api.py", "frontend/App.tsx", "tests/test_api.py"]
        )

        assert decision == ApprovalDecision.APPROVE

    @pytest.mark.asyncio
    async def test_approval_timeout(self):
        """Test approval request timeout."""
        manager = ApprovalManager()

        # Don't submit any response - let it timeout
        async def mock_callback(request: ApprovalRequest):
            # Don't submit response
            pass

        manager._on_approval_request = mock_callback

        plan = {"agents": []}

        # Should timeout and return DISCARD
        with pytest.raises(asyncio.TimeoutError):
            # Override timeout to be quick for testing
            decision = await asyncio.wait_for(
                manager.request_plan_approval(
                    feature_id="feat_timeout",
                    title="Timeout test",
                    plan=plan
                ),
                timeout=1.0  # 1 second timeout for testing
            )

    @pytest.mark.asyncio
    async def test_clear_pending(self):
        """Test clearing pending approval."""
        manager = ApprovalManager()

        async def mock_callback(request: ApprovalRequest):
            pass

        manager._on_approval_request = mock_callback

        plan = {"agents": []}

        # Start approval request
        task = asyncio.create_task(
            manager.request_plan_approval(
                feature_id="feat_clear",
                title="Clear test",
                plan=plan
            )
        )

        # Give it a moment to process
        await asyncio.sleep(0.1)

        # Verify pending request exists
        pending = manager.get_pending_request()
        assert pending is not None

        # Clear it
        manager.clear_pending()

        # Verify it's cleared
        assert manager.get_pending_request() is None

        # Cancel the task to clean up
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


class TestImplementationTeamFlow:
    """Integration tests for complete implementation team flow."""

    @pytest.mark.asyncio
    async def test_complete_flow_approved(self, tmp_path):
        """Test complete flow: plan → execute → verify → approve."""
        # Setup
        change_tracker = ChangeTracker(feature_id="test_flow_1", backup_dir=str(tmp_path))
        approval_manager = ApprovalManager()

        # Track some changes
        test_file1 = tmp_path / "api.py"
        test_file1.write_text("def get_user(): pass")
        change_tracker.track_create(str(test_file1), agent="backend-engineer")

        test_file2 = tmp_path / "test.py"
        test_file2.write_text("def test_user(): pass")
        change_tracker.track_create(str(test_file2), agent="test-engineer")

        # Mock user approvals
        async def auto_approve(request: ApprovalRequest):
            response = ApprovalResponse(
                feature_id=request.feature_id,
                stage=request.stage,
                decision=ApprovalDecision.APPROVE
            )
            await approval_manager.submit_response(response)

        approval_manager._on_approval_request = auto_approve

        # Test plan approval
        plan = {
            "agents": ["backend-engineer", "test-engineer"],
            "file_count": 2,
            "estimated_duration_minutes": 5
        }

        decision = await approval_manager.request_plan_approval(
            feature_id=change_tracker.feature_id,
            title="Implement user API",
            plan=plan
        )

        assert decision == ApprovalDecision.APPROVE

        # Simulate execution and test results
        test_results = {
            "backend_tests": {"passed": 5, "failed": 0},
            "frontend_tests": {"passed": 0, "failed": 0},
            "coverage": {"percentage": 88}
        }

        # Test verification approval
        decision = await approval_manager.request_verification_approval(
            feature_id=change_tracker.feature_id,
            title="Implement user API",
            test_results=test_results,
            files_changed=[str(test_file1), str(test_file2)]
        )

        assert decision == ApprovalDecision.APPROVE

        # Save manifest
        manifest = change_tracker.save_manifest(
            user_request="Implement user API",
            status="COMPLETED",
            test_results=test_results,
            git_commit="abc123def456"
        )

        assert manifest["status"] == "COMPLETED"
        assert manifest["test_results"]["coverage"]["percentage"] == 88

    @pytest.mark.asyncio
    async def test_flow_with_rollback(self, tmp_path):
        """Test complete flow with user rejection and rollback."""
        change_tracker = ChangeTracker(feature_id="test_flow_rollback", backup_dir=str(tmp_path))

        test_file = tmp_path / "api.py"
        test_file.write_text("old code")

        change_tracker.track_modify(str(test_file), agent="backend-engineer")
        test_file.write_text("new code")

        # Verify modification
        assert test_file.read_text() == "new code"

        # Rollback
        stats = change_tracker.rollback()

        # Verify restoration
        assert test_file.read_text() == "old code"
        assert stats["restored"] == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
