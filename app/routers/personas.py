"""GET /api/personas — 화면의 역할 선택 드롭다운에 보여줄 목록."""

from fastapi import APIRouter, Depends

from app.auth import get_current_user
from app.personas import DEFAULT_PERSONA_ID, PERSONAS
from app.schemas import PersonaOption, PersonasResponse

router = APIRouter(dependencies=[Depends(get_current_user)])


@router.get("/personas", response_model=PersonasResponse)
async def list_personas() -> PersonasResponse:
    return PersonasResponse(
        default=DEFAULT_PERSONA_ID,
        personas=[
            PersonaOption(id=p.id, name=p.name, description=p.description)
            for p in PERSONAS
        ],
    )
