"""Retrieval-Augmented Generation over (a) the shared verified IS-rule notes and (b) tenant-scoped project documents.

Pipeline: documents -> chunking -> (optional) embedding -> stored with metadata -> hybrid search
(keyword + cosine similarity, metadata-filtered by the caller's accessible projects) -> ranking -> token-budgeted context.
Works with no embedding provider (keyword only). Embeddings are stored as JSON and compared in Python: fine up to a few
thousand chunks; move to PostgreSQL + pgvector beyond that (the table already carries model/dims/config metadata for it)."""
import asyncio
import hashlib
import json
import logging
import math
import re
import time
from collections import Counter

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import AiChunk, AiDocument
from ..services.advisor import _tokens
from ..services.rules_engine import RULES
from . import factory, kv
from .base import ProviderError
from .dbutil import in_db, in_db_write
from .grounding import clean_text
from .metrics import metrics
from .tokens import estimate_tokens

log = logging.getLogger("buildguard.ai.rag")
CHUNK_CONFIG = "p800o100"
MIN_COSINE = 0.30
MIN_COVERAGE = 0.30
_embed_lock = asyncio.Lock()
_embed_backoff_until = 0.0


# ───────────── chunking ─────────────
def chunk_text(text: str, size: int = 800, overlap: int = 100) -> list[str]:
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    pieces: list[str] = []
    for p in paras:
        while len(p) > size:
            cut = p.rfind(" ", 0, size)
            cut = cut if cut > size // 2 else size
            pieces.append(p[:cut].strip())
            p = p[cut:].strip()
        if p:
            pieces.append(p)
    chunks, cur = [], ""
    for p in pieces:
        if not cur:
            cur = p
        elif len(cur) + len(p) + 2 <= size:
            cur += "\n\n" + p
        else:
            chunks.append(cur)
            cur = (cur[-overlap:].split(" ", 1)[-1] + "\n\n" + p) if overlap else p
    if cur:
        chunks.append(cur)
    return chunks


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


# ───────────── corpus maintenance (sync DB) ─────────────
def sync_knowledge(db: Session) -> int:
    """Make ai_chunks mirror the rule pack's knowledge notes (startup; DB only, no network)."""
    notes = {k["id"]: k for k in RULES["knowledge"]}
    existing = {c.doc_id: c for c in db.scalars(select(AiChunk).where(AiChunk.source == "knowledge"))}
    changed = 0
    for did, k in notes.items():
        text = f"{k['title']}. {k['text']}"
        c = existing.get(did)
        if c and c.content_hash == _h(text) and c.reference == k["reference"]:
            continue
        if c is None:
            c = AiChunk(source="knowledge", doc_id=did, title=k["title"], reference=k["reference"], text=text, content_hash=_h(text), chunk_config=CHUNK_CONFIG)
            db.add(c)
        else:
            c.title, c.reference, c.text, c.content_hash, c.embedding, c.embedding_model, c.embedding_dims = k["title"], k["reference"], text, _h(text), None, None, None
        changed += 1
    for did, c in existing.items():
        if did not in notes:
            db.delete(c)
            changed += 1
    db.commit()
    return changed


def ingest_document(db: Session, user_id: str, project_id: str, title: str, text: str) -> tuple[AiDocument, list[str]]:
    text = clean_text_keep_newlines(text)
    if len(text.encode()) > settings.ai_max_document_kb * 1024:
        raise HTTPException(413, f"Document larger than {settings.ai_max_document_kb} KB")
    if len(text.strip()) < 20:
        raise HTTPException(422, "Document is empty or too short")
    ch = _h(text)
    if db.scalar(select(AiDocument.id).where(AiDocument.project_id == project_id, AiDocument.content_hash == ch)):
        raise HTTPException(409, "This document is already indexed for the project")
    pieces = chunk_text(text)
    doc = AiDocument(project_id=project_id, title=clean_text(title, 200), content_hash=ch, chunk_count=len(pieces), created_by=user_id)
    db.add(doc)
    db.flush()
    ids = []
    for i, p in enumerate(pieces):
        c = AiChunk(source="project_doc", doc_id=doc.id, project_id=project_id, chunk_index=i, title=doc.title, text=p, content_hash=_h(p), chunk_config=CHUNK_CONFIG)
        db.add(c)
        db.flush()
        ids.append(c.id)
    db.commit()
    return doc, ids


def clean_text_keep_newlines(s: str) -> str:
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", s)


def delete_document(db: Session, doc_id: str) -> None:
    db.execute(delete(AiChunk).where(AiChunk.doc_id == doc_id, AiChunk.source == "project_doc"))
    db.execute(delete(AiDocument).where(AiDocument.id == doc_id))
    db.commit()


# ───────────── embeddings ─────────────
def _pending_rows(db: Session, ids: list[str] | None, model_id: str) -> list[tuple[str, str]]:
    q = select(AiChunk.id, AiChunk.text, AiChunk.embedding_model).where((AiChunk.embedding == None) | (AiChunk.embedding_model != model_id))    # noqa: E711
    if ids is not None:
        q = q.where(AiChunk.id.in_(ids))
    return [(r[0], r[1]) for r in db.execute(q.limit(400))]


def _store_embeddings(db: Session, vecs: dict[str, list[float]], model_id: str) -> None:
    for cid, v in vecs.items():
        c = db.get(AiChunk, cid)
        if c:
            c.embedding, c.embedding_model, c.embedding_dims = v, model_id, len(v)
    db.commit()


