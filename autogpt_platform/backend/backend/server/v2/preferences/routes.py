# autogpt_platform/backend/backend/server/v2/preferences/routes.py
#
# User preferences endpoints. Generic per-user key/value store scoped under
# /api/me/preferences. The changelog feature uses key "changelog.lastSeenId".
#
# Integration TODOs (two short edits before this router is live):
# 1. Prisma schema: apply the UserPreference model from
#    autogpt_platform/backend/schema.prisma.changelog.snippet and run
#    `prisma generate && prisma migrate dev`.
# 2. Router mounting: wire into the API app in backend/api/app.py (or
#    equivalent) the same way other routers are mounted.

from __future__ import annotations

import prisma.models
from autogpt_libs.auth import get_user_id
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

router = APIRouter(prefix="/preferences", tags=["preferences"])

KEY_CHANGELOG_LAST_SEEN = "changelog.lastSeenId"


class ChangelogPrefs(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    last_seen_id: str | None = Field(default=None, alias="lastSeenId")


@router.get(
    "/changelog",
    response_model=ChangelogPrefs,
    response_model_by_alias=True,
)
async def get_changelog_prefs(
    user_id: str = Depends(get_user_id),
) -> ChangelogPrefs:
    # TODO: remove type: ignore once UserPreference is added to prisma schema
    pref = await prisma.models.UserPreference.prisma().find_unique(  # type: ignore[attr-defined]
        where={
            "userId_key": {
                "userId": user_id,
                "key": KEY_CHANGELOG_LAST_SEEN,
            }
        }
    )
    return ChangelogPrefs(last_seen_id=pref.value if pref else None)


@router.put(
    "/changelog",
    response_model=ChangelogPrefs,
    response_model_by_alias=True,
)
async def put_changelog_prefs(
    body: ChangelogPrefs,
    user_id: str = Depends(get_user_id),
) -> ChangelogPrefs:
    if not body.last_seen_id:
        raise HTTPException(status_code=400, detail="lastSeenId is required")

    if len(body.last_seen_id) > 64:
        raise HTTPException(status_code=400, detail="lastSeenId too long")

    # TODO: remove type: ignore once UserPreference is added to prisma schema
    await prisma.models.UserPreference.prisma().upsert(  # type: ignore[attr-defined]
        where={
            "userId_key": {
                "userId": user_id,
                "key": KEY_CHANGELOG_LAST_SEEN,
            }
        },
        data={
            "create": {
                "userId": user_id,
                "key": KEY_CHANGELOG_LAST_SEEN,
                "value": body.last_seen_id,
            },
            "update": {"value": body.last_seen_id},
        },
    )
    return body
