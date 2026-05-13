"""
Logging utility for 4S1T Agent AI system.
Provides consistent logging across all components.
"""
import logging
import sys
from typing import Optional

from config.settings import settings


def setup_logger(name: str, level: Optional[int] = None) -> logging.Logger:
    """
    Set up and return a logger with consistent formatting.
    
    Args:
        name: Name of the logger
        level: Logging level (defaults to DEBUG if DEBUG setting is True, otherwise INFO)
        
    Returns:
        Configured logger instance
    """
    # Create logger
    logger = logging.getLogger(name)
    
    # Set level
    if level is None:
        level = logging.DEBUG if settings.DEBUG else logging.INFO
    logger.setLevel(level)
    
    # Prevent adding multiple handlers if logger already exists
    if logger.handlers:
        return logger
    
    # Create formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # Create console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    # Prevent propagation to root logger
    logger.propagate = False
    
    return logger


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger instance.
    
    Args:
        name: Name of the logger
        
    Returns:
        Logger instance
    """
    return setup_logger(name)
