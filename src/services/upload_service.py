import os
import uuid
import shutil
from pathlib import Path
from fastapi import UploadFile

from src.core.config import settings
from src.core.logger import logger
from src.core.exceptions import DatasetUploadError

class UploadService:
    def __init__(self):
        self.upload_dir = Path(settings.UPLOAD_DIR)
        self.upload_dir.mkdir(parents=True, exist_ok=True)

    async def save_upload_file(self, file: UploadFile) -> tuple[str, str]:
        """
        Saves an uploaded file to the local storage and returns dataset_id and file_path.
        """
        if not file.filename:
            raise DatasetUploadError("No file provided.")
            
        # Basic validation of extensions
        allowed_extensions = {".csv", ".xlsx", ".json"}
        ext = Path(file.filename).suffix.lower()
        if ext not in allowed_extensions:
            raise DatasetUploadError(f"File extension {ext} not allowed. Supported: {allowed_extensions}")

        dataset_id = str(uuid.uuid4())
        safe_filename = f"{dataset_id}{ext}"
        file_path = self.upload_dir / safe_filename
        
        logger.info(f"Saving uploaded file to {file_path}")
        
        try:
            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
        except Exception as e:
            logger.error(f"Error saving file: {str(e)}")
            raise DatasetUploadError(f"Could not save file: {str(e)}")
            
        return dataset_id, str(file_path)
