# VentasBot · Chatbot RAG sobre Qdrant Cloud + LLM (ACA 2 · Computación en la Nube)

**Especialización en Inteligencia Artificial · CUN** · Docente: Dr. Hamilton Rivera Flor
**Integrantes:** Juan Guillermo Ángel Jaramillo · Luis Bernardo Correa Restrepo · María Alejandra Manga Betancourt
**Pilar:** Ventas: soporte al cliente de comercio electrónico y analítica de productos.

Chatbot RAG (*Retrieval-Augmented Generation*) que responde dudas sobre **derecho de retracto, devoluciones, reversión del pago, garantía legal e información obligatoria en tiendas en línea**, y sobre **productos con alta tasa de devolución**. Cada respuesta se fundamenta en la base vectorial construida en el ACA 1 y cita sus fuentes `[n]`.

## Entregables

- **Notebook ejecutado:** [ACA2_Ventas_RAG_Chatbot.ipynb](ACA2_Ventas_RAG_Chatbot.ipynb)
- **Aplicación:** [app.py](app.py) + [rag_core.py](rag_core.py)
- **Póster A3:** [poster/Poster_ACA2_Ventas_RAG.pdf](poster/Poster_ACA2_Ventas_RAG.pdf)
- **Video de sustentación:** pendiente

## Arquitectura

```
Usuario ──► Streamlit (app.py)
              │ pregunta + historial
              ▼
        rag_core.py ──► SentenceTransformer (paraphrase-multilingual-MiniLM-L12-v2, 384 d)
              │                 │ vector de la consulta
              │                 ▼
              │        Qdrant Cloud · colección ventas_ecommerce_rag (coseno)
              │          ├─ fragmentos normativos: guía SIC, Ley 1480/2011, Ley 2439/2024
              │          └─ 300 resúmenes de productos (UCI Online Retail)
              │                 │ Top-k + umbral
              ▼                 ▼
        Prompt: system prompt restrictivo + CONTEXTO numerado + pregunta
              ▼
        LLM: Gemini API (por defecto) · Groq/GPT-OSS · Claude API · Ollama local
              ▼
        Respuesta con citas [n] + panel de fuentes + latencias
```

## Estructura

| Archivo | Contenido |
|---|---|
| `app.py` | Interfaz Streamlit: chat con historial, gestión documental y explorador semántico. |
| `rag_core.py` | Conexión a Qdrant, recuperación, plantilla de prompt, clientes LLM, ingesta y retiro de documentos. |
| `ACA2_Ventas_RAG_Chatbot.ipynb` | Notebook reproducible: pasos 1–5, pruebas de diálogo con citas y evaluación. |
| `requirements.txt` | Dependencias. |
| `.env.example` / `.streamlit/secrets.toml.example` | Plantillas de credenciales (los archivos reales no se suben). |

## Ejecución local

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows  ·  source .venv/bin/activate en Linux/Mac
pip install -r requirements.txt
copy .env.example .env          # y completa QDRANT_* y GOOGLE_API_KEY (o GROQ_API_KEY)
streamlit run app.py
```

- **Clave de Gemini (gratis):** https://aistudio.google.com/apikey → `GOOGLE_API_KEY`
- **Clave de Groq (gratis):** https://console.groq.com/keys → `GROQ_API_KEY`
- **Sin clave (local):** instala [Ollama](https://ollama.com), ejecuta `ollama pull gemma2:2b` y elige "Ollama local" en la app.

La colección de Qdrant ya existe (ACA 1). Para recrearla, ejecuta el notebook del ACA 1.

## Despliegue en Streamlit Community Cloud

1. Sube este repositorio a GitHub (el `.gitignore` excluye `.env` y `secrets.toml`).
2. En https://share.streamlit.io crea la app apuntando a `app.py`.
3. En *App settings → Secrets* pega el contenido de `.streamlit/secrets.toml.example` con tus valores reales.

## Configuración del LLM e hiperparámetros

| Parámetro | Valor | Justificación |
|---|---|---|
| Modelo | `gemini-3.5-flash` | Capa gratuita, baja latencia, buen español, sin infraestructura (coherente con Qdrant serverless). |
| `temperature` | 0.2 | Respuestas fieles al contexto y reproducibles. |
| `top_p` | 0.9 | Descarta tokens improbables. |
| `max_tokens` | 900 | Respuesta de hasta ~220 palabras con citas. |
| Top-k | 5 normas + 3 productos | Contexto suficiente sin ruido. |
| Umbral coseno | 0.35 | Si ningún fragmento lo supera, no se llama al LLM y se responde "no encuentro esa información". |
| Memoria | 4 turnos | Permite preguntas de seguimiento. |

Todos se pueden ajustar en la barra lateral de la app.

## Mitigación de alucinaciones

1. *System prompt* que obliga a usar solo el CONTEXTO y a citar `[n]`.
2. Respuesta fija «No encuentro esa información en los documentos indexados» cuando no hay evidencia.
3. Umbral de similitud, que es un guardarraíl previo al LLM.
4. Temperatura baja.
5. Panel de fuentes en la UI para verificar cada cita.

## Seguridad de credenciales

- Las claves se leen solo desde variables de entorno (`.env`) o `st.secrets`. Nunca se imprimen ni se escriben en el código.
- `.env` y `.streamlit/secrets.toml` están en `.gitignore`.
- La app solo permite eliminar documentos cargados desde ella misma (`origen = "app"`): el corpus del ACA 1 queda protegido.

## Referencias

- Lewis, P., et al. (2020). Retrieval-augmented generation for knowledge-intensive NLP tasks. *NeurIPS, 33*, 9459–9474. https://arxiv.org/abs/2005.11401
- Qdrant Solutions GmbH. (2024). *Qdrant documentation*. https://qdrant.tech/documentation/
- Streamlit Inc. (2024). *Streamlit documentation*. https://docs.streamlit.io/
- Chen, D. (2015). *Online Retail* [Conjunto de datos]. UCI Machine Learning Repository. https://doi.org/10.24432/C5BW33
- Congreso de la República de Colombia. (2011). *Ley 1480 de 2011*. · (2024). *Ley 2439 de 2024*.
