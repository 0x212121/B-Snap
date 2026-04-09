from datetime import datetime, timezone


def is_valid_web_token(token: str, token_list: list[dict]) -> bool:
    if not token_list:  # Handle None atau empty list
        return False
    now = datetime.now(timezone.utc)
    for t in token_list:
        if t["token"] == token:
            expires = datetime.fromisoformat(t["expires_at"])
            return expires > now
    return False