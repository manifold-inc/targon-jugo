from cachetools import TTLCache
from fastapi import FastAPI, HTTPException, Request
from nanoid import generate
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import time
from dotenv import load_dotenv
import os

from pymysql.cursors import DictCursor
from epistula import verify_signature
import pymysql
import json
import traceback
from asyncio import Lock


pymysql.install_as_MySQLdb()
load_dotenv()

DEBUG = not not os.getenv("DEBUG")
config = {}
if not DEBUG:
    config = {"docs_url": None, "redoc_url": None}
app = FastAPI(**config)  # type: ignore


class Stats(BaseModel):
    time_to_first_token: float
    time_for_all_tokens: float
    total_time: float
    tps: float
    tokens: List[Dict[str, Any]]
    verified: bool
    error: Optional[str] = None
    cause: Optional[str] = None
    organic: Optional[bool] = None


# Define the MinerResponse model
class MinerResponse(BaseModel):
    r_nanoid: str
    hotkey: str
    coldkey: str
    uid: int
    stats: Stats


class LLMRequest(BaseModel):
    messages: Optional[List[Dict[str, Any]]] = None
    prompt: Optional[str] = None
    model: str
    seed: Optional[int]
    max_tokens: Optional[int]
    temperature: Optional[float]


# Define the ValidatorRequest model
class ValidatorRequest(BaseModel):
    request_endpoint: str
    r_nanoid: str
    block: int
    request: LLMRequest
    version: int
    hotkey: str


class IngestPayload(BaseModel):
    responses: List[MinerResponse]
    request: ValidatorRequest
    models: List[str]


class CurrentBucket(BaseModel):
    id: Optional[str] = None
    model_last_ids: Dict[str, int] = {}


def is_authorized_hotkey(cursor, signed_by: str) -> bool:
    cursor.execute("SELECT 1 FROM validator WHERE hotkey = %s", (signed_by,))
    return cursor.fetchone() is not None


# Global variables for bucket management
current_bucket = CurrentBucket()

# Cache: Store the data for 20 minutes (1200 seconds)
cache = TTLCache(maxsize=2, ttl=1200)

targon_hub_db = pymysql.connect(
    host=os.getenv("HUB_DATABASE_HOST"),
    user=os.getenv("HUB_DATABASE_USERNAME"),
    passwd=os.getenv("HUB_DATABASE_PASSWORD"),
    db=os.getenv("HUB_DATABASE"),
    autocommit=True,
    ssl={"ssl_ca": "/etc/ssl/certs/ca-certificates.crt"},
)

targon_stats_db = pymysql.connect(
    host=os.getenv("STATS_DATABASE_HOST"),
    user=os.getenv("STATS_DATABASE_USERNAME"),
    passwd=os.getenv("STATS_DATABASE_PASSWORD"),
    db=os.getenv("STATS_DATABASE"),
    autocommit=True,
    ssl={"ssl_ca": "/etc/ssl/certs/ca-certificates.crt"},
)

# Create a single lock instance - this is shared across all requests
cache_lock = Lock()  # Initialize the mutex lock

# Ingestion endpoint
@app.post("/")
async def ingest(request: Request):
    now = round(time.time() * 1000)
    request_id = generate(size=6)  # Unique ID for tracking request flow
    body = await request.body()
    json_data = await request.json()

    # Extract signature information from headers
    timestamp = request.headers.get("Epistula-Timestamp")
    uuid = request.headers.get("Epistula-Uuid")
    signed_by = request.headers.get("Epistula-Signed-By")
    signature = request.headers.get("Epistula-Request-Signature")

    # Verify the signature using the new epistula protocol
    err = verify_signature(
        signature=signature,
        body=body,
        timestamp=timestamp,
        uuid=uuid,
        signed_by=signed_by,
        now=now,
    )

    if err:
        log_error(
            request_id,
            Exception(err),
            "Signature verification failed",
            "ingest",
        )
        raise HTTPException(status_code=400, detail=str(err))

    cursor = targon_stats_db.cursor()
    try:
        payload = IngestPayload(**json_data)
        # Check if the sender is an authorized hotkey
        if not signed_by or not is_authorized_hotkey(cursor, signed_by):
            log_error(
                request_id,
                Exception("Unauthorized hotkey"),
                f"Unauthorized hotkey: {signed_by}",
                "ingest",
            )
            raise HTTPException(
                status_code=401, detail=f"Unauthorized hotkey: {signed_by}"
            )
        cursor.executemany(
            """
            INSERT INTO miner_response (r_nanoid, hotkey, coldkey, uid, verified, time_to_first_token, time_for_all_tokens, total_time, tokens, tps, error, cause, organic) 
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            [
                (
                    md.r_nanoid,
                    md.hotkey,
                    md.coldkey,
                    md.uid,
                    md.stats.verified,
                    md.stats.time_to_first_token,
                    md.stats.time_for_all_tokens,
                    md.stats.total_time,
                    json.dumps(md.stats.tokens),
                    md.stats.tps,
                    md.stats.error,
                    md.stats.cause,
                    md.stats.organic or 0,
                )
                for md in payload.responses
            ],
        )

        # Insert validator request
        cursor.execute(
            """
            INSERT INTO validator_request (r_nanoid, block, messages, request_endpoint, version, hotkey, model, seed, max_tokens, temperature) 
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                payload.request.r_nanoid,
                payload.request.block,
                json.dumps(
                    payload.request.request.messages or payload.request.request.prompt
                ),
                payload.request.request_endpoint.split(".")[1],
                payload.request.version,
                payload.request.hotkey,
                payload.request.request.model,
                payload.request.request.seed,
                payload.request.request.max_tokens,
                payload.request.request.temperature,
            ),
        )

        # Update models in validator table if changed
        models = json.dumps(payload.models)
        cursor.execute(
            """
            INSERT INTO validator (hotkey, models)
            VALUES (%s, %s)
            ON DUPLICATE KEY UPDATE
                models = IF(
                    JSON_CONTAINS(models, %s) AND JSON_CONTAINS(%s, models),
                    models,
                    CAST(%s AS JSON)
                )
            """,
            (
                payload.request.hotkey,
                models,
                models,
                models,
                models,
            ),
        )

        targon_stats_db.commit()
        return "", 200

    except Exception as e:
        targon_stats_db.rollback()
        error_traceback = traceback.format_exc()
        log_error(request_id, e, error_traceback, "ingest")
        raise HTTPException(
            status_code=500,
            detail=f"[{request_id}] Internal Server Error: Could not insert responses/requests. {str(e)}",
        )
    finally:
        cursor.close()


