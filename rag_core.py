"""
rag_core.py — Núcleo del chatbot RAG (ACA 2 · Computación en la Nube · Pilar Ventas · CUN)

Integra:
  1. Recuperación semántica sobre la colección de Qdrant Cloud construida en el ACA 1.
  2. Un LLM configurable (Gemini API, Groq Cloud, Claude API o Ollama local).
  3. Plantilla de prompt que obliga a responder solo con evidencia y a citar fuentes [n].

Las credenciales se leen exclusivamente de variables de entorno (.env local o
st.secrets en Streamlit Cloud). Ninguna clave se imprime ni se guarda en el código.
"""
from __future__ import annotations

import os
import re
import time
import uuid
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv(usecwd=True))
load_dotenv(Path(__file__).with_name(".env"))   # también funciona si se ejecuta desde otra carpeta

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------
COLLECTION = os.getenv("QDRANT_COLLECTION", "ventas_ecommerce_rag").strip()
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "paraphrase-multilingual-MiniLM-L12-v2").strip()

# Proveedores de LLM soportados -> (variable de la API key, modelo por defecto)
PROVEEDORES = {
    "gemini": ("GOOGLE_API_KEY", os.getenv("GEMINI_MODEL", "gemini-3.5-flash")),
    "groq": ("GROQ_API_KEY", os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")),
    "claude": ("ANTHROPIC_API_KEY", os.getenv("CLAUDE_MODEL", "claude-haiku-4-5")),
    "ollama": (None, os.getenv("OLLAMA_MODEL", "gemma2:2b")),
}
GEMINI_RESPALDO = ["gemini-3.5-flash-lite", "gemini-3.8-flash"]   # si el modelo principal está saturado
NOMBRES_PROVEEDOR = {
    "gemini": "Google Gemini API (cloud)",
    "groq": "Groq Cloud · GPT-OSS 120B abierto (cloud)",
    "claude": "Anthropic Claude API (cloud)",
    "ollama": "Ollama local (Gemma / Llama)",
}

# Hiperparámetros por defecto (justificados en el README y el notebook)
DEFAULTS = dict(
    temperature=0.2,     # baja: respuestas deterministas y pegadas al contexto
    max_tokens=900,      # suficiente para una respuesta con 3-5 citas
    top_k_docs=5,        # fragmentos normativos inyectados en el prompt
    top_k_prod=3,        # resúmenes de productos (UCI Online Retail)
    umbral=0.35,         # similitud coseno mínima para usar un fragmento
    turnos_historial=4,  # turnos previos que se envían al LLM como memoria
)

SYSTEM_PROMPT = """Eres «VentasBot», asistente de soporte al cliente y analítica comercial de una tienda en línea que opera en Colombia.
Respondes preguntas sobre derecho de retracto, devoluciones, reversión del pago, garantía legal, información obligatoria en comercio electrónico y sobre el comportamiento de ventas y devoluciones de productos.

REGLAS OBLIGATORIAS:
1. Responde ÚNICAMENTE con la información del CONTEXTO. No uses conocimiento externo ni inventes plazos, artículos, cifras ni productos.
2. Cita cada afirmación con el número del fragmento entre corchetes, por ejemplo [1] o [2][4].
3. Si el CONTEXTO responde la pregunta total o parcialmente, responde con lo que SÍ está respaldado (aunque no cubra cada detalle) y señala brevemente qué parte no aparece en los documentos. Solo si el CONTEXTO no contiene NADA pertinente, responde exactamente: «No encuentro esa información en los documentos indexados.» y sugiere reformular. Nunca completes con suposiciones.
4. Si la pregunta no tiene relación con ventas, consumo o comercio electrónico, indícalo con amabilidad y no la respondas.
5. Responde en español, en tono claro y profesional, con viñetas cuando ayuden. Máximo 220 palabras.
6. Los datos de productos vienen de una tienda del Reino Unido (UCI Online Retail, montos en libras £); acláralo cuando los uses."""

NO_INFO = "No encuentro esa información en los documentos indexados."


def _secret(nombre: str) -> str:
    """Lee una variable de entorno; en Streamlit Cloud cae a st.secrets."""
    val = os.getenv(nombre, "").strip()
    if val:
        return val
    try:
        import streamlit as st  # noqa: WPS433
        return str(st.secrets.get(nombre, "")).strip()
    except Exception:
        return ""


def proveedores_disponibles() -> dict[str, bool]:
    """Indica qué proveedores tienen credencial configurada (sin revelar la clave)."""
    out = {}
    for p, (var, _) in PROVEEDORES.items():
        out[p] = True if var is None else bool(_secret(var))
    return out


# ---------------------------------------------------------------------------
# Conexión a Qdrant Cloud y modelo de embeddings
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def get_client():
    from qdrant_client import QdrantClient

    url, key = _secret("QDRANT_URL"), _secret("QDRANT_API_KEY")
    if not url.startswith("https://") or not key:
        raise RuntimeError("Faltan QDRANT_URL / QDRANT_API_KEY en el .env o en st.secrets.")
    return QdrantClient(url=url, api_key=key, timeout=60)


@lru_cache(maxsize=1)
def get_embedder():
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(EMBEDDING_MODEL)


def estado_coleccion() -> dict:
    """Verifica la conexión y devuelve métricas básicas de la colección."""
    c = get_client()
    t0 = time.time()
    info = c.get_collection(COLLECTION)
    total = c.count(COLLECTION, exact=True).count
    ms = (time.time() - t0) * 1000
    vec = info.config.params.vectors
    return dict(coleccion=COLLECTION, puntos=total, dimension=vec.size,
                metrica=str(vec.distance).split(".")[-1], estado=str(info.status).split(".")[-1],
                latencia_ms=round(ms))


# ---------------------------------------------------------------------------
# Recuperación (PASO 1)
# ---------------------------------------------------------------------------
@dataclass
class Fragmento:
    n: int
    score: float
    texto: str
    payload: dict = field(default_factory=dict)

    @property
    def tipo(self) -> str:
        return self.payload.get("tipo_contenido", "documento")

    @property
    def cita(self) -> str:
        p = self.payload
        if self.tipo == "producto":
            return f"UCI Online Retail · producto {p.get('codigo_producto')} ({p.get('descripcion')})"
        ubic = f"p. {p['pagina']}" if "pagina" in p else f"bloque {p.get('bloque', '?')}"
        return f"{p.get('titulo', p.get('fuente'))} · {ubic}"


def _filtro_tipo(tipo: str):
    from qdrant_client import models

    return models.Filter(must=[models.FieldCondition(key="tipo_contenido", match=models.MatchValue(value=tipo))])


def buscar(consulta: str, k: int = 5, tipo: str | None = None, umbral: float = 0.0, fuente: str | None = None):
    """Top-k fragmentos por similitud coseno. Devuelve (lista de Fragmento, latencia_ms)."""
    from qdrant_client import models

    qv = get_embedder().encode(consulta, normalize_embeddings=True).tolist()
    must = []
    if tipo:
        must.append(models.FieldCondition(key="tipo_contenido", match=models.MatchValue(value=tipo)))
    if fuente:
        must.append(models.FieldCondition(key="fuente", match=models.MatchValue(value=fuente)))
    t0 = time.time()
    res = get_client().query_points(collection_name=COLLECTION, query=qv, limit=k,
                                    query_filter=models.Filter(must=must) if must else None,
                                    with_payload=True, score_threshold=umbral or None)
    ms = (time.time() - t0) * 1000
    frs = [Fragmento(n=i, score=round(p.score, 4), texto=p.payload.get("texto", ""), payload=p.payload)
           for i, p in enumerate(res.points, start=1)]
    return frs, ms


def recuperar_contexto(pregunta: str, k_docs: int, k_prod: int, umbral: float):
    """Recuperación híbrida por tipo: normas (documentos) + productos del dataset."""
    docs, ms1 = buscar(pregunta, k=k_docs, tipo="documento", umbral=umbral)
    prods, ms2 = ([], 0.0)
    if k_prod > 0:
        prods, ms2 = buscar(pregunta, k=k_prod, tipo="producto", umbral=umbral + 0.05)
    frs = sorted(docs + prods, key=lambda f: f.score, reverse=True)
    for i, f in enumerate(frs, start=1):
        f.n = i
    return frs, ms1 + ms2


# ---------------------------------------------------------------------------
# Prompt (PASO 3)
# ---------------------------------------------------------------------------
def construir_contexto(frs: list[Fragmento]) -> str:
    bloques = []
    for f in frs:
        bloques.append(f"[{f.n}] Fuente: {f.cita} (similitud {f.score:.2f})\n{f.texto.strip()}")
    return "\n\n".join(bloques)


def construir_prompt(pregunta: str, frs: list[Fragmento]) -> str:
    return (f"CONTEXTO (fragmentos recuperados de la base vectorial):\n\n{construir_contexto(frs)}\n\n"
            f"PREGUNTA DEL USUARIO: {pregunta}\n\n"
            "Responde siguiendo las reglas y cita los fragmentos usados con [n].")


# ---------------------------------------------------------------------------
# LLM (PASO 2)
# ---------------------------------------------------------------------------
def llamar_llm(proveedor: str, mensajes: list[dict], modelo: str | None = None,
               temperature: float = 0.2, max_tokens: int = 900) -> str:
    """mensajes: [{'role': 'user'|'assistant', 'content': str}, ...] (el system va aparte)."""
    var, modelo_def = PROVEEDORES[proveedor]
    modelo = modelo or modelo_def
    key = _secret(var) if var else ""
    if var and not key:
        raise RuntimeError(f"Falta {var} en el .env / st.secrets para usar {NOMBRES_PROVEEDOR[proveedor]}.")

    if proveedor == "gemini":
        from google import genai
        from google.genai import types

        from google.genai import errors as gerr

        cli = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=45_000))  # 45 s
        contents = [types.Content(role="model" if m["role"] == "assistant" else "user",
                                  parts=[types.Part(text=m["content"])]) for m in mensajes]
        # Los modelos Gemini 3.x "piensan" antes de responder: ese razonamiento consume tokens de salida,
        # por eso se suma un margen al presupuesto visible. Ante saturación (503/429) se reintenta
        # con espera exponencial y luego se pasa a un modelo de respaldo.
        cfg = types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT, temperature=temperature,
                                          max_output_tokens=max_tokens + 2048, top_p=0.9)
        ultimo = None
        for mdl in dict.fromkeys([modelo] + GEMINI_RESPALDO):
            for intento in range(3):
                try:
                    r = cli.models.generate_content(model=mdl, contents=contents, config=cfg)
                    return (r.text or "").strip()
                except gerr.APIError as e:
                    ultimo = e
                    if getattr(e, "code", None) not in (429, 500, 503, 504):
                        raise
                    time.sleep(2 ** intento)
        raise RuntimeError(f"Gemini no disponible tras reintentos: {ultimo}")

    if proveedor == "groq":
        from groq import Groq

        extra = {"reasoning_effort": "low"} if "gpt-oss" in modelo else {}
        r = Groq(api_key=key).chat.completions.create(
            model=modelo, temperature=temperature, max_tokens=max_tokens + 1024, top_p=0.9,
            messages=[{"role": "system", "content": SYSTEM_PROMPT}] + mensajes, **extra)
        return (r.choices[0].message.content or "").strip()

    if proveedor == "claude":
        import anthropic

        r = anthropic.Anthropic(api_key=key).messages.create(
            model=modelo, system=SYSTEM_PROMPT, messages=mensajes,
            temperature=temperature, max_tokens=max_tokens)
        return "".join(b.text for b in r.content if b.type == "text").strip()

    if proveedor == "ollama":
        import requests

        host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
        r = requests.post(f"{host}/api/chat", timeout=180, json={
            "model": modelo, "stream": False,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + mensajes,
            "options": {"temperature": temperature, "num_predict": max_tokens, "top_p": 0.9}})
        r.raise_for_status()
        return r.json()["message"]["content"].strip()

    raise ValueError(f"Proveedor desconocido: {proveedor}")


