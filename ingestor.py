from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from typing import List, Dict, Any
import time
import asyncpg
from dotenv import load_dotenv
import os
from epistula import verify_signature

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


# Function to verify if the hotkey is authorized
async def is_authorized_hotkey(conn, signed_by: str) -> bool:
    row = await conn.fetchrow("SELECT 1 FROM validator WHERE hotkey = $1", signed_by)
    return row is not None


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

    now = time.time_ns()    

    if not all([signature, timestamp, address]):
        raise HTTPException(
            status_code=400, detail="Signature, timestamp, or address is missing"
        )

    # Verify the signature using the new epistula protocol
    err = verify_signature(signature, body, timestamp, uuid, signed_by, now)

    if err:
        raise HTTPException(status_code=400, detail=str(err))

    conn: asyncpg.Connection = await asyncpg.connect(os.getenv("DATABASE_URL"))

    try:
        # Check if the sender is an authorized hotkey
        if not await is_authorized_hotkey(conn, signed_by):
            raise HTTPException(status_code=401, detail="Unauthorized hotkey")
        async with conn.transaction():
            validator_request_data = ValidatorRequest(**json_data["data"]["request"])
            await conn.execute(
                """
                INSERT INTO validator_request (r_nanoid, block, sampling_params, ground_truth, version, hotkey) 
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                validator_request_data.r_nanoid,
                validator_request_data.block,
                json.dumps(validator_request_data.sampling_params),
                json.dumps(validator_request_data.ground_truth),
                validator_request_data.version,
                validator_request_data.hotkey,
            )
            # Insert miner response data into the database
            miner_response_data = [
                MinerResponse(**data) for data in json_data["data"]["response"]
            ]
            await conn.executemany(
                """
                INSERT INTO miner_response (r_nanoid, hotkey, coldkey, uid, stats) 
                VALUES ($1, $2, $3, $4, $5)
                """,
                [
                    (md.r_nanoid, md.hotkey, md.coldkey, md.uid, json.dumps(md.stats))
                    for md in miner_response_data
                ],
            )
        return "", 200

    except Exception as e:
        print(f"Error inserting miner response: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Internal Server Error: Could not insert miner response data. {str(e)}",
        )
    finally:
        await conn.close()
