from fastapi.responses import JSONResponse

def json_error_response(message: str, status_code: int = 400) -> JSONResponse:
    """
    Balikin response error standar dalam format JSON.
    Default status_code = 400 (Bad Request).
    """
    return JSONResponse(
        status_code=status_code,
        content={"status": "error", "message": message}
    )


def json_success_response(message: str, data: dict | None = None, status_code: int = 200) -> JSONResponse:
    """
    Balikin response sukses standar dalam format JSON.
    Bisa bawa data tambahan.
    """
    payload = {"status": "success", "message": message}
    if data:
        payload["data"] = data
    return JSONResponse(
        status_code=status_code,
        content=payload
    )
