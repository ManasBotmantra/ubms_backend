import sys
from loguru import logger

# Remove default logger
logger.remove()

# Add standard out logger with custom format
logger.add(
    sys.stdout,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
    level="INFO",
    colorize=True,
)

# You can also add file logging if necessary
# logger.add("logs/app.log", rotation="500 MB", level="INFO")
