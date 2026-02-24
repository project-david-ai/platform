import json
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from projectdavid_common import ValidationInterface
from projectdavid_common.utilities.logging_service import LoggingUtility
from redis import Redis

from entities_api.orchestration.engine.inference_arbiter import \
    InferenceArbiter
from entities_api.orchestration.engine.inference_provider_selector import \
    InferenceProviderSelector
from src.api.entities_api.dependencies import get_redis

router = APIRouter()
logging_utility = LoggingUtility()


@router.post(
    "/completions",
    summary="Asynchronous completions streaming endpoint (Unified Orchestration)",
    response_description="A stream of JSON-formatted completions chunks",
)
async def completions(
    stream_request: ValidationInterface.StreamRequest, redis: Redis = Depends(get_redis)
):
    """
    Handles streaming completion requests using the appropriate inference provider.
    NOW FULLY ASYNC-NATIVE (No Threading Wrappers).
    """

    logging_utility.info(
        "Completions endpoint called for model: %s, run: %s",
        stream_request.model,
        stream_request.run_id,
    )

    try:
        arbiter = InferenceArbiter(redis=redis)
        selector = InferenceProviderSelector(arbiter)
        # Assuming select_provider returns the initialized Async Worker
        general_handler_instance, api_model_name = selector.select_provider(
            model_id=stream_request.model
        )
    except Exception as e:
        logging_utility.error(f"Provider setup failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

    async def stream_generator():
        start_time = time.time()
        run_id = stream_request.run_id
        prefix = "data: "
        suffix = "\n\n"

        chunk_count = 0
        error_occurred = False

        try:
            # -------------------------------------------------------
            # DIRECT ASYNC ITERATION
            # Removed: run_sync_generator_in_thread(...)
            # -------------------------------------------------------
            async for chunk in general_handler_instance.process_conversation(
                thread_id=stream_request.thread_id,
                message_id=stream_request.message_id,
                run_id=run_id,
                assistant_id=stream_request.assistant_id,
                model=stream_request.model,
                stream_reasoning=False,
                api_key=stream_request.api_key,
            ):
                chunk_count += 1

                # --- FORMATTING LOGIC ---
                final_str = ""

                if isinstance(chunk, str):
                    # Fast path: It's already a JSON string from the worker
                    final_str = chunk
                elif isinstance(chunk, dict):
                    # Ensure run_id exists
                    if "run_id" not in chunk:
                        chunk["run_id"] = run_id
                    final_str = json.dumps(chunk)
                else:
                    # Fallback
                    final_str = json.dumps(
                        {"type": "content", "content": str(chunk), "run_id": run_id}
                    )

                # Yield SSE format
                yield f"{prefix}{final_str}{suffix}"

            # End of Stream
            if not error_occurred:
                yield "data: [DONE]\n\n"

        except Exception as e:
            error_occurred = True
            logging_utility.error(f"Stream loop error: {e}", exc_info=True)
            yield f"data: {json.dumps({'type': 'error', 'run_id': run_id, 'message': str(e)})}\n\n"
        finally:
            elapsed = time.time() - start_time
            logging_utility.info(
                f"Stream finished: {chunk_count} chunks in {elapsed:.2f}s"
            )

    return StreamingResponse(
        stream_generator(),
        media_type="text/event-stream",
        headers={
            "X-Accel-Buffering": "no",  # Critical for Nginx/Proxies
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Content-Type": "text/event-stream",
            # "Transfer-Encoding": "chunked", # FastAPI handles this automatically
        },
    )
