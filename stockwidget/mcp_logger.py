"""MCP connection status logging helpers."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path


MCP_LOG_PATH = Path("logs/mcp_connection.log")


def log_mcp_status(level: str, message: str) -> None:
    """Append MCP connection status to local log file."""
    try:
        MCP_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with MCP_LOG_PATH.open("a", encoding="utf-8") as fp:
            fp.write(f"{timestamp} {level.upper()} {message}\n")
    except Exception:
        # Logging failure must not affect MCP notification flow.
        pass


def format_mcp_status(message: str) -> str:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return f"[{timestamp}] {message}"
