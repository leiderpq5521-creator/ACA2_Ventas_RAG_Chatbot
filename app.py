"""
VentasBot · Chatbot RAG de soporte y analítica comercial (ACA 2 · Computación en la Nube · CUN)
Ejecutar:  streamlit run app.py
"""
import pandas as pd
import streamlit as st

import rag_core as rc

st.set_page_config(page_title="VentasBot · RAG Ventas", page_icon=":material/storefront:", layout="wide")

# ---------------------------------------------------------------------------
# Recursos cacheados (cliente Qdrant y modelo de embeddings se crean una sola vez)
# ---------------------------------------------------------------------------
@st.cache_resource(show_spinner="Conectando con Qdrant Cloud y cargando embeddings…")
def iniciar():
    rc.get_client()
    rc.get_embedder()
    return True


@st.cache_data(ttl=300, show_spinner=False)
def estado():
    return rc.estado_coleccion()


@st.cache_data(ttl=300, show_spinner="Leyendo el inventario de la colección…")
def inventario_df():
    return pd.DataFrame(rc.inventario())


try:
    iniciar()
    est = estado()
except Exception as e:  # noqa: BLE001
    st.error(f"No fue posible conectar con la base vectorial: {e}", title="Error de conexión")
    st.stop()

if "mensajes" not in st.session_state:
    st.session_state.mensajes = []  # {'role', 'content', 'fragmentos', 'meta'}

# ---------------------------------------------------------------------------
# Barra lateral: configuración del LLM y de la recuperación
# ---------------------------------------------------------------------------
disp = rc.proveedores_disponibles()
with st.sidebar:
    st.header(":material/tune: Configuración")
    opciones = [p for p in rc.PROVEEDORES if disp[p]] or list(rc.PROVEEDORES)
    proveedor = st.selectbox("Modelo de lenguaje", opciones, format_func=lambda p: rc.NOMBRES_PROVEEDOR[p])
    modelo = st.text_input("Modelo", value=rc.PROVEEDORES[proveedor][1])
    if not disp[proveedor]:
        st.warning(f"Agrega {rc.PROVEEDORES[proveedor][0]} al archivo .env o a los secrets.", title="Falta la API key")

    with st.expander("Hiperparámetros", icon=":material/settings:"):
        temperature = st.slider("Temperatura", 0.0, 1.0, rc.DEFAULTS["temperature"], 0.05,
                                help="Baja = respuestas más fieles al contexto y reproducibles.")
        max_tokens = st.slider("Máx. tokens de salida", 200, 2000, rc.DEFAULTS["max_tokens"], 50)
        top_k_docs = st.slider("Top-k fragmentos normativos", 1, 10, rc.DEFAULTS["top_k_docs"])
        top_k_prod = st.slider("Top-k productos (dataset)", 0, 5, rc.DEFAULTS["top_k_prod"])
        umbral = st.slider("Umbral de similitud coseno", 0.0, 0.8, rc.DEFAULTS["umbral"], 0.05,
                           help="Fragmentos por debajo del umbral no se envían al LLM.")
        turnos = st.slider("Turnos de memoria", 0, 8, rc.DEFAULTS["turnos_historial"])

    st.divider()
    st.caption("Base vectorial")
    c1, c2 = st.columns(2)
    c1.metric("Vectores", f"{est['puntos']:,}")
    c2.metric("Dimensión", est["dimension"])
    st.caption(f"Colección `{est['coleccion']}` · métrica {est['metrica']} · estado {est['estado']}")
    if st.button("Limpiar conversación", icon=":material/delete_sweep:", width="stretch"):
        st.session_state.mensajes = []
        st.rerun()

# ---------------------------------------------------------------------------
# Encabezado y navegación
# ---------------------------------------------------------------------------
st.title(":material/storefront: VentasBot")
st.caption("Chatbot RAG de soporte al cliente y analítica comercial · Pilar Ventas · "
           "Qdrant Cloud + LLM · Especialización en IA, CUN")

vista = st.segmented_control("Vista", ["Chat", "Gestión documental", "Explorador semántico"],
                             default="Chat", label_visibility="collapsed")


