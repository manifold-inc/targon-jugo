from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from typing import Optional, Annotated
from substrateinterface import Keypair
import time
import asyncpg
from dotenv import load_dotenv
import os
import json

app = FastAPI()
load_dotenv()

# Placeholder address for the `signed_for` field
PLACEHOLDER_ADDRESS = "targon-stats"

# Data models for incoming requests
class EpistulaRequest(BaseModel):
    data: dict
    nonce: int
    signed_by: str
    signed_for: Optional[str] = None
    version: int

class MinerResponse(BaseModel):
    r_nanoid: str
    hotkey: str
    coldkey: str
    uid: int
    stats: dict

class ValidatorRequest(BaseModel):
    r_nanoid: str
    block: int
    sampling_params: dict
    ground_truth: dict
    version: int
    hotkey: str

# Establish a connection to the database
async def connect_to_db():
    try:
        connection = await asyncpg.connect(os.getenv("DATABASE_URL"))
        return connection
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal Server Error: Could not connect to the database. {str(e)}")


def verify_signature(
    signature: str, body: bytes, nonce: int, sender: str, now: int
) -> Optional[Annotated[str, "Error Message"]]:
    if not isinstance(signature, str):
        return "Invalid Signature"
    if not isinstance(nonce, int):
        return "Invalid Nonce"
    if not isinstance(sender, str):
        return "Invalid Sender key"
    if not isinstance(body, bytes):
        return "Body is not of type bytes"

    ALLOWED_DELTA_NS = 5 * 1000000000  # 5 seconds

    keys = Keypair(ss58_address=sender)

    if nonce + ALLOWED_DELTA_NS < now:
        return "Request is too stale"

    try:
        signature_bytes = bytes.fromhex(signature)
    except ValueError:
        return "Invalid Signature Format"

    verified = keys.verify(body, signature_bytes)
    if not verified:
        return "Signature Mismatch"

    return None
# Authorization check, is this a true validator hotkey
async def is_authorized_hotkey(conn, signed_by: str) -> bool:
    row = await conn.fetchrow('SELECT 1 FROM validator WHERE hotkey = $1', signed_by)
    return row is not None

# MinerResponse endpoint with all checks
@app.post("/miner_response")
async def miner_response(request: EpistulaRequest, body_signature: str = Header(...)):
    now = time.time_ns()

    # Parse JSON to get specific fields
    json_data = request.dict()
    signed_by = json_data.get("signed_by")
    signed_for = json_data.get("signed_for")
    nonce = json_data.get("nonce")

    # Validate nonce and signed_by
    if nonce is None or signed_by is None:
        raise HTTPException(status_code=400, detail="Nonce or Signed By is missing or invalid")

    # Check if the message is intended for the correct recipient using the placeholder address
    if signed_for != PLACEHOLDER_ADDRESS:
        raise HTTPException(
            status_code=400, detail="Bad Request, message is not intended for this server"
        )

    # Verify the signature
    body = json.dumps(json_data).encode('utf-8')
    err = verify_signature(
        body_signature,
        body,
        nonce,
        signed_by,
        now,
    )

    if err:
        raise HTTPException(status_code=400, detail=err)

    # Connect to the database
    conn = await connect_to_db()

    try:
        # Check if the sender is an authorized hotkey
        if not await is_authorized_hotkey(conn, signed_by):
            raise HTTPException(status_code=401, detail="Unauthorized hotkey")

        # Insert miner response data into the database
        miner_response_data = MinerResponse(**json_data['data'])
        await conn.execute(
            """
            INSERT INTO miner_response (r_nanoid, hotkey, coldkey, uid, stats) 
            VALUES ($1, $2, $3, $4, $5)
            """,
            miner_response_data.r_nanoid, miner_response_data.hotkey, miner_response_data.coldkey, miner_response_data.uid, json.dumps(miner_response_data.stats)
        )
        return {"message": "Miner response data inserted successfully"}

    except Exception as e:
        print(f"Error inserting miner response: {e}")
        raise HTTPException(status_code=500, detail=f"Internal Server Error: Could not insert miner response data. {str(e)}")
    finally:
        await conn.close()

# ValidatorRequest endpoint with all checks
@app.post("/validator_request")
async def validator_request(request: EpistulaRequest, body_signature: str = Header(...)):
    now = time.time_ns()

    # Parse JSON to get specific fields
    json_data = request.dict()
    signed_by = json_data.get("signed_by")
    signed_for = json_data.get("signed_for")
    nonce = json_data.get("nonce")

    # Validate nonce and signed_by
    if nonce is None or signed_by is None:
        raise HTTPException(status_code=400, detail="Nonce or Signed By is missing or invalid")

    # Check if the message is intended for the correct recipient using the placeholder address
    if signed_for != PLACEHOLDER_ADDRESS:
        raise HTTPException(
            status_code=400, detail="Bad Request, message is not intended for this server"
        )

    # Verify the signature
    body = json.dumps(json_data).encode('utf-8')
    err = verify_signature(
        body_signature,
        body,
        nonce,
        signed_by,
        now,
    )

    if err:
        raise HTTPException(status_code=400, detail=err)

    # Connect to the database
    conn = await connect_to_db()

    try:
        # Check if the sender is an authorized hotkey
        if not await is_authorized_hotkey(conn, signed_by):
            raise HTTPException(status_code=401, detail="Unauthorized hotkey")

        # Insert validator request data into the database
        validator_request_data = ValidatorRequest(**json_data['data'])
        await conn.execute(
            """
            INSERT INTO validator_request (r_nanoid, block, sampling_params, ground_truth, version, hotkey) 
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            validator_request_data.r_nanoid, validator_request_data.block, json.dumps(validator_request_data.sampling_params), json.dumps(validator_request_data.ground_truth), validator_request_data.version, validator_request_data.hotkey
        )
        return {"message": "Validator request data inserted successfully"}

    except Exception as e:
        print(f"Error inserting validator request: {e}")
        raise HTTPException(status_code=500, detail=f"Internal Server Error: Could not insert validator request data. {str(e)}")
    finally:
        await conn.close()
