"""
Hackathon team enrollment routes (solo or 2–4 member teams), scoped per round.

    GET  /hackathons/{id}/rounds/{round_index}/participation
    POST /hackathons/{id}/rounds/{round_index}/enroll/solo
    POST /hackathons/{id}/rounds/{round_index}/teams/create
    POST /hackathons/{id}/rounds/{round_index}/teams/join
    POST /hackathons/{id}/rounds/{round_index}/teams/join-code
"""

import logging

from fastapi import APIRouter, Depends, status

from app.dependencies import get_team_service
from app.exceptions import AppError
from app.middleware.auth_middleware import get_current_user
from app.models.team_model import (
    CreateTeamRequest,
    CreateTeamResponse,
    HackathonParticipationResponse,
    JoinTeamRequest,
    JoinTeamResponse,
    TeamJoinCodeResponse,
)
from app.models.user_model import CurrentUser
from app.services.team_service import TeamService
from app.utils.async_io import run_sync


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/hackathons", tags=["teams"])


@router.get(
    "/{hackathon_id}/rounds/{round_index}/participation",
    response_model=HackathonParticipationResponse,
)
async def get_participation(
    hackathon_id: str,
    round_index: int,
    current_user: CurrentUser = Depends(get_current_user),
    team_service: TeamService = Depends(get_team_service),
) -> HackathonParticipationResponse:
    """Return the student's enrollment state for a hackathon round."""
    try:
        return await run_sync(
            team_service.get_participation, hackathon_id, round_index, current_user
        )
    except AppError:
        raise


@router.post(
    "/{hackathon_id}/rounds/{round_index}/enroll/solo",
    response_model=HackathonParticipationResponse,
    status_code=status.HTTP_200_OK,
)
async def enroll_solo(
    hackathon_id: str,
    round_index: int,
    current_user: CurrentUser = Depends(get_current_user),
    team_service: TeamService = Depends(get_team_service),
) -> HackathonParticipationResponse:
    """Enroll directly for solo (1-member) rounds."""
    try:
        return await run_sync(
            team_service.enroll_solo, hackathon_id, round_index, current_user
        )
    except AppError:
        raise


@router.post(
    "/{hackathon_id}/rounds/{round_index}/teams/create",
    response_model=CreateTeamResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_team(
    hackathon_id: str,
    round_index: int,
    payload: CreateTeamRequest,
    current_user: CurrentUser = Depends(get_current_user),
    team_service: TeamService = Depends(get_team_service),
) -> CreateTeamResponse:
    """
    Create a team as leader for this round (or refresh join code if already leader).

    Requires ``team_name`` on first create. Returns a 6-digit join code valid for 5 minutes.
    """
    try:
        return await run_sync(
            team_service.create_team,
            hackathon_id,
            round_index,
            current_user,
            payload.team_name,
        )
    except AppError:
        raise


@router.post(
    "/{hackathon_id}/rounds/{round_index}/teams/join",
    response_model=JoinTeamResponse,
    status_code=status.HTTP_200_OK,
)
async def join_team(
    hackathon_id: str,
    round_index: int,
    payload: JoinTeamRequest,
    current_user: CurrentUser = Depends(get_current_user),
    team_service: TeamService = Depends(get_team_service),
) -> JoinTeamResponse:
    """Join an existing team for this round using the leader's 6-digit code."""
    try:
        return await run_sync(
            team_service.join_team,
            hackathon_id,
            round_index,
            current_user,
            payload.code,
        )
    except AppError:
        raise


@router.post(
    "/{hackathon_id}/rounds/{round_index}/teams/join-code",
    response_model=TeamJoinCodeResponse,
    status_code=status.HTTP_200_OK,
)
async def refresh_join_code(
    hackathon_id: str,
    round_index: int,
    current_user: CurrentUser = Depends(get_current_user),
    team_service: TeamService = Depends(get_team_service),
) -> TeamJoinCodeResponse:
    """Team leader only — issue a new 6-digit join code for this round."""
    try:
        return await run_sync(
            team_service.refresh_join_code, hackathon_id, round_index, current_user
        )
    except AppError:
        raise
