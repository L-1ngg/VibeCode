"""Shared utilities for WebSearch."""

from .env_parser import load_env_file
from .logger import logger

__all__ = ["logger", "load_env_file"]
