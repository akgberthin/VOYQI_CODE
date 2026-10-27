"""Images VOYQI hosts for marketing: uploaded, or generated from a prompt
with the tenant's OpenAI integration. Served at a public URL because
social platforms fetch images by URL."""
import base64
import logging
import uuid

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core import integrations
from src.core.config import get_settings
from src.models import MediaAsset, MediaGenerationRequest, MediaGenerationStatus

logger = logging.getLogger("marketing-svc.media")
IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}


class InvalidMedia(Exception):
    pass


def public_url(asset_id: uuid.UUID | None) -> str | None:
    if not asset_id:
        return None
    return f"{get_settings().public_base_url.rstrip('/')}/api/v1/marketing/public/media/{asset_id}"


async def store(db: AsyncSession, tenant_id: str, data: bytes, content_type: str, title: str, source: str, created_by: str,
                generation_request_id: uuid.UUID | None = None) -> MediaAsset:
    if content_type not in IMAGE_TYPES:
        raise InvalidMedia(f"{content_type} isn't a supported image type ({', '.join(sorted(IMAGE_TYPES))})")
    if len(data) > get_settings().max_upload_bytes:
        raise InvalidMedia(f"image is {len(data) // 1024} KB; the limit is {get_settings().max_upload_bytes // 1024} KB")
    asset = MediaAsset(tenant_id=tenant_id, data=data, content_type=content_type, title=title[:512], source=source,
                       created_by=created_by, generation_request_id=generation_request_id)
    db.add(asset)
    await db.commit()
    await db.refresh(asset)
    return asset


async def get(db: AsyncSession, asset_id: uuid.UUID) -> MediaAsset | None:
    return (await db.execute(select(MediaAsset).where(MediaAsset.id == asset_id))).scalar_one_or_none()


async def list_assets(db: AsyncSession, tenant_id: str) -> list[MediaAsset]:
    result = await db.execute(select(MediaAsset).where(MediaAsset.tenant_id == tenant_id).order_by(MediaAsset.created_at.desc()))
    return list(result.scalars().all())


async def openai_image(config: dict, prompt: str) -> bytes:
    async with httpx.AsyncClient(timeout=180.0) as client:
        resp = await client.post("https://api.openai.com/v1/images/generations",
                                 headers={"Authorization": f"Bearer {config['api_key']}"},
                                 json={"model": config.get("image_model") or "gpt-image-1", "prompt": prompt, "size": "1024x1024", "n": 1})
        if resp.status_code != 200:
            try:
                message = resp.json().get("error", {}).get("message")
            except ValueError:
                message = resp.text[:200]
            raise InvalidMedia(f"OpenAI HTTP {resp.status_code}: {message}")
        item = resp.json()["data"][0]
        if item.get("b64_json"):
            return base64.b64decode(item["b64_json"])
        image = await client.get(item["url"])  # models that answer with a URL
        image.raise_for_status()
        return image.content


async def generate(db: AsyncSession, tenant_id: str, request_id: uuid.UUID) -> MediaGenerationRequest:
    """Fulfils a generation request with the tenant's OpenAI integration.
    Without one the request stays pending — nothing pretends to be made."""
    req = (await db.execute(select(MediaGenerationRequest).where(MediaGenerationRequest.id == request_id, MediaGenerationRequest.tenant_id == tenant_id))).scalar_one()
    media_type = req.media_type.value if hasattr(req.media_type, "value") else str(req.media_type)
    if media_type != "image":
        req.status, req.error_detail = MediaGenerationStatus.FAILED, "Video generation needs a video provider, which isn't connected"
        await db.commit()
        return req
    config = await integrations.get(tenant_id, "openai")
    if not config:
        return req
    req.status, req.provider = MediaGenerationStatus.GENERATING, "openai"
    await db.commit()
    try:
        data = await openai_image(config, req.prompt)
        asset = await store(db, tenant_id, data, "image/png", req.prompt, "openai", req.requested_by, req.id)
        req.status, req.result_url = MediaGenerationStatus.COMPLETED, public_url(asset.id)
    except (InvalidMedia, httpx.HTTPError, KeyError, ValueError) as exc:
        logger.warning("image generation %s failed: %s", request_id, exc)
        req.status, req.error_detail = MediaGenerationStatus.FAILED, str(exc)[:2000]
    await db.commit()
    await db.refresh(req)
    return req
