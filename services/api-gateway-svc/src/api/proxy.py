from fastapi import APIRouter, Request, Response

from src.services.proxy_engine import GatewayError, handle_request

router = APIRouter()


@router.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy(full_path: str, request: Request):
    path = f"/{full_path}"
    body = await request.body()
    access_token_cookie = request.cookies.get("access_token")
    client_host = request.client.host if request.client else None

    try:
        result = await handle_request(
            method=request.method,
            path=path,
            query_string=str(request.url.query),
            headers=dict(request.headers),
            body=body,
            access_token_cookie=access_token_cookie,
            client_host=client_host,
        )
    except GatewayError as exc:
        return Response(content=exc.detail, status_code=exc.status_code, media_type="text/plain")

    return Response(content=result.content, status_code=result.status_code, headers=result.headers)