# ---------------------------------------------------------------------------
# Pipeline RAG completo
# ---------------------------------------------------------------------------
def responder(pregunta: str, historial: list[dict] | None = None, proveedor: str = "gemini",
              modelo: str | None = None, **kw) -> dict:
    """Ejecuta recuperación + generación. Devuelve respuesta, fragmentos y latencias."""
    cfg = {**DEFAULTS, **kw}
    historial = historial or []

    frs, ms_ret = recuperar_contexto(pregunta, cfg["top_k_docs"], cfg["top_k_prod"], cfg["umbral"])

    # Guardarraíl: sin evidencia suficiente no se llama al LLM (evita alucinaciones y ahorra cuota)
    if not frs:
        return dict(respuesta=NO_INFO + " Prueba a reformular la pregunta con otras palabras.",
                    fragmentos=[], ms_recuperacion=round(ms_ret), ms_llm=0, proveedor=proveedor,
                    modelo=modelo or PROVEEDORES[proveedor][1], citas_usadas=[])

    memoria = [{"role": m["role"], "content": m["content"]} for m in historial[-2 * cfg["turnos_historial"]:]]
    mensajes = memoria + [{"role": "user", "content": construir_prompt(pregunta, frs)}]

    t0 = time.time()
    texto = llamar_llm(proveedor, mensajes, modelo, cfg["temperature"], cfg["max_tokens"])
    ms_llm = (time.time() - t0) * 1000

    usadas = sorted({int(n) for n in re.findall(r"\[(\d+)\]", texto) if 0 < int(n) <= len(frs)})
    return dict(respuesta=texto, fragmentos=frs, ms_recuperacion=round(ms_ret), ms_llm=round(ms_llm),
                proveedor=proveedor, modelo=modelo or PROVEEDORES[proveedor][1], citas_usadas=usadas)