async def embed_pending(ids: list[str] | None = None) -> int:
    """Embed chunks that lack an embedding for the current model. Best effort: on provider failure we back off and stay keyword-only."""
    global _embed_backoff_until
    if not factory.embeddings_enabled() or time.monotonic() < _embed_backoff_until:
        return 0
    model_id = factory.embedding_model_id()
    async with _embed_lock:
        pending = await in_db(_pending_rows, ids, model_id)
        done = 0
        for i in range(0, len(pending), 64):
            batch = pending[i:i + 64]
            try:
                vecs = await factory.get_provider().embed([t for _, t in batch])                         # type: ignore[union-attr]
            except ProviderError as e:
                log.warning("embedding failed (%s); keyword-only for 60s", e.kind)
                _embed_backoff_until = time.monotonic() + 60
                return done
            await in_db_write(_store_embeddings, {cid: v for (cid, _), v in zip(batch, vecs, strict=True)}, model_id)
            done += len(batch)
        return done


async def _query_vec(query: str) -> list[float] | None:
    if not factory.embeddings_enabled() or time.monotonic() < _embed_backoff_until:
        return None
    key = f"qemb:{factory.embedding_model_id()}:{_h(query)}"
    store = kv.get_kv()
    hit = await store.get(key)
    if hit:
        metrics.inc("cache_hit")
        return json.loads(hit)
    metrics.inc("cache_miss")
    try:
        v = (await factory.get_provider().embed([query]))[0]                                             # type: ignore[union-attr]
    except ProviderError as e:
        log.warning("query embedding failed (%s); keyword-only", e.kind)
        return None
    await store.set(key, json.dumps(v), settings.ai_cache_ttl_s)
    return v


def _cos(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b, strict=True)) / (na * nb) if na and nb else 0.0


# ───────────── search ─────────────
def _load(db: Session, allowed: list[str], project_id: str | None, model_id: str | None) -> list[dict]:
    q = select(AiChunk)
    scope = (AiChunk.source == "knowledge")
    if project_id:
        if project_id not in allowed:
            return [_row(c, model_id) for c in db.scalars(q.where(scope))]                   # no access to that project: knowledge only
        scope = scope | (AiChunk.project_id == project_id)
    elif allowed:
        scope = scope | AiChunk.project_id.in_(allowed)
    return [_row(c, model_id) for c in db.scalars(q.where(scope))]


def _row(c: AiChunk, model_id: str | None) -> dict:
    return {"id": c.id, "doc_id": c.doc_id, "source": c.source, "project_id": c.project_id, "title": c.title, "reference": c.reference,
            "text": c.text, "emb": c.embedding if (model_id and c.embedding_model == model_id) else None}


async def search(query: str, allowed_project_ids: list[str], *, project_id: str | None = None, top_k: int = 4, token_budget: int | None = None) -> list[dict]:
    """Hybrid, access-filtered retrieval. `allowed_project_ids` is the caller's authorization: project chunks outside it are never loaded."""
    t0 = time.perf_counter()
    use_emb = factory.embeddings_enabled()
    model_id = factory.embedding_model_id() if use_emb else None
    if use_emb:
        await embed_pending()
    rows = await in_db(_load, allowed_project_ids, project_id, model_id)
    qt = _tokens(query)
    qv = await _query_vec(query) if use_emb else None
    # keyword side: IDF-weighted coverage of the query's words (rare words like "cure" count more than "concrete"; title hits count 1.5x)
    row_tokens = [_tokens(r["title"] + " " + r["text"]) for r in rows]
    title_tokens = [_tokens(r["title"]) for r in rows]
    n = max(1, len(rows))
    df = Counter(t for ts in row_tokens for t in ts)
    idf = {t: (math.log(1 + n / df[t]) if df.get(t) else 0.35 * math.log(1 + n)) for t in qt}      # words absent from the corpus can't help: down-weight them
    total = sum(idf.values()) or 1.0
    scored = []
    for i, r in enumerate(rows):
        cov = min(1.0, sum(idf[t] * (1.5 if t in title_tokens[i] else 1.0) for t in qt & row_tokens[i]) / total)
        cos = _cos(qv, r["emb"]) if (qv and r["emb"]) else None
        if cos is not None:
            if cos < MIN_COSINE and cov < MIN_COVERAGE:
                continue
            score = 0.6 * cos + 0.4 * cov
        else:
            if cov < MIN_COVERAGE:
                continue
            score = cov
        scored.append((score, r))
    scored.sort(key=lambda t: -t[0])
    if scored:
        scored = [t for t in scored if t[0] >= 0.6 * scored[0][0]]          # drop the weak tail: irrelevant context costs tokens and invites hallucination
    out: list[dict] = []
    used, d = 0, 0
    for score, r in scored:
        if len(out) >= top_k:
            break
        text = r["text"][:900]
        cost = estimate_tokens(text)
        if token_budget is not None and used + cost > token_budget and out:
            break
        used += cost
        if r["source"] == "knowledge":
            label = r["doc_id"]
        else:
            d += 1
            label = f"D{d}"
        out.append({"label": label, "id": r["id"], "source": r["source"], "project_id": r["project_id"], "title": r["title"],
                    "reference": r["reference"], "text": text, "score": round(score, 3)})
    metrics.observe("retrieval", (time.perf_counter() - t0) * 1000)
    return out


def corpus_version(db: Session) -> str:
    """Changes whenever the shared knowledge notes change; part of cache keys so stale answers are never served."""
    return _h(",".join(sorted(f"{c.doc_id}:{c.content_hash}" for c in db.scalars(select(AiChunk).where(AiChunk.source == "knowledge")))))[:12]
