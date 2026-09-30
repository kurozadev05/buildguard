"""Database access for conversations, messages and usage (sync; called through in_db)."""
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..models import AiConversation, AiMessage, AiUsage, User, utcnow

MAX_MESSAGES_PER_CONVERSATION = 200
MAX_CONVERSATIONS_PER_USER = 200


def owned_conversation(db: Session, user_id: str, conv_id: str) -> AiConversation:
    """404 (not 403) for someone else's conversation so existence is not revealed. Admins get no special access."""
    c = db.scalar(select(AiConversation).where(AiConversation.id == conv_id, AiConversation.user_id == user_id))
    if not c:
        raise HTTPException(404, "Conversation not found")
    return c


def start_turn(db: Session, user_id: str, conv_id: str | None, project_id: str | None, text: str) -> tuple[str, list[dict]]:
    """Create/load the conversation, store the user's message, return (conversation_id, prior messages as role/content dicts)."""
    if conv_id:
        conv = owned_conversation(db, user_id, conv_id)
        if conv.message_count >= MAX_MESSAGES_PER_CONVERSATION:
            raise HTTPException(409, "Conversation is full; start a new one")
    else:
        n = db.scalar(select(func.count(AiConversation.id)).where(AiConversation.user_id == user_id)) or 0
        if n >= MAX_CONVERSATIONS_PER_USER:
            raise HTTPException(409, "Too many conversations; delete some first")
        conv = AiConversation(user_id=user_id, project_id=project_id, title=text[:60])
        db.add(conv)
        db.flush()
    prior = db.scalars(select(AiMessage).where(AiMessage.conversation_id == conv.id).order_by(AiMessage.created_at.desc()).limit(40)).all()
    history = [{"role": m.role, "content": m.content} for m in reversed(prior)]
    db.add(AiMessage(conversation_id=conv.id, role="user", content=text))
    conv.message_count += 1
    conv.updated_at = utcnow()
    db.commit()
    return conv.id, history


def add_assistant(db: Session, conv_id: str, text: str, meta: dict) -> str:
    m = AiMessage(conversation_id=conv_id, role="assistant", content=text, meta=meta)
    db.add(m)
    c = db.get(AiConversation, conv_id)
    if c:
        c.message_count += 1
        c.updated_at = utcnow()
    db.commit()
    return m.id


def list_conversations(db: Session, user_id: str, limit: int, offset: int) -> list[dict]:
    rows = db.scalars(select(AiConversation).where(AiConversation.user_id == user_id).order_by(AiConversation.updated_at.desc()).limit(limit).offset(offset))
    return [{"id": c.id, "title": c.title, "project_id": c.project_id, "message_count": c.message_count, "updated_at": c.updated_at.isoformat() + "Z"} for c in rows]


def get_conversation(db: Session, user_id: str, conv_id: str) -> dict:
    c = owned_conversation(db, user_id, conv_id)
    msgs = db.scalars(select(AiMessage).where(AiMessage.conversation_id == c.id).order_by(AiMessage.created_at)).all()
    return {"id": c.id, "title": c.title, "project_id": c.project_id,
            "messages": [{"id": m.id, "role": m.role, "content": m.content, "meta": m.meta, "created_at": m.created_at.isoformat() + "Z"} for m in msgs]}


def delete_conversation(db: Session, user_id: str, conv_id: str) -> None:
    c = owned_conversation(db, user_id, conv_id)
    db.execute(delete(AiMessage).where(AiMessage.conversation_id == c.id))
    db.delete(c)
    db.commit()


def record_usage(db: Session, **f) -> None:
    db.add(AiUsage(**f))
    db.commit()


def usage_for_user(db: Session, user_id: str) -> dict:
    def agg(since):
        r = db.execute(select(func.count(AiUsage.id), func.coalesce(func.sum(AiUsage.input_tokens), 0), func.coalesce(func.sum(AiUsage.output_tokens), 0),
                              func.coalesce(func.sum(AiUsage.estimated_cost), 0.0)).where(AiUsage.user_id == user_id, AiUsage.created_at >= since)).one()
        return {"requests": r[0], "input_tokens": int(r[1]), "output_tokens": int(r[2]), "estimated_cost": round(float(r[3]), 6)}
    now = utcnow()
    return {"last_24h": agg(now - timedelta(hours=24)), "last_30d": agg(now - timedelta(days=30))}


def usage_summary(db: Session, days: int = 7) -> dict:
    since = utcnow() - timedelta(days=days)
    rows = db.execute(select(AiUsage.user_id, func.count(AiUsage.id), func.coalesce(func.sum(AiUsage.input_tokens + AiUsage.output_tokens), 0),
                             func.coalesce(func.sum(AiUsage.estimated_cost), 0.0)).where(AiUsage.created_at >= since)
                      .group_by(AiUsage.user_id).order_by(func.sum(AiUsage.input_tokens + AiUsage.output_tokens).desc()).limit(20)).all()
    by_status = dict(db.execute(select(AiUsage.status, func.count(AiUsage.id)).where(AiUsage.created_at >= since).group_by(AiUsage.status)).all())
    names = {u.id: u.email for u in db.scalars(select(User).where(User.id.in_([r[0] for r in rows if r[0]])))}
    return {"days": days, "by_status": by_status,
            "top_users": [{"user": names.get(r[0] or "", r[0]), "requests": r[1], "tokens": int(r[2]), "estimated_cost": round(float(r[3]), 6)} for r in rows]}
