"""AI endpoints. Every one: authenticated, project-authorized, validated, rate-limited (IP bucket + per-user limits/budget),
timeout-bounded, and returned in the standard envelope (except the SSE stream)."""
import base64
import json
import logging
import time

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from ..ai import factory, kv, rag, store, tools
from ..ai import service as ai
from ..ai.dbutil import in_db, in_db_write
from ..ai.metrics import metrics
from ..ai.prompts import PROMPT_VERSION, READ_DISPLAY
from ..ai.schemas import AskIn, ChatIn, DisplayReading, DocumentIn, SearchIn, to_strength_mpa
from ..ai.structured import generate_structured
from ..config import settings
from ..database import get_db
from ..deps import TEST_ROLES, WRITE_ROLES, accessible_project_ids, current_user, ensure_access, lang_dep, user_release_db
from ..errors import EnvelopeRoute
from ..models import AiDocument, User
from ..services import audit, evidence

router = APIRouter(prefix="/ai", tags=["AI assistant"], route_class=EnvelopeRoute)
stream_router = APIRouter(prefix="/ai", tags=["AI assistant"])            # SSE must not be wrapped in the JSON envelope
log = logging.getLogger("buildguard.ai.api")


def admin_only(user: User = Depends(user_release_db)) -> User:
    if user.role != "admin":
        raise HTTPException(403, f"Role '{user.role}' is not permitted for this action")
    return user


def caller(request: Request, user: User, lang: str) -> ai.Caller:
    return ai.Caller(user=user, request_id=getattr(request.state, "request_id", "-"), lang=lang)


@router.get("/status")
async def status(user: User = Depends(user_release_db)):
    """Capabilities and limits. Provider/model details are only shown to admins; the frontend never needs them."""
    prov = factory.get_provider()
    out: dict = {"enabled": prov is not None, "mode": "llm+retrieval" if prov else "retrieval-only", "prompt_version": PROMPT_VERSION,
           "features": {"chat": True, "streaming": prov is not None, "tools": sorted(tools.TOOLS) if prov else [], "semantic_search": factory.embeddings_enabled(),
                        "vision": prov is not None, "project_documents": True},
           "limits": {"requests_per_minute": settings.ai_max_requests_per_minute, "daily_token_budget": settings.ai_daily_token_budget,
                      "max_message_chars": settings.ai_max_input_chars, "max_document_kb": settings.ai_max_document_kb}}
    if user.role == "admin":
        out["admin"] = {"provider": prov.name if prov else None, "model": prov.model if prov else None, "fallback": settings.ai_fallback_provider,
                        "embeddings": settings.ai_embedding_provider, "kv_backend": "redis" if settings.redis_url else "memory", "redis_ok": await kv.get_kv().ping()}
    return out


@router.post("/ask")
async def ask(body: AskIn, request: Request, user: User = Depends(user_release_db), lang: str = Depends(lang_dep)):
    """One-shot grounded Q&A over verified IS-code notes (and, with project_id, that project's documents)."""
    return await ai.ask(caller(request, user, lang), body.question, body.batch_id, body.project_id)


@router.post("/chat")
async def chat(body: ChatIn, request: Request, user: User = Depends(user_release_db), lang: str = Depends(lang_dep)):
    """Conversational assistant with read-only tools. Continue a conversation by passing conversation_id."""
    return await ai.chat(caller(request, user, lang), body.message, body.conversation_id, body.project_id)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@stream_router.post("/chat/stream")
