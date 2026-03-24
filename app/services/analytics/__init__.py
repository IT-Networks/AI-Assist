"""
Analytics Services - Analyse und Reporting.

Dieses Paket gruppiert Services für Analyse:
- AnalyticsLogger (anonymisiertes Tracking)
- PerformanceTracker
- PatternDetector / PatternLearner
- ReportGenerator
- TokenTracker

Verwendung:
    from app.services.analytics import get_analytics_logger

    logger = get_analytics_logger()
    logger.log_tool_usage("search_code", duration_ms=150)
"""

from app.services.analytics_logger import (
    AnalyticsLogger,
    get_analytics_logger,
)

from app.services.performance_tracker import (
    PerformanceTracker,
)

from app.services.pattern_detector import (
    PatternDetector,
)

from app.services.pattern_learner import (
    PatternLearner,
    get_pattern_learner,
)

from app.services.report_generator import (
    ReportGenerator,
)

from app.services.token_tracker import (
    TokenTracker,
    get_token_tracker,
)

__all__ = [
    # Analytics
    "AnalyticsLogger",
    "get_analytics_logger",
    # Performance
    "PerformanceTracker",
    # Patterns
    "PatternDetector",
    "PatternLearner",
    "get_pattern_learner",
    # Reports
    "ReportGenerator",
    # Tokens
    "TokenTracker",
    "get_token_tracker",
]
