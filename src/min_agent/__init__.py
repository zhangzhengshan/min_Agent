"""min_agent：从零实现的最小 Agent 运行时（P1 包含数据契约、Session 与 Context）。"""

from .context import build_context, tool_result_content, turn_to_messages
from .errors import (
    AgentError,
    ConfigError,
    DuplicateToolError,
    ErrorCode,
    ErrorInfo,
    ModelError,
    ParseError,
    SessionNotFoundError,
    SummarizationError,
    ToolArgumentError,
    ToolExecutionError,
    ToolLimitError,
    ToolNotFoundError,
)
from .models import (
    DecisionKind,
    Message,
    ModelDecision,
    Role,
    ToolCall,
    ToolExchange,
    ToolResult,
    Turn,
    TurnStatus,
)
from .session import (
    DEFAULT_MAX_TURNS,
    DEFAULT_RECENT_TURNS,
    DEFAULT_SYSTEM_PROMPT,
    Session,
    SessionManager,
    Summarizer,
)

__version__ = "0.1.0"

__all__ = [
    "AgentError",
    "ConfigError",
    "DEFAULT_MAX_TURNS",
    "DEFAULT_RECENT_TURNS",
    "DEFAULT_SYSTEM_PROMPT",
    "DecisionKind",
    "DuplicateToolError",
    "ErrorCode",
    "ErrorInfo",
    "Message",
    "ModelDecision",
    "ModelError",
    "ParseError",
    "Role",
    "Session",
    "SessionManager",
    "SessionNotFoundError",
    "SummarizationError",
    "Summarizer",
    "ToolArgumentError",
    "ToolCall",
    "ToolExchange",
    "ToolExecutionError",
    "ToolLimitError",
    "ToolNotFoundError",
    "ToolResult",
    "Turn",
    "TurnStatus",
    "build_context",
    "tool_result_content",
    "turn_to_messages",
]
