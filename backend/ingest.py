"""
Ingesta de fuentes (PDF y DOCX) -> índice FAISS + metadatos JSON.

- Extracción robusta:
  * PDF con pypdf
  * DOCX con python-docx
- Normalización de texto:
  * Quitar guiones de final de línea
  * Unificar saltos de línea y espacios (incl. no-break spaces)
- Chunking semántico por tokens (~900) con solape (150)
- Metadatos por fragmento: título, año, páginas, fuente (filename)
- Validación: lista de archivos fallidos con causa
- Salida:
  * storage/index.faiss
  * storage/docs.json

Uso:
  python backend/ingest.py
"""

from __future__ import annotations

import os
import re
import json
import glob
import math
import traceback
from dataclasses import dataclass
from typing import List, Dict, Any, Optional, Tuple

import numpy as np
import faiss
from tqdm import tqdm
from pypdf import PdfReader
from docx import Document
import tiktoken
from openai import OpenAI

# Ajusta rutas base
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_DIR = os.path.join(ROOT_DIR, "data")
STORAGE_DIR = os.path.join(ROOT_DIR, "storage")

FAISS_PATH = os.path.join(STORAGE_DIR, "index.faiss")
DOCS_JSON = os.path.join(STORAGE_DIR, "docs.json")

# Tokenización (modelo de embeddings)
ENC = tiktoken.get_encoding("cl100k_base")

# Parámetros de chunking
CHUNK_TOKENS = 900
OVERLAP_TOKENS = 150

# Modelo de embeddings
DEFAULT_EMBED_MODEL = "text-embedding-3-small"

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# ---------------------------
# Normalización de texto
# ---------------------------

NBSP_RE = re.compile(r"\u00A0")
MULTISPACE_RE = re.compile(r"[ \t]{2,}")
LINE_HYPHEN_RE = re.compile(r"(\w)-\n(\w)")
CRLF_RE = re.compile(r"\r\n?")
MULTI_NL_RE = re.compile(r"\n{3,}")

def clean_text(s: str) -> str:
    if not s:
        return ""
    s = CRLF_RE.sub("\n", s)
    s = LINE_HYPHEN_RE.sub(r"\1\2", s)
    s = NBSP_RE.sub(" ", s)
    s = s.replace("\u2009", " ").replace("\u2002", " ").replace("\u2003", " ")
    s = MULTISPACE_RE.sub(" ", s)
    s = MULTI_NL_RE.sub("\n\n", s)
    return s.strip()

# ---------------------------
# Extracción de contenido
# ---------------------------

@dataclass
class DocMeta:
    filename: str
    source_path: str
    title: Optional[str]
    year: Optional[int]
    pages_total: Optional[int]
    filetype: str  # 'pdf' | 'docx'

def guess_title_year_from_filename(filename: str) -> Tuple[Optional[str], Optional[int]]:
    name, _ = os.path.splitext(os.path.basename(filename))
    m_year = re.search(r"(1[0-9]{3}|20[0-9]{2})", name)
    year = int(m_year.group(1)) if m_year else None
    title = re.sub(r"[_\\-]+", " ", name).strip()
    if m_year:
        idx = m_year.end()
        rest = title[idx:].strip(" -_")
        if rest:
            title = rest
    if title:
        title = re.sub(r"\s+", " ", title).strip().title()
    return title or None, year

def extract_pdf(path: str) -> Tuple[str, DocMeta, List[Tuple[int, str]]]:
    reader = PdfReader(path)
    pages_text: List[Tuple[int, str]] = []
    for i, page in enumerate(reader.pages, start=1):
        try:
            t = page.extract_text() or ""
        except Exception:
            t = ""
        pages_text.append((i, t))

    raw_title = None
    year = None
    try:
        meta = reader.metadata or {}
        raw_title = meta.get("/Title") if isinstance(meta, dict) else None
        raw_date = meta.get("/CreationDate") if isinstance(meta, dict) else None
        if raw_date:
            m = re.search(r"(\d{4})", str(raw_date))
            if m:
                year = int(m.group(1))
    except Exception:
        pass

    fallback_title, fallback_year = guess_title_year_from_filename(path)
    title = raw_title or fallback_title
    if year is None:
        year = fallback_year

    meta_doc = DocMeta(
        filename=os.path.basename(path),
        source_path=os.path.abspath(path),
        title=title,
        year=year,
        pages_total=len(pages_text),
        filetype="pdf",
    )
    return "pdf", meta_doc, pages_text

def extract_docx(path: str) -> Tuple[str, DocMeta, List[Tuple[int, str]]]:
    doc = Document(path)
    paras = [p.text or "" for p in doc.paragraphs]
    full = "\n".join(paras)
    title, year = guess_title_year_from_filename(path)
    meta_doc = DocMeta(
        filename=os.path.basename(path),
        source_path=os.path.abspath(path),
        title=title,
        year=year,
        pages_total=1,
        filetype="docx",
    )
    return "docx", meta_doc, [(1, full)]

# ---------------------------
# Chunking semántico
# ---------------------------

