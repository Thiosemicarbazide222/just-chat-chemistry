from datetime import datetime, timezone
import logging
import os
from typing import Any, Dict, Iterable, Optional

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from pymongo import MongoClient, ReturnDocument
from pymongo.errors import PyMongoError

logger = logging.getLogger("search_logger")


class SearchEvent(BaseModel):
    user_id: Optional[str] = Field(default=None, description="Stable app/user ID if available")
    email: Optional[str] = Field(default=None, description="User email if available")
    name: Optional[str] = Field(default=None, description="Display name if available")
    query: str = Field(..., description="The search or message content")
    metadata: Optional[Dict[str, Any]] = Field(default=None, description="Optional extra data from UI")
    timestamp: Optional[datetime] = Field(default=None, description="Client-provided timestamp; server will set if missing")


FORWARD_BASE_URL = os.getenv("FORWARD_BASE_URL", "http://just-chat-agents:8091")
FORWARD_TIMEOUT_SECONDS = float(os.getenv("FORWARD_TIMEOUT_SECONDS", "60"))


def get_mongo_client() -> MongoClient:
    mongo_uri = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
    return MongoClient(mongo_uri, uuidRepresentation="standard")


def get_db_name() -> str:
    return os.getenv("MONGODB_DB", "just_chat")


def _normalize_timestamp(event: SearchEvent) -> datetime:
    return event.timestamp or datetime.now(timezone.utc)


def _extract_user_text(content: Any) -> Optional[str]:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_bits = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text" and "text" in item:
                    text_bits.append(str(item["text"]))
                elif "text" in item:
                    text_bits.append(str(item["text"]))
        return " ".join(bit.strip() for bit in text_bits if bit)
    return None


def _build_event_from_payload(payload: Dict[str, Any]) -> Optional[SearchEvent]:
    messages = payload.get("messages") or []
    if not isinstance(messages, Iterable):
        return None

    user_message = None
    for message in reversed(list(messages)):
        if isinstance(message, dict) and message.get("role") == "user":
            user_message = message
            break

    if not user_message:
        return None

    content = _extract_user_text(user_message.get("content"))
    if not content:
        return None

    request_metadata = {}
    metadata_field = payload.get("metadata")
    if isinstance(metadata_field, dict):
        request_metadata = metadata_field

    event_metadata: Dict[str, Any] = {
        "model": payload.get("model"),
        "messages_count": len(messages),
        "stream": bool(payload.get("stream")),
        "conversation_id": payload.get("conversation_id") or request_metadata.get("conversation_id"),
    }
    if request_metadata:
        event_metadata["request_metadata"] = request_metadata

    user_id = payload.get("user") or request_metadata.get("user_id")
    email = request_metadata.get("email")
    name = request_metadata.get("name")

    return SearchEvent(
        user_id=user_id,
        email=email,
        name=name,
        query=content,
        metadata=event_metadata,
    )


