class ChatbotException(Exception):
    """Base exception for the Chatbot Backend."""
    def __init__(self, message: str, status_code: int = 500):
        self.message = message
        self.status_code = status_code
        super().__init__(self.message)

class DatasetUploadError(ChatbotException):
    def __init__(self, message: str):
        super().__init__(message, status_code=400)

class DatasetNotFoundError(ChatbotException):
    def __init__(self, message: str):
        super().__init__(message, status_code=404)

class InvalidSQLQueryError(ChatbotException):
    def __init__(self, message: str):
        super().__init__(message, status_code=400)

class SQLExecutionError(ChatbotException):
    def __init__(self, message: str):
        super().__init__(message, status_code=500)

class LLMGenerationError(ChatbotException):
    def __init__(self, message: str):
        super().__init__(message, status_code=502)

class APIFetchError(ChatbotException):
    """Raised when the transaction log API call fails (auth, network, or API-level error)."""
    def __init__(self, message: str):
        super().__init__(message, status_code=502)

class SQLGenerationRefusedError(ChatbotException):
    """Raised when the SQL generator LLM declines to produce SQL (e.g. the required table is
    empty) and replies with a natural-language explanation instead."""
    def __init__(self, message: str):
        super().__init__(message, status_code=422)

