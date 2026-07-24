import azure.functions as func
import json
import asyncio
import logging
from src.api.dependencies import get_response_builder, get_dataset_service
from src.core.exceptions import DatasetNotFoundError
from src.core.logger import logger
from src.domain.models import ChatMessage

def main(req: func.HttpRequest) -> func.HttpResponse:
    if req.method == "OPTIONS":
        return func.HttpResponse(
            "",
            status_code=204,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, authorize_token",
            }
        )

    logger.info("Python HTTP trigger function processed a chat request.")

    authorize_token = req.headers.get("authorize_token")
    if not authorize_token:
        return func.HttpResponse(
            "Missing 'authorize_token' in request headers",
            status_code=401,
            headers={"Access-Control-Allow-Origin": "*"}
        )

    try:
        req_body = req.get_json()
    except ValueError:
        return func.HttpResponse(
            "Invalid JSON format in request body",
            status_code=400,
            headers={"Access-Control-Allow-Origin": "*"}
        )
    logging.error(f"Request body: {req_body}")
    user_message = req_body.get('message')
    dataset_id = req_body.get('dataset_id')
    history_raw = req_body.get('history', [])

    if not user_message:
        return func.HttpResponse(
            "Missing 'message' in request body",
            status_code=400,
            headers={"Access-Control-Allow-Origin": "*"}
        )

    chat_history = [
        ChatMessage(role=msg['role'], content=msg['content'])
        for msg in history_raw
    ]

    # Use asyncio.run since Azure Functions v1 python handlers are synchronous by default unless declared async
    # But wait, Azure Functions V1 Python natively supports `async def main(...)`
    # Let's use async def main to be native.

    return asyncio.run(process_chat_request(user_message, dataset_id, chat_history, authorize_token))


async def process_chat_request(user_message, dataset_id, chat_history, authorize_token):
    try:
        dataset_service = get_dataset_service()
        response_builder = get_response_builder()

        metadata = None
        if dataset_id:
            try:
                metadata = await dataset_service.get_metadata(dataset_id)
            except DatasetNotFoundError:
                logger.warning(f"dataset_id={dataset_id} not found on disk — will re-fetch if needed")

        blocks, resolved_dataset_id = await response_builder.build_response(
            user_message=user_message,
            dataset_id=dataset_id,
            metadata=metadata,
            chat_history=chat_history,
            authorize_token=authorize_token,
        )

        response_payload = {
            "dataset_id": resolved_dataset_id,
            "blocks": [b.model_dump() for b in blocks]
        }

        return func.HttpResponse(
            json.dumps(response_payload),
            mimetype="application/json",
            status_code=200,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type",
            }
        )

    except Exception as e:
        logger.error(f"Error processing chat: {str(e)}")
        return func.HttpResponse(
            f"Internal server error: {str(e)}",
            status_code=500,
            headers={"Access-Control-Allow-Origin": "*"}
        )
