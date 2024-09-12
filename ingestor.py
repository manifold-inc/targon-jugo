from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import time
from dotenv import load_dotenv
import os
from epistula import verify_signature
import pymysql
import json
import traceback


pymysql.install_as_MySQLdb()
app = FastAPI()
load_dotenv()


class Stats(BaseModel):
    verified: bool
    time_to_first_token: float
    time_for_all_tokens: float
    total_time: float
    response: Optional[str] = "No repsonse"
    tps: float

# Define the MinerResponse model
class MinerResponse(BaseModel):
    r_nanoid: str
    hotkey: str
    coldkey: str
    uid: int
    stats: Stats  


# Define the ValidatorRequest model
class ValidatorRequest(BaseModel):
    r_nanoid: str
    block: int
    request: Dict[str, Any]
    request_endpoint: str
    version: int
    hotkey: str


class IngestPayload(BaseModel):
    responses: List[MinerResponse]
    request: ValidatorRequest


def is_authorized_hotkey(cursor, signed_by: str) -> bool:
    cursor.execute("SELECT 1 FROM validator WHERE hotkey = %s", (signed_by,))
    return cursor.fetchone() is not None


# Ingestion endpoint
@app.post("/ingest")
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

    connection = pymysql.connect(
        host=os.getenv("DATABASE_HOST"),
        user=os.getenv("DATABASE_USERNAME"),
        passwd=os.getenv("DATABASE_PASSWORD"),
        db=os.getenv("DATABASE"),
        autocommit=True,
        ssl={"ssl_ca": "/etc/ssl/certs/ca-certificates.crt"},
    )

    cursor = connection.cursor()
    try:
        payload = IngestPayload(**json_data)
        # Check if the sender is an authorized hotkey
        if not signed_by or not is_authorized_hotkey(cursor, signed_by):
            raise HTTPException(status_code=401, detail="Unauthorized hotkey")
        cursor.executemany(
            """
            INSERT INTO miner_response (r_nanoid, hotkey, coldkey, uid, verified, time_to_first_token, time_for_all_tokens, total_time, response, tps) 
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                    md.stats.response,
                    md.stats.tps,
                )
                for md in payload.responses
            ],
        )

        # Insert validator request
        cursor.execute(
            """
            INSERT INTO validator_request (r_nanoid, block, vali_request, request_endpoint, version, hotkey) 
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                payload.request.r_nanoid,
                payload.request.block,
                json.dumps(payload.request.request),
                payload.request.request_endpoint,
                payload.request.version,
                payload.request.hotkey,
            ),
        )

        connection.commit()
        return "", 200

    except Exception as e:
        connection.rollback()
        error_traceback = traceback.format_exc()
        print(f"Error occurred: {str(e)}\n{error_traceback}")
        raise HTTPException(
            status_code=500,
            detail=f"Internal Server Error: Could not insert responses/requests. {str(e)}",
        )
    finally:
        cursor.close()
        connection.close()
