class AIAssistError(Exception):
    """Base exception for AI Assist application."""
    pass


class LLMError(AIAssistError):
    """Raised when LLM request fails."""
    pass


class JavaReaderError(AIAssistError):
    """Raised when Java repository reading fails."""
    pass


class PathTraversalError(AIAssistError):
    """Raised on suspected path traversal attack."""
    pass


class PDFReadError(AIAssistError):
    """Raised when PDF extraction fails."""
    pass


class ConfluenceError(AIAssistError):
    """Raised when Confluence API call fails."""
    pass


class LogParserError(AIAssistError):
    """Raised when log parsing fails."""
    pass
