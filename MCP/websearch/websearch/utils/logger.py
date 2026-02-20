"""Logger accessor for shared use across modules."""

import logging

logger = logging.getLogger(__name__)

__all__ = ["logger"]
