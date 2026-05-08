from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import get_current_user, require_manager
from ..storage import db

router = APIRouter(prefix="/teams", tags=["teams"])


class CreateTeamPayload(BaseModel):
    name: str


class AddMemberPayload(BaseModel):
    user_id: int


@router.get("/leaderboard")
async def team_leaderboard(user=Depends(get_current_user)):
    return db.get_team_leaderboard()


@router.get("/mine")
async def my_team(user=Depends(get_current_user)):
    uid  = user["user_id"]
    role = user["role"]
    u    = db.get_user_by_id(uid)
    if not u:
        raise HTTPException(404, "User not found")
    if role in ("field_manager", "evan", "boss", "admin"):
        team = db.get_team_by_manager(uid)
    else:
        team = db.get_team(u.team_id) if u.team_id else None
    if not team:
        return None
    return db.get_team_detail(team.id)


@router.get("/unassigned")
async def unassigned_workers(user=Depends(require_manager)):
    workers = db.get_unassigned_workers()
    return [{"id": w.id, "full_name": w.full_name, "role": w.role} for w in workers]


@router.post("")
async def create_team(payload: CreateTeamPayload, user=Depends(require_manager)):
    name = payload.name.strip()
    if not name:
        raise HTTPException(400, "Team name required")
    existing = db.get_team_by_manager(user["user_id"])
    if existing:
        raise HTTPException(400, "You already have a team — rename it instead")
    team_id = db.create_team(name, user["user_id"])
    return {"id": team_id, "name": name}


@router.post("/{team_id}/members")
async def add_member(team_id: int, payload: AddMemberPayload, user=Depends(require_manager)):
    team = db.get_team(team_id)
    if not team:
        raise HTTPException(404, "Team not found")
    if user["role"] in ("field_manager", "evan") and team.manager_id != user["user_id"]:
        raise HTTPException(403, "Not your team")
    db.set_user_team(payload.user_id, team_id)
    return {"ok": True}


@router.delete("/{team_id}/members/{user_id}")
async def remove_member(team_id: int, user_id: int, user=Depends(require_manager)):
    team = db.get_team(team_id)
    if not team:
        raise HTTPException(404, "Team not found")
    if user["role"] in ("field_manager", "evan") and team.manager_id != user["user_id"]:
        raise HTTPException(403, "Not your team")
    db.set_user_team(user_id, None)
    return {"ok": True}