# ---------------------------------------------------------------------------
# Gestión documental (inventario e ingesta de nuevos documentos)
# ---------------------------------------------------------------------------
def inventario(limite: int = 5000) -> list[dict]:
    """Recorre la colección (scroll) y devuelve el payload de cada punto (sin vectores)."""
    c, out, offset = get_client(), [], None
    while len(out) < limite:
        pts, offset = c.scroll(COLLECTION, limit=256, offset=offset, with_payload=True, with_vectors=False)
        out.extend({"id": str(p.id), **p.payload} for p in pts)
        if offset is None:
            break
    return out


def ingestar_documento(nombre: str, contenido: bytes, titulo: str, entidad: str, anio: int,
                       chunk_size: int = 500, overlap: int = 75) -> int:
    """Extrae texto de un PDF/TXT/MD/DOCX, lo fragmenta, lo vectoriza y lo sube a Qdrant.
    Usa el mismo esquema de payload del ACA 1 y marca origen='app' para poder eliminarlo."""
    import io
    from qdrant_client import models

    ext = Path(nombre).suffix.lower()
    unidades = []  # (clave, n, texto)
    if ext == ".pdf":
        from pypdf import PdfReader
        for i, pg in enumerate(PdfReader(io.BytesIO(contenido)).pages, start=1):
            unidades.append(("pagina", i, pg.extract_text() or ""))
    elif ext == ".docx":
        from docx import Document
        texto = "\n".join(p.text for p in Document(io.BytesIO(contenido)).paragraphs if p.text.strip())
        unidades.append(("bloque", 1, texto))
    else:
        unidades.append(("bloque", 1, contenido.decode("utf-8", errors="ignore")))

    frags = []
    for clave, n, texto in unidades:
        texto = re.sub(r"[ \t]+", " ", re.sub(r"-\n(?=\w)", "", texto)).strip()
        ini, k = 0, 0
        while ini < len(texto):
            fin = min(len(texto), ini + chunk_size)
            if fin < len(texto):
                corte = max(texto.rfind(". ", ini, fin), texto.rfind("\n", ini, fin))
                fin = corte + 1 if corte > ini + chunk_size // 2 else fin
            frag = texto[ini:fin].strip()
            if len(frag) >= 80:
                frags.append((clave, n, k, frag)); k += 1
            if fin >= len(texto):
                break
            ini = max(fin - overlap, ini + 1)
    if not frags:
        return 0

    vecs = get_embedder().encode([f[3] for f in frags], batch_size=64, normalize_embeddings=True)
    puntos = []
    for (clave, n, k, frag), v in zip(frags, vecs):
        payload = {"tipo_contenido": "documento", "fuente": nombre, "titulo": titulo, "entidad": entidad,
                   "anio": int(anio), "audiencia": "general", "tipo_documento": "documento_cargado_app",
                   clave: n, "chunk_en_pagina": k, "texto": frag, "origen": "app"}
        pid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{nombre}|{n}|{k}"))
        puntos.append(models.PointStruct(id=pid, vector=v.tolist(), payload=payload))
    for i in range(0, len(puntos), 64):
        get_client().upsert(COLLECTION, points=puntos[i:i + 64], wait=True)
    return len(puntos)


def eliminar_documento(nombre: str) -> None:
    """Elimina SOLO documentos cargados desde la app (origen='app'); protege el corpus del ACA 1."""
    from qdrant_client import models

    get_client().delete(COLLECTION, points_selector=models.FilterSelector(filter=models.Filter(must=[
        models.FieldCondition(key="fuente", match=models.MatchValue(value=nombre)),
        models.FieldCondition(key="origen", match=models.MatchValue(value="app")),
    ])), wait=True)
