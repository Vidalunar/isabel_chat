import os
import pickle
import logging
from typing import List, Dict, Any

import numpy as np
import faiss
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from openai import OpenAI

from settings import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("isabel-chat")

app = FastAPI(title="isabel-chat API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

INDEX_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "storage", f"{settings.index_name}.faiss"))
META_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "storage", "docs.json"))
index = None
meta: List[Dict[str, Any]] = []

if os.path.exists(INDEX_PATH) and os.path.exists(META_PATH):
    index = faiss.read_index(INDEX_PATH)
    import json
    with open(META_PATH, "r", encoding="utf-8") as f:
        meta = json.load(f)
    logger.info("FAISS index y metadatos cargados (%d fragmentos).", len(meta))
else:
    logger.warning("No se encontró el índice. Ejecuta backend/ingest.py antes de /chat.")

client = OpenAI(api_key=settings.openai_api_key)

SYSTEM_PROMPT = """
Eres Isabel I de Castilla (Isabel la Católica). Hablas en primera persona, con tono cortesano y didáctico.
Explica tus decisiones entre 1469 y 1504 con rigor histórico y lenguaje claro.
Termina cada respuesta con una sección titulada 'Fuentes', listando los documentos relevantes.
"""

class ChatRequest(BaseModel):
    query: str
    k: int = 5

class ChatResponse(BaseModel):
    answer: str
    sources: List[Dict[str, Any]]

def retrieve(query: str, k: int = 5) -> List[Dict[str, Any]]:
    if index is None or not meta:
        return []
    emb = client.embeddings.create(model=settings.embedding_model, input=[query]).data[0].embedding
    v = np.array([emb], dtype='float32')
    faiss.normalize_L2(v)
    scores, idxs = index.search(v, min(k, len(meta)))
    out = []
    for score, i in zip(scores[0], idxs[0]):
        if i == -1: continue
        rec = meta[int(i)].copy()
        rec["score"] = float(score)
        out.append(rec)
    return out

def build_prompt(query: str, passages: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    context_lines = [f"[{p['filename']} · pág. {p['page']}]\\n{p['text']}" for p in passages]
    context = "\\n\\n".join(context_lines)
    user_msg = (
        f"Pregunta: {query}\\n\\n"
        f"Contexto recuperado:\\n{context}\\n\\n"
        "Responde en tono pedagógico, en primera persona, como Isabel I de Castilla. "
        "Incluye una sección 'Fuentes' al final con las citas."
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]

@app.get("/health")
async def health():
    return {"status": "ok", "model": settings.model}

@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    passages = retrieve(req.query, k=req.k)
    messages = build_prompt(req.query, passages)
    completion = client.chat.completions.create(
        model=settings.model,
        messages=messages,
        temperature=0.3,
        max_tokens=700,
    )
    answer = completion.choices[0].message.content
    sources = [
        {"filename": p.get("filename"), "page": p.get("page"), "text": (p.get("text") or "")[:500], "score": p.get("score", 0.0)}
        for p in passages
    ]
    return {"answer": answer, "sources": sources}
