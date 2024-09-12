from hashlib import sha256
from typing import Annotated, Optional, Union, Dict, Any, List
from substrateinterface import Keypair
import json
import time
from math import ceil
from uuid import uuid4

def verify_signature(
        signature: str, body: Union[Dict[Any, Any], List[Any], bytes], timestamp: int, uuid: str, signed_by: str, now: int, signed_for: Optional[str] = None,
) -> Optional[Annotated[str, "Error Message"]]:
    if not isinstance(signature, str):
        return "Invalid Signature"
    if not isinstance(timestamp, int):
        return "Invalid Timestamp"
    if not isinstance(signed_by, str):
        return "Invalid Sender key"
    if not isinstance(uuid, str):
        return "Invalid uuid"
    if not isinstance(body, (bytes, dict, list)):
        return "Body is not of type bytes, dict, or list"
    
    ALLOWED_DELTA_MS = 8000
    keypair = Keypair(ss58_address=signed_by)
    if timestamp + ALLOWED_DELTA_MS < now:
        return f"Request is too stale: {timestamp + ALLOWED_DELTA_MS} < {now}"
    
    if isinstance(body, bytes):
        req_hash = sha256(body).hexdigest()
    else:
        req_hash = sha256(json.dumps(body, separators=(',', ':')).encode("utf-8")).hexdigest()
    
    message = f"{req_hash}.{uuid}.{timestamp}.{signed_for or ''}"
    print("Constructed message: ", message)
    print(f"Body hash: {req_hash}")
    print(f"Body type: {type(body)}")
    if not isinstance(body, bytes):
        print(f"JSON-encoded body: {json.dumps(body, separators=(',', ':'))}")
    
    # Convert signature to bytes if it's a hex string
    if isinstance(signature, str) and signature.startswith('0x'):
        signature = bytes.fromhex(signature[2:])
    
    # Ensure message is in bytes
    if isinstance(message, str):
        message = message.encode()

    print(f"Signature (bytes): {signature}")
    print(f"Message (bytes): {message}")

    verified = keypair.verify(message, signature)
    print(f"Verification result: {verified}")
    if not verified:
        return "Signature Mismatch"
    return None
