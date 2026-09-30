from fastapi import APIRouter, Depends, Request

from ..errors import EnvelopeRoute
from ..deps import current_user, lang_dep, user_release_db
from ..models import User
from ..schemas import ChecklistIn
from ..ai import service as ai
from ..services import advisor
from ..services.rules_engine import RULES

router = APIRouter(tags=["test advisor & rules"], route_class=EnvelopeRoute)


@router.post("/advisor/checklist")
async def checklist(body: ChecklistIn, request: Request, user: User = Depends(user_release_db), lang: str = Depends(lang_dep)):
    """Rule-based test checklist for a construction scenario (with IS references). `explain=true` adds a plain-language summary."""
    result = advisor.build_checklist(**body.model_dump(exclude={"explain"}))
    if lang == "hi":
        for it in result["items"]:
            it["name"], it["purpose"] = it["name_hi"], it["purpose_hi"]
    if body.explain:
        result["explanation"] = await ai.explain_checklist(ai.Caller(user, getattr(request.state, "request_id", "-"), lang), result)
    return result


@router.get("/advisor/knowledge")
def knowledge(q: str, limit: int = 5, user: User = Depends(current_user)):
    """Search the verified knowledge notes (used to ground explanations)."""
    return {"query": q, "results": advisor.search_knowledge(q, min(limit, 10))}


@router.get("/rules")
def rules(user: User = Depends(current_user)):
    """The configured IS-code requirements the validation engine applies (transparent by design)."""
    return {k: v for k, v in RULES.items() if k != "knowledge"}