def mostrar_fragmentos(frs, usadas=()):
    for f in frs:
        marca = " · citado" if f.n in usadas else ""
        icono = ":material/inventory_2:" if f.tipo == "producto" else ":material/gavel:"
        with st.expander(f"[{f.n}] {f.cita} — similitud {f.score:.3f}{marca}", icon=icono):
            st.write(f.texto)


# ---------------------------------------------------------------------------
# Vista 1: Chat
# ---------------------------------------------------------------------------
if vista == "Chat":
    if not st.session_state.mensajes:
        st.info("Pregunta sobre derecho de retracto, devoluciones, reversión del pago, garantía legal o "
                "productos con más devoluciones. Las respuestas citan la fuente entre corchetes.",
                title="¿En qué te ayudo?", icon=":material/support_agent:")
        sugerencias = [
            "¿En cuántos días puedo ejercer el derecho de retracto en una compra por internet?",
            "¿Cuándo procede la reversión del pago y en qué plazo se devuelve el dinero?",
            "¿Qué productos tienen alta tasa de devolución y qué norma aplica al reembolso?",
        ]
        elegida = st.pills("Ejemplos", sugerencias, label_visibility="collapsed")
    else:
        elegida = None

    for m in st.session_state.mensajes:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])
            if m["role"] == "assistant" and m.get("fragmentos"):
                meta = m["meta"]
                st.caption(f":material/smart_toy: {meta['modelo']} · recuperación {meta['ms_recuperacion']} ms · "
                           f"LLM {meta['ms_llm']} ms · {len(m['fragmentos'])} fragmentos")
                with st.popover("Ver fuentes", icon=":material/menu_book:"):
                    mostrar_fragmentos(m["fragmentos"], meta["citas_usadas"])

    pregunta = st.chat_input("Escribe tu pregunta…") or elegida
    if pregunta:
        historial = [{"role": m["role"], "content": m["content"]} for m in st.session_state.mensajes]
        st.session_state.mensajes.append({"role": "user", "content": pregunta})
        with st.chat_message("user"):
            st.markdown(pregunta)
        with st.chat_message("assistant"):
            with st.spinner("Buscando en la base vectorial y generando respuesta…"):
                try:
                    r = rc.responder(pregunta, historial, proveedor=proveedor, modelo=modelo,
                                     temperature=temperature, max_tokens=max_tokens, top_k_docs=top_k_docs,
                                     top_k_prod=top_k_prod, umbral=umbral, turnos_historial=turnos)
                except Exception as e:  # noqa: BLE001
                    st.error(str(e), title="No se pudo generar la respuesta")
                    st.session_state.mensajes.pop()
                    st.stop()
        meta = {k: r[k] for k in ("modelo", "ms_recuperacion", "ms_llm", "citas_usadas")}
        st.session_state.mensajes.append({"role": "assistant", "content": r["respuesta"],
                                          "fragmentos": r["fragmentos"], "meta": meta})
        st.rerun()

