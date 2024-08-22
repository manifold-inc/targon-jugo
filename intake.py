from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import time
import asyncpg
import json
from dotenv import load_dotenv
import os
from .epistula import EpistulaRequest, generate_body, verify_signature

app = FastAPI()
load_dotenv()

# Placeholder address for the `signed_for` field
PLACEHOLDER_ADDRESS = "targon-stats"


# Models for organic requests and updates
class OrganicRequestData(BaseModel):
    response: Optional[str]
    uid: int
    pub_id: str
    messages: str
    max_tokens: int
    total_time: int


class OrganicScoreUpdateData(BaseModel):
    uid: int
    verified: bool
    wps: Optional[float] = None


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


# Establish a connection to the database
async def connect_to_db():
    try:
        connection = await asyncpg.connect(os.getenv("DATABASE_URL"))
        return connection
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Internal Server Error: Could not connect to the database. {str(e)}",
        )


# Function to verify if the hotkey is authorized
async def is_authorized_hotkey(conn, signed_by: str) -> bool:
    row = await conn.fetchrow("SELECT 1 FROM validator WHERE hotkey = $1", signed_by)
    return row is not None


# GET endpoint to fetch organic requests
@app.get("/organic_requests", response_model=EpistulaRequest[List[OrganicRequestData]])
async def get_organic_requests(
    signed_by: str = Header(...),
    body_signature: str = Header(...),
    nonce: int = Header(...),
):
    conn = None
    try:
        # Connect to the database
        conn = await connect_to_db()

        # First, verify that the sender is a registered validator
        if not await is_authorized_hotkey(conn, signed_by):
            raise HTTPException(
                status_code=401,
                detail="Unauthorized: sender is not a registered validator",
            )

        # Prepare the data to be signed and verify the signature
        to_sign = f"{signed_by}:{nonce}"
        now = time.time_ns()

        err = verify_signature(
            signature=body_signature,
            body=to_sign.encode("utf-8"),
            nonce=nonce,
            sender=signed_by,
            now=now,
        )
        if err:
            raise HTTPException(
                status_code=400, detail=f"Signature verification failed: {err}"
            )

        # Now that the signature is verified, perform the database query
        rows = await conn.fetch(
            """
            SELECT response, uid, pub_id, request->'messages' as messages, request->'max_tokens' as max_tokens, 
                   metadata->'request_duration_ms' as total_time 
            FROM organic_request
            WHERE scored=FALSE AND created_at >= (NOW() - INTERVAL '30 minutes') LIMIT 5
            """
        )

        # Process the query results into the expected format
        organics = [
            OrganicRequestData(
                response=row["response"],
                uid=row["uid"],
                pub_id=row["pub_id"],
                messages=json.dumps(row["messages"]),
                max_tokens=row["max_tokens"],
                total_time=row["total_time"],
            )
            for row in rows
        ]

        # Respond with the organic data wrapped in an EpistulaRequest
        return EpistulaRequest(
            data=organics, nonce=nonce, signed_by="targon-stats", signed_for=signed_by
        )

    except Exception as e:
        print(f"Error fetching organic requests: {e}")
        raise HTTPException(
            status_code=500,
            detail="Internal Server Error: Could not fetch organic requests.",
        )
    finally:
        if conn:
            await conn.close()


@app.post("/organic_requests/score")
async def update_organic_scores(
    request: EpistulaRequest[List[OrganicScoreUpdateData]],
    body_signature: str = Header(...),
):
    conn = None
    try:
        now = time.time_ns()
        json_data = request.dict()
        signed_by = json_data.get("signed_by")
        nonce = json_data.get("nonce")

        if nonce is None or signed_by is None:
            raise HTTPException(
                status_code=400, detail="Nonce or Signed By is missing or invalid"
            )

        # Convert the data to JSON and encode it to bytes
        body = json.dumps(json_data).encode("utf-8")

        # Verify the signature
        err = verify_signature(
            signature=body_signature, body=body, nonce=nonce, sender=signed_by, now=now
        )

        if err:
            raise HTTPException(
                status_code=400, detail=f"Signature verification failed: {err}"
            )

        # Connect to the database
        conn = await connect_to_db()

        # Check if the sender is an authorized hotkey
        if not await is_authorized_hotkey(conn, signed_by):
            raise HTTPException(status_code=401, detail="Unauthorized hotkey")

        # Update the database with the scoring results
        for score in request.data:
            await conn.execute(
                """
                UPDATE organic_request 
                SET scored=TRUE, verified=$1, wps=$2
                WHERE uid=$3
                """,
                score.verified,
                score.wps,
                score.uid,
            )

        return {"message": "Organic request scores updated successfully"}

    except Exception as e:
        print(f"Error updating organic request scores: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Internal Server Error: Could not update organic request scores. {str(e)}",
        )
    finally:
        if conn:
            await conn.close()


# MinerResponse endpoint
@app.post("/miner_response")
async def miner_response(
    request: EpistulaRequest[MinerResponse], body_signature: str = Header(...)
):
    now = time.time_ns()

    json_data = request.dict()
    signed_by = json_data.get("signed_by")
    nonce = json_data.get("nonce")

    if nonce is None or signed_by is None:
        raise HTTPException(
            status_code=400, detail="Nonce or Signed By is missing or invalid"
        )

    body = json.dumps(json_data).encode("utf-8")
    err = verify_signature(
        body_signature,
        body,
        nonce,
        signed_by,
        now,
    )

    if err:
        raise HTTPException(status_code=400, detail=err)

    conn = await connect_to_db()

    try:
        # Check if the sender is an authorized hotkey
        if not await is_authorized_hotkey(conn, signed_by):
            raise HTTPException(status_code=401, detail="Unauthorized hotkey")

        # Insert miner response data into the database
        miner_response_data = MinerResponse(**json_data["data"])
        await conn.execute(
            """
            INSERT INTO miner_response (r_nanoid, hotkey, coldkey, uid, stats) 
            VALUES ($1, $2, $3, $4, $5)
            """,
            miner_response_data.r_nanoid,
            miner_response_data.hotkey,
            miner_response_data.coldkey,
            miner_response_data.uid,
            json.dumps(miner_response_data.stats),
        )
        return {"message": "Miner response data inserted successfully"}

    except Exception as e:
        print(f"Error inserting miner response: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Internal Server Error: Could not insert miner response data. {str(e)}",
        )
    finally:
        await conn.close()


# ValidatorRequest endpoint
@app.post("/validator_request")
async def validator_request(
    request: EpistulaRequest[ValidatorRequest], body_signature: str = Header(...)
):
    now = time.time_ns()

    json_data = request.dict()
    signed_by = json_data.get("signed_by")
    nonce = json_data.get("nonce")

    if nonce is None or signed_by is None:
        raise HTTPException(
            status_code=400, detail="Nonce or Signed By is missing or invalid"
        )

    body = json.dumps(json_data).encode("utf-8")
    err = verify_signature(
        body_signature,
        body,
        nonce,
        signed_by,
        now,
    )

    if err:
        raise HTTPException(status_code=400, detail=err)

    conn = await connect_to_db()

    try:
        # Check if the sender is an authorized hotkey
        if not await is_authorized_hotkey(conn, signed_by):
            raise HTTPException(status_code=401, detail="Unauthorized hotkey")

        # Insert validator request data into the database
        validator_request_data = ValidatorRequest(**json_data["data"])
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
        return {"message": "Validator request data inserted successfully"}

    except Exception as e:
        print(f"Error inserting validator request: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Internal Server Error: Could not insert validator request data. {str(e)}",
        )
    finally:
        await conn.close()
