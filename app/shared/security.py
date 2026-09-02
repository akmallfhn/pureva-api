import hashlib
import hmac


def verify_meta_signature(raw_body: bytes, signature_header: str, app_secret: str) -> bool:
    """Cocokkan header `X-Hub-Signature-256` dengan HMAC body pakai Meta app secret."""
    if not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header.removeprefix("sha256="))