# ---------------------------------------------------------------------------
# Vista 2: Gestión documental
# ---------------------------------------------------------------------------
elif vista == "Gestión documental":
    df = inventario_df()
    docs = df[df["tipo_contenido"] == "documento"]
    prods = df[df["tipo_contenido"] == "producto"]

    with st.container(horizontal=True):
        st.metric("Vectores totales", f"{len(df):,}", border=True)
        st.metric("Fragmentos normativos", f"{len(docs):,}", border=True)
        st.metric("Documentos", docs["fuente"].nunique(), border=True)
        st.metric("Productos indexados", f"{len(prods):,}", border=True)

    st.subheader("Documentos en la base vectorial")
    resumen = (docs.groupby("fuente")
               .agg(titulo=("titulo", "first"), entidad=("entidad", "first"), anio=("anio", "first"),
                    fragmentos=("texto", "size"), long_media=("texto", lambda s: int(s.str.len().mean())))
               .reset_index().sort_values("fuente"))
    st.dataframe(resumen, hide_index=True, alt="Resumen de documentos indexados",
                 column_config={"anio": st.column_config.NumberColumn("año", format="%d"),
                                "fragmentos": st.column_config.ProgressColumn(
                                    "fragmentos", max_value=int(resumen["fragmentos"].max()), format="%d"),
                                "long_media": "caracteres/fragmento"})
    st.bar_chart(resumen.set_index("fuente")["fragmentos"], horizontal=True, height=200)

    st.subheader("Explorar fragmentos y metadatos")
    fuente = st.selectbox("Documento", resumen["fuente"].tolist())
    sel = docs[docs["fuente"] == fuente]
    cols = [c for c in ["pagina", "bloque", "chunk_en_pagina", "tipo_documento", "texto"] if c in sel.columns]
    sel = sel[cols].dropna(axis=1, how="all")
    orden = [c for c in ["pagina", "bloque", "chunk_en_pagina"] if c in sel.columns]
    st.dataframe(sel.sort_values(orden), hide_index=True, height=300, alt="Fragmentos del documento",
                 column_config={"texto": st.column_config.TextColumn("texto", width="large")})

    st.subheader("Productos del dataset (UCI Online Retail)")
    solo_alta = st.toggle("Solo productos con alta tasa de devolución (≥ 5 %)")
    pv = prods[prods["alta_devolucion"]] if solo_alta else prods
    st.dataframe(pv[["codigo_producto", "descripcion", "pais_principal", "unidades_vendidas", "ingresos",
                     "unidades_devueltas", "tasa_devolucion"]].sort_values("ingresos", ascending=False),
                 hide_index=True, height=300, alt="Productos indexados",
                 column_config={"tasa_devolucion": st.column_config.NumberColumn("tasa devolución", format="percent"),
                                "ingresos": st.column_config.NumberColumn("ingresos (£)", format="localized")})

    st.subheader("Agregar o retirar documentos")
    with st.form("subir", clear_on_submit=True):
        archivo = st.file_uploader("Documento (PDF, DOCX, TXT o MD)", type=["pdf", "docx", "txt", "md"])
        c1, c2, c3 = st.columns([3, 2, 1])
        titulo = c1.text_input("Título")
        entidad = c2.text_input("Entidad", value="Documento complementario")
        anio = c3.number_input("Año", 1990, 2030, 2026)
        enviado = st.form_submit_button("Indexar en Qdrant", icon=":material/upload:")
    if enviado and archivo:
        with st.spinner("Fragmentando, vectorizando y subiendo…"):
            n = rc.ingestar_documento(archivo.name, archivo.getvalue(), titulo or archivo.name, entidad, anio)
        inventario_df.clear(); estado.clear()
        st.success(f"{n} fragmentos de «{archivo.name}» indexados.", icon=":material/check_circle:")

    cargados = sorted(df.loc[df["origen"] == "app", "fuente"].unique()) if "origen" in df.columns else []
    if cargados:
        with st.container(horizontal=True, vertical_alignment="bottom"):
            quitar = st.selectbox("Documentos cargados desde la app", cargados)
            if st.button("Eliminar", icon=":material/delete:"):
                rc.eliminar_documento(quitar)
                inventario_df.clear(); estado.clear()
                st.rerun()
    else:
        st.caption("El corpus original del ACA 1 está protegido: solo se pueden eliminar documentos cargados desde la app.")

# ---------------------------------------------------------------------------
# Vista 3: Explorador semántico (recuperación sin LLM)
# ---------------------------------------------------------------------------
else:
    st.caption("Prueba la recuperación vectorial directamente, sin pasar por el LLM.")
    with st.form("explorar"):
        consulta = st.text_input("Consulta", "plazo para devolver el dinero tras el retracto")
        c1, c2, c3 = st.columns([2, 1, 1])
        tipo =c1.segmented_control("Tipo", ["todo", "documento", "producto"], default="todo")
        k = c2.slider("Top-k", 1, 15, 5)
        umb = c3.slider("Umbral", 0.0, 0.8, 0.0, 0.05)
        ir = st.form_submit_button("Buscar", icon=":material/search:")
    if ir and consulta:
        frs, ms = rc.buscar(consulta, k=k, tipo=None if tipo == "todo" else tipo, umbral=umb)
        st.caption(f"{len(frs)} resultados · latencia Qdrant {ms:.0f} ms")
        if frs:
            st.bar_chart(pd.DataFrame({"fragmento": [f"[{f.n}]" for f in frs], "similitud": [f.score for f in frs]})
                         .set_index("fragmento"), height=220)
            mostrar_fragmentos(frs)
        else:
            st.warning("Sin resultados por encima del umbral.")
