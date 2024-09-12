from hashlib import sha256
from typing import Annotated, Optional
from substrateinterface import Keypair


def verify_signature(
        signature, body: bytes, timestamp, uuid, signed_by, now, signed_for: Optional[str] = None,
) -> Optional[Annotated[str, "Error Message"]]:
    if not isinstance(signature, str):
        return "Invalid Signature"
    timestamp = int(timestamp)
    if not isinstance(timestamp, int):
        return "Invalid Timestamp"
    if not isinstance(signed_by, str):
        return "Invalid Sender key"
    if not isinstance(uuid, str):
        return "Invalid uuid"
    if not isinstance(body, bytes):
        return "Body is not of type bytes"
    ALLOWED_DELTA_MS = 8000
    keypair = Keypair(ss58_address=signed_by)
    if timestamp + ALLOWED_DELTA_MS < now:
        return f"Request is too stale: {timestamp + ALLOWED_DELTA_MS} < {now}"
    message = f"{sha256(body).hexdigest()}.{uuid}.{timestamp}.{signed_for or ''}"
    verified = keypair.verify(message, signature)
    if not verified:
        return "Signature Mismatch"
    return None
