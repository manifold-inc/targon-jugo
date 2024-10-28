from cachetools import TTLCache
from fastapi import FastAPI, HTTPException, Request
from nanoid import generate
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import time
from dotenv import load_dotenv
import os
import requests

from pymysql.cursors import DictCursor
from epistula import verify_signature
import pymysql
import json
import traceback


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


def is_authorized_hotkey(cursor, signed_by: str) -> bool:
    cursor.execute("SELECT 1 FROM validator WHERE hotkey = %s", (signed_by,))
    return cursor.fetchone() is not None


class CurrentBucket(BaseModel):
    id: Optional[str] = None
    last_id: Optional[str] = None


# Global variables for bucket management
current_bucket = CurrentBucket()

# Cache: Store the data for 20 minutes (1200 seconds)
cache = TTLCache(maxsize=1, ttl=1200)

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

endonURL = os.getenv("ENDON_URL")

# Ingestion endpoint
@app.post("/")
async def ingest(request: Request):
    now = round(time.time() * 1000)
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
        print(err)
        raise HTTPException(status_code=400, detail=str(err))

    cursor = targon_stats_db.cursor()
    try:
        payload = IngestPayload(**json_data)
        # Check if the sender is an authorized hotkey
        if not signed_by or not is_authorized_hotkey(cursor, signed_by):
            raise HTTPException(
                status_code=401, detail=f"Unauthorized hotkey: {signed_by}"
            )
        for md in payload.responses:
            print(md.stats.cause)
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

        # Send error to Endon
        sendErrorToEndon(e, error_traceback, "ingest")
        print(f"Error occurred: {str(e)}\n{error_traceback}")
        raise HTTPException(
            status_code=500,
            detail=f"Internal Server Error: Could not insert responses/requests. {str(e)}",
        )
    finally:
        cursor.close()


# Exegestor endpoint
@app.get("/")
async def exgest(request: Request):
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
            print(err)
            raise HTTPException(status_code=400, detail=str(err))

    # Get the cached bucket (or fetch new records if cache is expired)

    # If cache is empty or expired, fetch new records and update the cache
    if "current_bucket" not in cache:
        # Update the bucket id
        alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
        current_bucket.id = "b_" + generate(alphabet=alphabet, size=14)

        cursor = targon_hub_db.cursor(DictCursor)
        try:
            # Fetch the latest 50 records after the last processed ID
            cursor.execute(
                """
                SELECT id, request, response, uid, hotkey, endpoint, success
                FROM request
                WHERE id > %s AND scored = false
                ORDER BY id DESC
                LIMIT 50
            """,
                (current_bucket.last_id or 0,),
            )

            records = cursor.fetchall()

            # Convert records to ResponseRecord objects
            response_records = []
            for record in records:
                # Parse the JSON string into a Python list
                record["response"] = json.loads(record["response"])
                record["request"] = json.loads(record["request"])
                response_records.append(record)

            # Update the last processed ID if there are new records
            if response_records and len(response_records):
                current_bucket.last_id = response_records[-1]["id"]

            # Update cache with new bucket id and records (even if empty)
            cache["current_bucket"] = {
                "bucket_id": current_bucket.id,
                "records": response_records,
            }

        except json.JSONDecodeError as e:
            error_traceback = traceback.format_exc()
            # Send error to Endon
            sendErrorToEndon(e, error_traceback, "exgest")
            print(f"Error decoding JSON: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail=f"Internal Server Error: Invalid JSON in response. {str(e)}",
            )
        except Exception as e:
            error_traceback = traceback.format_exc()
            # Send error to Endon
            sendErrorToEndon(e, error_traceback, "exgest")
            print(f"Error occurred: {str(e)}\n{error_traceback}")
            raise HTTPException(
                status_code=500,
                detail=f"Internal Server Error: Could not fetch responses. {str(e)}",
            )
        finally:
            cursor.close()

    # Return cached data (which may be an empty list of records)
    cached_bucket = cache["current_bucket"]

    # Return the cached records and bucket id (records may be an empty list)
    return {
        "bucket_id": cached_bucket["bucket_id"],
        "records": cached_bucket["records"],
    }


@app.get("/ping")
def ping():
    return "pong", 200

def sendErrorToEndon(error: Exception, error_traceback: str, endpoint: str) -> None:
        try:
            error_payload = {
                "error": str(error),
                "traceback": error_traceback,
                "endpoint": endpoint,
                "timestamp": time.time()
            }

            requests.post(
                (str(endonURL) + "/report"),
                json=error_payload
            )

            print(f"Error report sent to Endon: {str(error)}")
        except Exception as e:
            print(f"Failed to report error to Endon: {str(e)}")