SENT_SPLIT_RE = re.compile(r"(?<=[\.\?\!…])\s+(?=[A-ZÁÉÍÓÚÜÑ0-9])")

def token_len(text: str) -> int:
    return len(ENC.encode(text))

def sentences_from_text(text: str) -> List[str]:
    text = clean_text(text)
    parts = SENT_SPLIT_RE.split(text)
    out: List[str] = []
    for part in parts:
        if token_len(part) > CHUNK_TOKENS * 1.5:
            out.extend([p for p in part.split("\n\n") if p.strip()])
        else:
            if part.strip():
                out.append(part)
    return out

def chunk_by_tokens(text: str, chunk_tokens: int = CHUNK_TOKENS, overlap_tokens: int = OVERLAP_TOKENS) -> List[str]:
    sents = sentences_from_text(text)
    sent_tokens = [token_len(s) for s in sents]
    chunks: List[str] = []
    i = 0
    n = len(sents)
    while i < n:
        cur_tokens = 0
        j = i
        while j < n and (cur_tokens + sent_tokens[j]) <= chunk_tokens:
            cur_tokens += sent_tokens[j]
            j += 1
        if j == i:
            toks = ENC.encode(sents[j])
            slice_text = ENC.decode(toks[:chunk_tokens])
            chunks.append(slice_text.strip())
            i += 1
        else:
            chunk_text = " ".join(sents[i:j]).strip()
            if chunk_text:
                chunks.append(chunk_text)
            if j >= n: break
            back_toks = 0
            k = j - 1
            while k >= i and back_toks < overlap_tokens:
                back_toks += sent_tokens[k]
                k -= 1
            i = max(k + 1, i + 1)
    return [c for c in chunks if c.strip()]

# ---------------------------
# Embeddings y FAISS
# ---------------------------

def embed_texts(client: OpenAI, texts: List[str], model: str = DEFAULT_EMBED_MODEL, batch_size: int = 100) -> np.ndarray:
    vecs: List[List[float]] = []
    for i in tqdm(range(0, len(texts), batch_size), desc="Embeddings"):
        batch = texts[i:i + batch_size]
        resp = client.embeddings.create(model=model, input=batch)
        vecs.extend([d.embedding for d in resp.data])
    arr = np.array(vecs, dtype="float32")
    faiss.normalize_L2(arr)
    return arr

# ---------------------------
# Pipeline principal
# ---------------------------

def main() -> None:
    if not OPENAI_API_KEY:
        raise SystemExit("❌ ERROR: OPENAI_API_KEY no está definido.")

    os.makedirs(STORAGE_DIR, exist_ok=True)
    files = sorted(glob.glob(os.path.join(DATA_DIR, "*.pdf")) + glob.glob(os.path.join(DATA_DIR, "*.docx")))
    if not files:
        raise SystemExit("⚠️ No se encontraron archivos PDF o DOCX en /data.")

    client = OpenAI(api_key=OPENAI_API_KEY)
    all_records: List[Dict[str, Any]] = []
    failed: List[Dict[str, str]] = []

    for path in tqdm(files, desc="Extrayendo documentos"):
        try:
            ext = os.path.splitext(path)[1].lower()
            if ext == ".pdf":
                _, meta_doc, pages = extract_pdf(path)
            elif ext == ".docx":
                _, meta_doc, pages = extract_docx(path)
            else:
                continue

            for page_num, raw_text in pages:
                cleaned = clean_text(raw_text)
                if not cleaned:
                    continue
                chunks = chunk_by_tokens(cleaned, CHUNK_TOKENS, OVERLAP_TOKENS)
                for ch in chunks:
                    all_records.append({
                        "text": ch,
                        "filename": meta_doc.filename,
                        "source": meta_doc.source_path,
                        "title": meta_doc.title,
                        "year": meta_doc.year,
                        "page": page_num,
                        "pages_total": meta_doc.pages_total,
                        "filetype": meta_doc.filetype,
                    })
        except Exception as e:
            failed.append({"file": path, "error": str(e)})
            traceback.print_exc()

    if not all_records:
        raise SystemExit("❌ No se generaron fragmentos. Revisa los documentos.")

    corpus = [r["text"] for r in all_records]
    vectors = embed_texts(client, corpus, model=DEFAULT_EMBED_MODEL, batch_size=100)

    dim = vectors.shape[1]
    index = faiss.IndexFlatIP(dim)
    index.add(vectors)

    faiss.write_index(index, FAISS_PATH)
    with open(DOCS_JSON, "w", encoding="utf-8") as f:
        json.dump(all_records, f, ensure_ascii=False, indent=2)

    print(f"✅ Índice guardado en: {FAISS_PATH}")
    print(f"✅ Metadatos guardados en: {DOCS_JSON}")
    print(f"📦 Fragmentos: {len(all_records)} | Archivos procesados: {len(files)}")
    if failed:
        print("⚠️ Archivos con error:")
        for f in failed:
            print(f"  - {f['file']}: {f['error']}")
    else:
        print("✅ Sin fallos de extracción.")

if __name__ == "__main__":
    main()
