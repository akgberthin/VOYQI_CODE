"""
SAP-006 — Theme (color palette) engine.

Fully autonomous: activating a theme is a display-configuration
change, not an access-control decision. Exactly one theme is active
per tenant at any time — activate_theme enforces this by deactivating
every other theme for the tenant in the same transaction, so "which
theme is active" is never ambiguous or multi-valued.
"""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import Theme, ThemeColor, ThemeColorKey

DEFAULT_PALETTE = {
    ThemeColorKey.PRIMARY: "#c9a84c",
    ThemeColorKey.SECONDARY: "#3d8ed4",
    ThemeColorKey.ACCENT: "#7b6cd4",
    ThemeColorKey.BACKGROUND: "#07090f",
    ThemeColorKey.SURFACE: "#0d1320",
    ThemeColorKey.TEXT: "#edeae0",
    ThemeColorKey.TEXT_MUTED: "#6e8096",
    ThemeColorKey.SUCCESS: "#2ea87a",
    ThemeColorKey.WARNING: "#c9a84c",
    ThemeColorKey.ERROR: "#d05050",
    ThemeColorKey.INFO: "#3d8ed4",
    ThemeColorKey.BORDER: "#ffffff0f",
}


async def create_theme(db: AsyncSession, tenant_id: str, name: str, seed_defaults: bool = True) -> Theme:
    theme = Theme(tenant_id=tenant_id, name=name)
    db.add(theme)
    await db.commit()
    await db.refresh(theme)

    if seed_defaults:
        for color_key, hex_value in DEFAULT_PALETTE.items():
            db.add(ThemeColor(tenant_id=tenant_id, theme_id=theme.id, color_key=color_key, hex_value=hex_value))
        await db.commit()

    return theme


async def list_themes(db: AsyncSession, tenant_id: str) -> list[Theme]:
    result = await db.execute(select(Theme).where(Theme.tenant_id == tenant_id))
    return list(result.scalars().all())


async def get_theme(db: AsyncSession, tenant_id: str, theme_id: uuid.UUID) -> Theme | None:
    result = await db.execute(select(Theme).where(Theme.id == theme_id, Theme.tenant_id == tenant_id))
    return result.scalar_one_or_none()


async def get_theme_colors(db: AsyncSession, tenant_id: str, theme_id: uuid.UUID) -> list[ThemeColor]:
    result = await db.execute(select(ThemeColor).where(ThemeColor.tenant_id == tenant_id, ThemeColor.theme_id == theme_id))
    return list(result.scalars().all())


async def set_theme_color(db: AsyncSession, tenant_id: str, theme_id: uuid.UUID, color_key: str, hex_value: str) -> ThemeColor:
    result = await db.execute(select(ThemeColor).where(ThemeColor.tenant_id == tenant_id, ThemeColor.theme_id == theme_id, ThemeColor.color_key == color_key))
    existing = result.scalar_one_or_none()
    if existing:
        existing.hex_value = hex_value
        await db.commit()
        await db.refresh(existing)
        return existing

    color = ThemeColor(tenant_id=tenant_id, theme_id=theme_id, color_key=color_key, hex_value=hex_value)
    db.add(color)
    await db.commit()
    await db.refresh(color)
    return color


async def activate_theme(db: AsyncSession, tenant_id: str, theme_id: uuid.UUID) -> Theme | None:
    """Deactivates every other theme for this tenant in the same
    call — exactly one theme is ever active, structurally, not by
    convention the caller has to remember."""
    theme = await get_theme(db, tenant_id, theme_id)
    if theme is None:
        return None

    all_themes = await list_themes(db, tenant_id)
    for t in all_themes:
        t.is_active = (t.id == theme_id)
    await db.commit()
    await db.refresh(theme)
    return theme


async def get_active_theme(db: AsyncSession, tenant_id: str) -> Theme | None:
    result = await db.execute(select(Theme).where(Theme.tenant_id == tenant_id, Theme.is_active == True))  # noqa: E712
    return result.scalar_one_or_none()


async def delete_theme(db: AsyncSession, tenant_id: str, theme_id: uuid.UUID) -> bool:
    theme = await get_theme(db, tenant_id, theme_id)
    if theme is None:
        return False
    colors = await get_theme_colors(db, tenant_id, theme_id)
    for c in colors:
        await db.delete(c)
    await db.delete(theme)
    await db.commit()
    return True