# Exegestor endpoint
@app.post("/organics")
async def exgest(request: Request):
    request_id = generate(size=6)
    try:
        json_data = await request.json()
        now = round(time.time() * 1000)
        body = await request.body()

        # Extract signature information from headers
        timestamp = request.headers.get("Epistula-Timestamp")
        uuid = request.headers.get("Epistula-Uuid")
        signed_by = request.headers.get("Epistula-Signed-By")
        signature = request.headers.get("Epistula-Request-Signature")

        # Verify the signature using the new epistula protocol
        if not DEBUG:
            err = verify_signature(
                signature=signature,
                body=body,
                timestamp=timestamp,
                uuid=uuid,
                signed_by=signed_by,
                now=now,
            )

            if err:
                log_error(
                    request_id,
                    Exception(err),
                    "Signature verification failed",
                    "exgest",
                )
                raise HTTPException(status_code=400, detail=str(err))

        async with cache_lock:  # Acquire the lock - other threads must wait here
            cached_buckets = cache.get("buckets")
            bucket_id = cache.get("bucket_id")

            if cached_buckets is None or bucket_id is None:
                model_buckets = {}
                cursor = targon_hub_db.cursor(DictCursor)
                alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
                bucket_id = "b_" + generate(alphabet=alphabet, size=14)
                try:
                    for model in json_data:
                        # Generate bucket ID for this model

                        cursor.execute(
                            """
                            SELECT id, request, response, uid, hotkey, endpoint, success, total_time
                            FROM request
                            WHERE id > %s 
                            AND scored = false 
                            AND model_name = %s
                            ORDER BY id DESC
                            LIMIT 50
                            """,
                            (current_bucket.model_last_ids.get(model, 0), model),
                        )

                        records = cursor.fetchall()

                        # If we have records, mark them as scored
                        if records:
                            record_ids = [record["id"] for record in records]
                            placeholders = ", ".join(["%s"] * len(record_ids))
                            cursor.execute(
                                f"""
                                UPDATE request 
                                SET scored = true 
                                WHERE id IN ({placeholders})
                                """,
                                record_ids,
                            )

                        # Convert records to ResponseRecord objects
                        response_records = []
                        for record in records:
                            record["response"] = json.loads(record["response"])
                            record["request"] = json.loads(record["request"])

                            response_records.append(record)

                        # Update the last processed ID for this specific model
                        if response_records and len(response_records):
                            current_bucket.model_last_ids[model] = response_records[-1][
                                "id"
                            ]

                        model_buckets[model] = response_records

                    # Safely update cache - no other thread can interfere
                    cache["buckets"] = model_buckets
                    cache["bucket_id"] = bucket_id
                    cached_buckets = model_buckets
                except Exception as e:
                    error_traceback = traceback.format_exc()
                    log_error(request_id, e, error_traceback, "exgest")
                    raise HTTPException(
                        status_code=500,
                        detail=f"Internal Server Error: Could not fetch responses. {str(e)}",
                    )
                finally:
                    cursor.close()

        return {
            "bucket_id": bucket_id,
            "organics": {
                model: cached_buckets[model]
                for model in json_data
                if model in cached_buckets
            },
        }
    except json.JSONDecodeError as e:
        log_error(request_id, e, traceback.format_exc(), "exgest")
        raise HTTPException(
            status_code=400,
            detail=f"Invalid JSON in request body: {str(e)}"
        )


@app.get("/ping")
def ping():
    return "pong", 200


def log_error(
    request_id: str, error: Exception, error_traceback: str, endpoint: str
) -> None:
    error_payload = {
        "level": "error",
        "service": "targon-jugo",
        "endpoint": endpoint,
        "request_id": request_id,
        "error": str(error),
        "traceback": str(error_traceback),
        "type": "error_log"
    }
    print(json.dumps(error_payload, ensure_ascii=False))