async def chat_stream(body: ChatIn, request: Request, user: User = Depends(user_release_db), lang: str = Depends(lang_dep)):
    """Server-Sent Events. Events: meta, delta, tool, replace, done, error (see docs/AI_API.md)."""
    gen = ai.chat_events(caller(request, user, lang), body.message, body.conversation_id, body.project_id, stream=True)
    try:
        first = await gen.__anext__()                 # runs limits, ownership and project checks so failures are proper HTTP errors, not stream errors
    except HTTPException:
        await gen.aclose()
        raise

    async def frames():
        try:
            yield _sse(*first)
            async for kind, data in gen:
                yield _sse(kind, data)
        except HTTPException as e:
            yield _sse("error", {"code": getattr(e, "code", "ERROR"), "message": e.detail})
        except Exception:
            log.exception("stream failed")
            yield _sse("error", {"code": "INTERNAL_ERROR", "message": "Internal server error"})
        finally:
            await gen.aclose()                        # client gone or finished: stops the upstream provider stream too

    return StreamingResponse(frames(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no", "Connection": "keep-alive"})


# ───────────── conversations (strictly per-user) ─────────────
@router.get("/conversations")
async def conversations(limit: int = Query(30, ge=1, le=100), offset: int = Query(0, ge=0), user: User = Depends(user_release_db)):
    return await in_db(store.list_conversations, user.id, limit, offset)


@router.get("/conversations/{conv_id}")
async def conversation(conv_id: str, user: User = Depends(user_release_db)):
    return await in_db(store.get_conversation, user.id, conv_id)


@router.delete("/conversations/{conv_id}")
async def delete_conversation(conv_id: str, user: User = Depends(user_release_db)):
    await in_db_write(store.delete_conversation, user.id, conv_id)
    return {"deleted": True}


# ───────────── retrieval & project documents ─────────────
@router.post("/search")
async def search(body: SearchIn, request: Request, user: User = Depends(user_release_db)):
    """Semantic + keyword search over verified notes and the caller's project documents (no LLM call)."""
    await ai.enforce_limits(user.id)
    pids = await in_db(accessible_project_ids, user)
    if body.project_id and body.project_id not in pids:
        raise HTTPException(403, "You are not a member of this project")
    hits = await rag.search(body.query, pids, project_id=body.project_id, top_k=body.top_k)
    return {"results": [{k: h[k] for k in ("label", "source", "title", "reference", "text", "score")} for h in hits]}


def _ingest(db: Session, user: User, body: DocumentIn):
    ensure_access(db, user, body.project_id, WRITE_ROLES)
    doc, ids = rag.ingest_document(db, user.id, body.project_id, body.title, body.text)
    audit.stage(db, user, "ai.document.add", "ai_document", doc.id, body.project_id, {"chunks": doc.chunk_count})
    audit.commit(db)
    return {"id": doc.id, "project_id": doc.project_id, "title": doc.title, "chunk_count": doc.chunk_count}, ids


@router.post("/documents", status_code=201)
async def add_document(body: DocumentIn, user: User = Depends(user_release_db)):
    """Index a project document (specification, method statement) so answers can cite it. Only members with write roles."""
    out, ids = await in_db_write(_ingest, user, body)
    out["indexed_semantically"] = bool(await rag.embed_pending(ids)) if factory.embeddings_enabled() else False
    return out


def _list_docs(db: Session, user: User, project_id: str | None):
    from sqlalchemy import select
    pids = accessible_project_ids(db, user)
    q = select(AiDocument).where(AiDocument.project_id.in_([project_id] if project_id and project_id in pids else pids))
    return [{"id": d.id, "project_id": d.project_id, "title": d.title, "chunk_count": d.chunk_count, "created_at": d.created_at.isoformat() + "Z"}
            for d in db.scalars(q.order_by(AiDocument.created_at.desc()).limit(100))]


@router.get("/documents")
async def list_documents(project_id: str | None = None, user: User = Depends(user_release_db)):
    return await in_db(_list_docs, user, project_id)


def _delete_doc(db: Session, user: User, doc_id: str):
    d = db.get(AiDocument, doc_id)
    if not d:
        raise HTTPException(404, "Document not found")
    ensure_access(db, user, d.project_id, WRITE_ROLES)
    rag.delete_document(db, doc_id)
    audit.stage(db, user, "ai.document.delete", "ai_document", doc_id, d.project_id, {})
    audit.commit(db)


@router.delete("/documents/{doc_id}")
async def delete_document(doc_id: str, user: User = Depends(user_release_db)):
    await in_db_write(_delete_doc, user, doc_id)
    return {"deleted": True}


# ───────────── explanations & vision ─────────────
@router.get("/tests/{test_id}/explain")
async def explain(test_id: str, request: Request, user: User = Depends(user_release_db), lang: str = Depends(lang_dep)):
    """Plain-language (English/Hindi) explanation of a stored result. The verdict comes from the rules engine, not the model."""
    return await ai.explain_test(caller(request, user, lang), test_id)


@router.post("/read-display", status_code=201)
async def read_display(request: Request, file: UploadFile = File(...), project_id: str = Form(...), cube_size_mm: int = Form(150),
                       batch_id: str | None = Form(None), sample_id: str | None = Form(None), user: User = Depends(current_user),
                       db: Session = Depends(get_db), lang: str = Depends(lang_dep)):
    """Photo of the compression machine display -> draft strength. The photo is stored as tamper-evident evidence; a human confirms the value."""
    if cube_size_mm not in (100, 150):
        raise HTTPException(422, "cube_size_mm must be 100 or 150")

    def save():
        ensure_access(db, user, project_id, TEST_ROLES)
        doc = evidence.save_upload(db, user, file, project_id=project_id, kind="crushing_photo", batch_id=batch_id, sample_id=sample_id)
        audit.commit(db)
        return doc

    doc = await run_in_threadpool(save)
    c = caller(request, user, lang)
    await ai.enforce_limits(user.id)
    t0 = time.perf_counter()
    with open(doc.storage_path, "rb") as fh:
        raw = fh.read()
    obj, usage, st = await generate_structured(DisplayReading, READ_DISPLAY, [
        {"type": "image", "media_type": doc.content_type, "data": base64.b64encode(raw).decode()}, {"type": "text", "text": "Transcribe the display."}], max_tokens=200)
    await ai.record(c, "read_display", usage, (time.perf_counter() - t0) * 1000, None, "ok" if obj else "fallback", None if obj else st)
    reading = None
    note = "AI reading unavailable: enter the value manually. The photo was saved as evidence."
    if obj is not None:
        strength = to_strength_mpa(obj.value, obj.unit, cube_size_mm) if (obj.value and obj.unit and obj.legible) else None
        plausible = strength is not None and 3 <= strength <= 120
        reading = {"raw_value": obj.value, "unit": obj.unit, "specimen_label": obj.specimen_label, "legible": obj.legible, "cube_size_mm": cube_size_mm,
                   "strength_mpa": round(strength, 2) if plausible and strength else None, "plausible": plausible}
        note = ("Draft only: check it against the photo before saving the test." if plausible
                else "The display could not be read reliably or the value is implausible. Enter it manually.")
    return {"document_id": doc.id, "reading": reading, "needs_confirmation": True, "note": note}


# ───────────── usage & observability ─────────────
@router.get("/usage/me")
async def my_usage(user: User = Depends(user_release_db)):
    u = await in_db(store.usage_for_user, user.id)
    used = int(await kv.get_kv().get(f"tok:{user.id}:{time.strftime('%Y-%m-%d', time.gmtime())}") or 0)
    return {**u, "today_tokens": used, "daily_token_budget": settings.ai_daily_token_budget}


@router.get("/usage/summary")
async def usage_summary(days: int = Query(7, ge=1, le=90), _: User = Depends(admin_only)):
    return await in_db(store.usage_summary, days)


@router.get("/metrics")
async def get_metrics(_: User = Depends(admin_only)):
    """Latency (p50/p95), time-to-first-token, tokens, error/timeout/cache rates, retries, fallbacks, RAG retrieval time."""
    return metrics.snapshot()
