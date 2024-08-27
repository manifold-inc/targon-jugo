from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from typing import List, Dict, Any
import time
from dotenv import load_dotenv
import os
from epistula import verify_signature
import MySQLdb
import json

app = FastAPI()
load_dotenv()

# Define the MinerResponse model
class MinerResponse(BaseModel):
    r_nanoid: str
    hotkey: str
    coldkey: str
    uid: int
    stats: Dict[str, Any]


# Define the ValidatorRequest model
class ValidatorRequest(BaseModel):
    r_nanoid: str
    block: int
    sampling_params: Dict[str, Any]
    ground_truth: Dict[str, Any]
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
async def ingest(payload: IngestPayload, request: Request):
    now = time.time_ns()
    body = await request.body()
    json_data = await request.json()
    
    # Extract signature information from headers
    timestamp = request.headers.get("Epistula-Timestamp")
    uuid = request.headers.get("Epistula-Uuid")
    signed_by = request.headers.get("Epistula-Signed-By")
    signature = request.headers.get("Epistula-Request-Signature")

    if not all([signature, timestamp, uuid, signed_by]):
        raise HTTPException(
            status_code=400, detail="Signature, timestamp, uuid, signed_by is missing"
        )

    # Verify the signature using the new epistula protocol
    err = verify_signature(signature=signature, body=body, timestamp=timestamp, uuid=uuid, signed_by=signed_by, now=now)

    if err:
        raise HTTPException(status_code=400, detail=str(err))

    connection = MySQLdb.connect(
        host=os.getenv("DATABASE_HOST"),
        user=os.getenv("DATABASE_USERNAME"),
        passwd=os.getenv("DATABASE_PASSWORD"),
        db=os.getenv("DATABASE"),
        autocommit=True,
        ssl_mode="VERIFY_IDENTITY",
        ssl={ "ca": "/etc/ssl/cert.pem" }
    )


    cursor = connection.cursor()
    try:
        # Check if the sender is an authorized hotkey
        if not is_authorized_hotkey(cursor, signed_by):
            raise HTTPException(status_code=401, detail="Unauthorized hotkey")

        validator_request_data = ValidatorRequest(**json_data["request"])
        cursor.execute(
            """
            INSERT INTO validator_request (r_nanoid, block, sampling_params, ground_truth, version, hotkey) 
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                validator_request_data.r_nanoid,
                validator_request_data.block,
                json.dumps(validator_request_data.sampling_params),
                json.dumps(validator_request_data.ground_truth),
                validator_request_data.version,
                validator_request_data.hotkey,
            )
        )

        miner_response_data = [MinerResponse(**data) for data in json_data["responses"]]
        cursor.executemany(
            """
            INSERT INTO miner_response (r_nanoid, hotkey, coldkey, uid, stats) 
            VALUES (%s, %s, %s, %s, %s)
            """,
            [
                (md.r_nanoid, md.hotkey, md.coldkey, md.uid, json.dumps(md.stats))
                for md in miner_response_data
            ],
        )

        connection.commit()
        return "", 200

    except Exception as e:
        connection.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Internal Server Error: Could not insert responses/requests. {str(e)}",
        )
    finally:
        cursor.close()
        connection.close()
