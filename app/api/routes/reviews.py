"""
PR Review API - Endpoints for automated code review.

Features:
- Manual review triggering (no auto-post)
- Copy-friendly fix formatting
- Custom rule management
- Review history and statistics
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.services.pr_review import (
    get_pr_review_service,
    PRReviewConfig,
    ReviewType,
    Severity,
    ReviewStatus,
    CustomRule,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/reviews", tags=["reviews"])


# ═══════════════════════════════════════════════════════════════════════════════
# Request/Response Models
# ═══════════════════════════════════════════════════════════════════════════════

class ConfigRequest(BaseModel):
    """PR review configuration request."""
    enabled: bool = Field(default=True)
    reviewTypes: List[str] = Field(default_factory=lambda: ["security", "quality", "style"])
    minSeverity: str = Field(default="low", pattern="^(critical|high|medium|low|info)$")
    autoReviewOnPr: bool = Field(default=False)
    includeTestSuggestions: bool = Field(default=True)
    maxCommentsPerFile: int = Field(default=10, ge=1, le=50)
    maxTotalComments: int = Field(default=50, ge=1, le=200)


class ConfigResponse(BaseModel):
    """PR review configuration response."""
    enabled: bool
    reviewTypes: List[str]
    minSeverity: str
    autoReviewOnPr: bool
    includeTestSuggestions: bool
    maxCommentsPerFile: int
    maxTotalComments: int


class TriggerReviewRequest(BaseModel):
    """Request to trigger a PR review."""
    repoOwner: str = Field(min_length=1)
    repoName: str = Field(min_length=1)
    prNumber: int = Field(ge=1)
    headSha: str = Field(min_length=7, max_length=40)
    baseSha: str = Field(min_length=7, max_length=40)
    files: Dict[str, str] = Field(description="Map of file paths to content")
    reviewTypes: Optional[List[str]] = Field(default=None)


class SuggestedFixResponse(BaseModel):
    """Suggested fix response."""
    description: str
    originalCode: str
    fixedCode: str
    diff: str


class ReviewCommentResponse(BaseModel):
    """Review comment response."""
    id: str
    filePath: str
    line: int
    endLine: Optional[int]
    side: str
    type: str
    severity: str
    title: str
    body: str
    suggestedFix: Optional[SuggestedFixResponse]
    dismissed: bool
    copied: bool


class ReviewSummaryResponse(BaseModel):
    """Review summary response."""
    totalComments: int
    bySeverity: Dict[str, int]
    byType: Dict[str, int]
    verdict: str
    filesReviewed: int
    linesAnalyzed: int


class ReviewResponse(BaseModel):
    """Full review response."""
    id: str
    prNumber: int
    repoOwner: str
    repoName: str
    headSha: str
    baseSha: str
    timestamp: int
    status: str
    summary: Optional[ReviewSummaryResponse]
    comments: List[ReviewCommentResponse]
    errorMessage: Optional[str]


class CustomRuleRequest(BaseModel):
    """Custom rule request."""
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=500)
    severity: str = Field(pattern="^(critical|high|medium|low|info)$")
    enabled: bool = Field(default=True)
    pattern: Optional[str] = Field(default=None, max_length=200)


class CustomRuleResponse(BaseModel):
    """Custom rule response."""
    id: str
    name: str
    description: str
    severity: str
    enabled: bool
    pattern: Optional[str]


class CopyableFixResponse(BaseModel):
    """Copyable fix response."""
    commentId: str
    filePath: str
    line: int
    severity: str
    title: str
    copyableText: str
    diffText: str
    originalCode: str
    fixedCode: str
    description: str


class StatsResponse(BaseModel):
    """Statistics response."""
    totalReviews: int
    completedReviews: int
    failedReviews: int
    totalComments: int
    avgCommentsPerReview: float
    customRulesCount: int


# ═══════════════════════════════════════════════════════════════════════════════
# Configuration Endpoints
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/config", response_model=ConfigResponse)
async def get_config():
    """
    Get PR review configuration.

    Returns:
        Current configuration settings
    """
    service = get_pr_review_service()
    config = service.get_config()

    return ConfigResponse(
        enabled=config.enabled,
        reviewTypes=[t.value for t in config.review_types],
        minSeverity=config.min_severity.value,
        autoReviewOnPr=config.auto_review_on_pr,
        includeTestSuggestions=config.include_test_suggestions,
        maxCommentsPerFile=config.max_comments_per_file,
        maxTotalComments=config.max_total_comments,
    )


@router.put("/config", response_model=ConfigResponse)
async def set_config(request: ConfigRequest):
    """
    Update PR review configuration.

    Args:
        request: New configuration settings

    Returns:
        Updated configuration
    """
    service = get_pr_review_service()

    config = PRReviewConfig(
        enabled=request.enabled,
        review_types=[ReviewType(t) for t in request.reviewTypes],
        min_severity=Severity(request.minSeverity),
        auto_review_on_pr=request.autoReviewOnPr,
        include_test_suggestions=request.includeTestSuggestions,
        max_comments_per_file=request.maxCommentsPerFile,
        max_total_comments=request.maxTotalComments,
    )

    result = service.set_config(config)

    return ConfigResponse(
        enabled=result.enabled,
        reviewTypes=[t.value for t in result.review_types],
        minSeverity=result.min_severity.value,
        autoReviewOnPr=result.auto_review_on_pr,
        includeTestSuggestions=result.include_test_suggestions,
        maxCommentsPerFile=result.max_comments_per_file,
        maxTotalComments=result.max_total_comments,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Statistics Endpoints (MUST be before /{review_id} to avoid route conflict)
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/stats", response_model=StatsResponse)
async def get_stats():
    """
    Get PR review statistics.

    Returns:
        Statistics
    """
    service = get_pr_review_service()
    stats = service.get_stats()
    return StatsResponse(**stats)


# ═══════════════════════════════════════════════════════════════════════════════
# Custom Rules Endpoints (MUST be before /{review_id} to avoid route conflict)
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/rules", response_model=List[CustomRuleResponse])
async def get_custom_rules():
    """
    Get all custom review rules.

    Returns:
        List of custom rules
    """
    service = get_pr_review_service()
    rules = service.get_custom_rules()
    return [_rule_to_response(r) for r in rules]


@router.post("/rules", response_model=CustomRuleResponse)
async def create_custom_rule(request: CustomRuleRequest):
    """
    Create a new custom review rule.

    Rules can use natural language descriptions and optional regex patterns.

    Args:
        request: Rule definition

    Returns:
        Created rule
    """
    import uuid

    service = get_pr_review_service()

    rule = CustomRule(
        id=str(uuid.uuid4())[:8],
        name=request.name,
        description=request.description,
        severity=Severity(request.severity),
        enabled=request.enabled,
        pattern=request.pattern,
    )

    result = service.add_custom_rule(rule)
    return _rule_to_response(result)


@router.put("/rules/{rule_id}", response_model=CustomRuleResponse)
async def update_custom_rule(rule_id: str, request: CustomRuleRequest):
    """
    Update an existing custom rule.

    Args:
        rule_id: Rule ID
        request: Updated rule definition

    Returns:
        Updated rule
    """
    service = get_pr_review_service()

    result = service.update_custom_rule(rule_id, {
        "name": request.name,
        "description": request.description,
        "severity": request.severity,
        "enabled": request.enabled,
        "pattern": request.pattern,
    })

    if not result:
        raise HTTPException(status_code=404, detail=f"Rule {rule_id} not found")

    return _rule_to_response(result)


@router.delete("/rules/{rule_id}")
async def delete_custom_rule(rule_id: str):
    """
    Delete a custom rule.

    Args:
        rule_id: Rule ID

    Returns:
        Success message
    """
    service = get_pr_review_service()
    success = service.delete_custom_rule(rule_id)

    if not success:
        raise HTTPException(status_code=404, detail=f"Rule {rule_id} not found")

    return {"success": True, "message": "Rule deleted"}


# ═══════════════════════════════════════════════════════════════════════════════
# History Endpoint (MUST be before /{review_id} to avoid route conflict)
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/history", response_model=List[ReviewResponse])
async def get_history(
    limit: int = Query(default=20, ge=1, le=100),
):
    """
    Get review history (most recent first).

    Args:
        limit: Maximum number of results

    Returns:
        List of recent reviews
    """
    service = get_pr_review_service()
    results = service.get_reviews(limit=limit)
    return [_review_to_response(r) for r in results]


# ═══════════════════════════════════════════════════════════════════════════════
# Review Endpoints
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/trigger", response_model=ReviewResponse)
async def trigger_review(request: TriggerReviewRequest):
    """
    Trigger a PR review manually.

    Note: This does NOT auto-post to GitHub. Reviews are stored locally
    and fixes can be copied manually by the user.

    Args:
        request: PR details and files to review

    Returns:
        Review result with comments and suggested fixes
    """
    service = get_pr_review_service()

    review_types = None
    if request.reviewTypes:
        review_types = [ReviewType(t) for t in request.reviewTypes]

    result = service.create_review(
        repo_owner=request.repoOwner,
        repo_name=request.repoName,
        pr_number=request.prNumber,
        head_sha=request.headSha,
        base_sha=request.baseSha,
        files=request.files,
        review_types=review_types,
    )

    return _review_to_response(result)


@router.get("/list", response_model=List[ReviewResponse])
async def get_reviews(
    repoOwner: Optional[str] = Query(default=None),
    repoName: Optional[str] = Query(default=None),
    prNumber: Optional[int] = Query(default=None),
    status: Optional[str] = Query(default=None, pattern="^(pending|analyzing|completed|failed)$"),
    limit: int = Query(default=50, ge=1, le=200),
):
    """
    Get reviews with optional filters.

    Args:
        repoOwner: Filter by repository owner
        repoName: Filter by repository name
        prNumber: Filter by PR number
        status: Filter by status
        limit: Maximum number of results

    Returns:
        List of reviews
    """
    service = get_pr_review_service()

    status_enum = ReviewStatus(status) if status else None

    results = service.get_reviews(
        repo_owner=repoOwner,
        repo_name=repoName,
        pr_number=prNumber,
        status=status_enum,
        limit=limit,
    )

    return [_review_to_response(r) for r in results]


# ═══════════════════════════════════════════════════════════════════════════════
# Single Review Endpoints (MUST be after specific routes)
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/{review_id}", response_model=ReviewResponse)
async def get_review(review_id: str):
    """
    Get a specific review.

    Args:
        review_id: Review ID

    Returns:
        Review details
    """
    service = get_pr_review_service()
    result = service.get_review(review_id)

    if not result:
        raise HTTPException(status_code=404, detail=f"Review {review_id} not found")

    return _review_to_response(result)


# ═══════════════════════════════════════════════════════════════════════════════
# Copy-Friendly Fix Endpoints
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/{review_id}/fixes", response_model=List[CopyableFixResponse])
async def get_copyable_fixes(review_id: str):
    """
    Get all fixes from a review in copy-friendly format.

    This endpoint returns fixes formatted for easy copying:
    - copyableText: Ready-to-paste format with comments
    - diffText: Unified diff format
    - originalCode/fixedCode: Raw code for manual editing

    Args:
        review_id: Review ID

    Returns:
        List of copyable fixes
    """
    service = get_pr_review_service()
    fixes = service.get_copyable_fixes(review_id)

    if not fixes:
        review = service.get_review(review_id)
        if not review:
            raise HTTPException(status_code=404, detail=f"Review {review_id} not found")

    return [CopyableFixResponse(**f) for f in fixes]


@router.get("/{review_id}/fixes/{comment_id}/patch")
async def get_fix_as_patch(review_id: str, comment_id: str):
    """
    Get a single fix as a git-style patch.

    This can be applied with `git apply` or copied directly.

    Args:
        review_id: Review ID
        comment_id: Comment ID

    Returns:
        Git patch string
    """
    service = get_pr_review_service()
    patch = service.get_fix_as_patch(review_id, comment_id)

    if not patch:
        raise HTTPException(status_code=404, detail="Fix not found")

    return {"patch": patch}


@router.post("/{review_id}/fixes/{comment_id}/copied")
async def mark_fix_copied(review_id: str, comment_id: str):
    """
    Mark a fix as copied by the user.

    This is for tracking which fixes have been applied manually.

    Args:
        review_id: Review ID
        comment_id: Comment ID

    Returns:
        Success message
    """
    service = get_pr_review_service()
    success = service.mark_copied(review_id, comment_id)

    if not success:
        raise HTTPException(status_code=404, detail="Comment not found")

    return {"success": True, "message": "Fix marked as copied"}


@router.post("/{review_id}/comments/{comment_id}/dismiss")
async def dismiss_comment(review_id: str, comment_id: str):
    """
    Dismiss a review comment.

    Dismissed comments are excluded from copyable fixes.

    Args:
        review_id: Review ID
        comment_id: Comment ID

    Returns:
        Success message
    """
    service = get_pr_review_service()
    success = service.dismiss_comment(review_id, comment_id)

    if not success:
        raise HTTPException(status_code=404, detail="Comment not found")

    return {"success": True, "message": "Comment dismissed"}


# ═══════════════════════════════════════════════════════════════════════════════
# Helper Functions
# ═══════════════════════════════════════════════════════════════════════════════

def _review_to_response(review) -> ReviewResponse:
    """Convert PRReviewResult to response model."""
    summary = None
    if review.summary:
        summary = ReviewSummaryResponse(
            totalComments=review.summary.total_comments,
            bySeverity=review.summary.by_severity,
            byType=review.summary.by_type,
            verdict=review.summary.verdict.value,
            filesReviewed=review.summary.files_reviewed,
            linesAnalyzed=review.summary.lines_analyzed,
        )

    comments = []
    for c in review.comments:
        suggested_fix = None
        if c.suggested_fix:
            suggested_fix = SuggestedFixResponse(
                description=c.suggested_fix.description,
                originalCode=c.suggested_fix.original_code,
                fixedCode=c.suggested_fix.fixed_code,
                diff=c.suggested_fix.diff,
            )

        comments.append(ReviewCommentResponse(
            id=c.id,
            filePath=c.file_path,
            line=c.line,
            endLine=c.end_line,
            side=c.side,
            type=c.review_type.value,
            severity=c.severity.value,
            title=c.title,
            body=c.body,
            suggestedFix=suggested_fix,
            dismissed=c.dismissed,
            copied=c.copied,
        ))

    return ReviewResponse(
        id=review.id,
        prNumber=review.pr_number,
        repoOwner=review.repo_owner,
        repoName=review.repo_name,
        headSha=review.head_sha,
        baseSha=review.base_sha,
        timestamp=review.timestamp,
        status=review.status.value,
        summary=summary,
        comments=comments,
        errorMessage=review.error_message,
    )


def _rule_to_response(rule) -> CustomRuleResponse:
    """Convert CustomRule to response model."""
    return CustomRuleResponse(
        id=rule.id,
        name=rule.name,
        description=rule.description,
        severity=rule.severity.value,
        enabled=rule.enabled,
        pattern=rule.pattern,
    )