def store_search_event(event: SearchEvent) -> Dict[str, Any]:
    client = get_mongo_client()
    db = client[get_db_name()]
    users = db["users"]
    searches = db["searches"]

    event_ts = _normalize_timestamp(event)

    user_filter: Dict[str, Any] = {}
    if event.email:
        user_filter["email"] = event.email.strip().lower()
    elif event.user_id:
        user_filter["external_user_id"] = event.user_id

    try:
        user_doc: Dict[str, Any]
        if user_filter:
            update_doc = {
                "$setOnInsert": {
                    "created_at": event_ts,
                },
                "$set": {
                    "updated_at": event_ts,
                },
            }

            if event.name:
                update_doc["$set"]["name"] = event.name
            if event.email:
                update_doc["$set"]["email"] = event.email.strip().lower()
            if event.user_id:
                update_doc["$set"]["external_user_id"] = event.user_id

            user_doc = users.find_one_and_update(
                filter=user_filter,
                update=update_doc,
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
        else:
            user_doc = {
                "name": event.name or "Anonymous",
                "created_at": event_ts,
                "updated_at": event_ts,
            }
            insert_res = users.insert_one(user_doc)
            user_doc["_id"] = insert_res.inserted_id

        search_doc = {
            "user_id": user_doc["_id"],
            "query": event.query,
            "metadata": event.metadata or {},
            "created_at": event_ts,
        }
        ins = searches.insert_one(search_doc)
        return {"ok": True, "search_id": str(ins.inserted_id), "user_id": str(user_doc["_id"])}
    except PyMongoError as exc:
        raise HTTPException(status_code=500, detail=f"Mongo error: {exc}") from exc


def _filter_headers(headers: Iterable) -> Dict[str, str]:
    hop_by_hop = {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "content-length",
    }
    filtered: Dict[str, str] = {}
    for key, value in headers:
        if key.lower() not in hop_by_hop:
            filtered[key] = value
    return filtered


def _forward_headers(request_headers: Iterable) -> Dict[str, str]:
    excluded = {"host", "content-length"}
    outgoing: Dict[str, str] = {}
    for key, value in request_headers:
        if key.lower() not in excluded:
            outgoing[key] = value
    return outgoing


def _timeout() -> httpx.Timeout:
    return httpx.Timeout(FORWARD_TIMEOUT_SECONDS, connect=FORWARD_TIMEOUT_SECONDS, read=None)


app = FastAPI(title="Search Logger Service", version="1.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ALLOW_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.post("/log-search")
def log_search(event: SearchEvent) -> Dict[str, Any]:
    return store_search_event(event)


async def _forward_json(path: str, payload: Dict[str, Any], request: Request) -> Response:
    headers = _forward_headers(request.headers.items())
    timeout = _timeout()
    stream = bool(payload.get("stream")) or request.headers.get("accept", "").startswith("text/event-stream")
    params = request.query_params

    async with httpx.AsyncClient(base_url=FORWARD_BASE_URL, timeout=timeout) as client:
        if stream:
            request_obj = client.build_request("POST", path, json=payload, headers=headers, params=params)
            upstream = await client.send(request_obj, stream=True)

            async def iterator():
                try:
                    async for chunk in upstream.aiter_raw():
                        yield chunk
                finally:
                    await upstream.aclose()

            response_headers = _filter_headers(upstream.headers.items())
            media_type = upstream.headers.get("content-type")
            return StreamingResponse(iterator(), status_code=upstream.status_code, headers=response_headers, media_type=media_type)

        upstream = await client.post(path, json=payload, headers=headers, params=params)
        response_headers = _filter_headers(upstream.headers.items())
        media_type = upstream.headers.get("content-type")
        return Response(content=upstream.content, status_code=upstream.status_code, headers=response_headers, media_type=media_type)


async def _forward_raw(path: str, method: str, body: bytes, request: Request) -> Response:
    headers = _forward_headers(request.headers.items())
    timeout = _timeout()
    params = request.query_params

    async with httpx.AsyncClient(base_url=FORWARD_BASE_URL, timeout=timeout) as client:
        upstream = await client.request(method, path, headers=headers, content=body, params=params)
        response_headers = _filter_headers(upstream.headers.items())
        media_type = upstream.headers.get("content-type")
        return Response(content=upstream.content, status_code=upstream.status_code, headers=response_headers, media_type=media_type)


@app.post("/v1/chat/completions")
async def proxy_chat_completions(request: Request) -> Response:
    payload: Dict[str, Any] = await request.json()

    event = _build_event_from_payload(payload)
    if event:
        try:
            store_search_event(event)
        except HTTPException:
            raise
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception("Unable to persist search event: %s", exc)

    return await _forward_json("/v1/chat/completions", payload, request)


@app.api_route("/v1/{full_path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
async def proxy_openai(full_path: str, request: Request) -> Response:
    method = request.method
    if method == "POST":
        body = await request.body()
    else:
        body = await request.body()
    return await _forward_raw(f"/v1/{full_path}", method, body, request)


@app.exception_handler(httpx.RequestError)
async def upstream_unavailable(_: Request, exc: httpx.RequestError) -> JSONResponse:
    logger.error("Error forwarding request to upstream: %s", exc)
    return JSONResponse(status_code=502, content={"detail": "Upstream agent service is unavailable"})


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("APP_HOST", "0.0.0.0")
    port = int(os.getenv("APP_PORT", "8099"))
    uvicorn.run("scripts.search_logger:app", host=host, port=port, reload=False)
