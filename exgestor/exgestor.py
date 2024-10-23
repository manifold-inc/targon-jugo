from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from typing import Optional
from dotenv import load_dotenv
import os

from pymysql.cursors import DictCursor
from epistula import verify_signature
import pymysql
import traceback
from nanoid import generate
from cachetools import TTLCache
import json
import time

load_dotenv()
pymysql.install_as_MySQLdb()
DEBUG = not not os.getenv("DEBUG")
config = {}
if not DEBUG:
    config = {"docs_url": None, "redoc_url": None}
app = FastAPI(**config) #type: ignore


class CurrentBucket(BaseModel):
    id: Optional[str] = None
    last_id: Optional[str] = None


# Global variables for bucket management
current_bucket = CurrentBucket()

# Cache: Store the data for 20 minutes (1200 seconds)
cache = TTLCache(maxsize=1, ttl=1200)

connection = pymysql.connect(
    host=os.getenv("HUB_DATABASE_HOST"),
    user=os.getenv("HUB_DATABASE_USERNAME"),
    passwd=os.getenv("HUB_DATABASE_PASSWORD"),
    db=os.getenv("HUB_DATABASE"),
    autocommit=True,
    ssl={"ssl_ca": "/etc/ssl/certs/ca-certificates.crt"},
)


async def get_cached_records():
    # If cache is empty or expired, fetch new records and update the cache
    if "current_bucket" not in cache:
        # Update the bucket id
        alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
        current_bucket.id = "b_" + generate(alphabet=alphabet, size=14)

        cursor = connection.cursor(DictCursor)
        try:
            # Fetch the latest 50 records after the last processed ID
            cursor.execute(
                """
                SELECT id, request, response, uid, hotkey, endpoint
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
            print(f"Error decoding JSON: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail=f"Internal Server Error: Invalid JSON in response. {str(e)}",
            )
        except Exception as e:
            error_traceback = traceback.format_exc()
            print(f"Error occurred: {str(e)}\n{error_traceback}")
            raise HTTPException(
                status_code=500,
                detail=f"Internal Server Error: Could not fetch responses. {str(e)}",
            )
        finally:
            cursor.close()
            connection.close()

    # Return cached data (which may be an empty list of records)
    return cache["current_bucket"]


# Exegestor endpoint
@app.get("/exgest")
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

    cached_bucket = await get_cached_records()

    # Return the cached records and bucket id (records may be an empty list)
    return {
        "bucket_id": cached_bucket["bucket_id"],
        "records": cached_bucket["records"],
    }
