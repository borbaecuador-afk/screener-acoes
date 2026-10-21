"""Screener Dinâmico de Ações da B3 — filtros móveis, 4 fontes e verificação cruzada.

Executar:  streamlit run app.py
"""
import os

import pandas as pd
import streamlit as st

from scoring import (CAT_ICON, CATEGORIES, CRITERIA, OP, PRESETS, VOL_OPTIONS, agreement,
                     score_universe, threshold)
from sources import SOURCES, enrich, fetch_fundamentus, fetch_statusinvest

st.set_page_config(page_title="Screener B3", page_icon="📈", layout="wide")

CSS = {"ok": "background-color: rgba(46,160,67,.38)", "fail": "background-color: rgba(218,54,51,.38)",
       "div": "background-color: rgba(210,153,34,.45)", "nd": "background-color: rgba(128,128,128,.22)"}
ICON = {"ok": "✓", "fail": "✗", "div": "⚠", "nd": "—"}
LABEL = {"ok": "✓ Passou", "fail": "✗ Falhou", "div": "⚠ Fontes divergem", "nd": "— Sem dado"}
METRIC_NAME = {c["key"]: c["short"] for c in CRITERIA}


# ------------------------------------------------------------------ formatação
def money(v):
    for lim, suf in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if abs(v) >= lim:
            return f"{v / lim:.1f}{suf}"
    return f"{v:.0f}"


def fmt(c, v):
    if v is None:
        return "—"
    if c["kind"] == "pct":
        return f"{v * 100:.1f}%"
    if c["kind"] in ("money", "bool"):
        return money(v)
    return f"{v:.2f}"


def rule(c, cfg):
    return "positivo" if c["kind"] == "bool" else f"{OP[c['op']]} {fmt(c, threshold(c, cfg))}"


# ------------------------------------------------------------------- dados
@st.cache_data(ttl=6 * 3600, show_spinner="Carregando todas as ações da B3…")
def load_bulk():
    out, status = {}, {}
    for key, fn in (("fundamentus", fetch_fundamentus), ("statusinvest", fetch_statusinvest)):
        data, err = fn()
        status[key] = err
        if data:
            out[key] = data
    return out, status


def apply_preset():
    for k, (on, val) in PRESETS[st.session_state["preset"]].items():
        st.session_state[f"on_{k}"] = on
        if k != "fcl":
            st.session_state[f"val_{k}"] = val


for _c in CRITERIA:
    st.session_state.setdefault(f"on_{_c['key']}", True)
    if _c["kind"] != "bool":
        st.session_state.setdefault(f"val_{_c['key']}", _c["default"])
st.session_state.setdefault("extra", {})

bulk, bulk_status = load_bulk()

# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.header("Critérios")
    st.selectbox("Perfil", list(PRESETS), key="preset", on_change=apply_preset,
                 help="Carrega valores prontos. Depois é só mexer nos controles.")
    for c in CRITERIA:
        k = c["key"]
        st.toggle(c["label"], key=f"on_{k}")
        if c["kind"] == "bool":
            continue
        off = not st.session_state[f"on_{k}"]
        if c["kind"] == "money":
            st.select_slider("v", options=c["options"], key=f"val_{k}", format_func=money,
                             disabled=off, label_visibility="collapsed")
        else:
            st.slider("v", c["lo"], c["hi"], step=c["step"], key=f"val_{k}", disabled=off,
                      format="%.1f%%" if c["kind"] == "pct" else "%.2f", label_visibility="collapsed")
    with st.expander("Regras de classificação"):
        prata_min = st.slider("Prata a partir de", 50, 99, 80, format="%d%%") / 100
        bronze_min = st.slider("Bronze a partir de", 10, 90, 50, format="%d%%") / 100
        missing_fails = st.toggle("Dado ausente conta como falha", value=True,
                                  help="Desligado: critério sem dado sai do cálculo.")
    with st.expander("Token da Brapi"):
        try:
            secret = st.secrets.get("BRAPI_TOKEN", "")
        except Exception:  # noqa: BLE001
            secret = ""
        token = st.text_input("Token", type="password", value=os.getenv("BRAPI_TOKEN", secret))
    st.divider()
    st.caption("Status das fontes em lote")
    for s in ("fundamentus", "statusinvest"):
        err = bulk_status.get(s)
        n = len(bulk.get(s, {}))
        st.caption(f"{'✅' if s in bulk else '❌'} {SOURCES[s]}: {f'{n} ações' if s in bulk else err}")
    if st.button("Atualizar todos os dados"):
        load_bulk.clear()
        st.session_state["extra"] = {}
        st.rerun()

if not bulk:
    st.error("Nenhuma fonte em lote respondeu. Veja o motivo na barra lateral e use 'Atualizar todos os dados'.")
    st.stop()

# ---------------------------------------------------------------- cálculo
cfg = {c["key"]: {"on": st.session_state[f"on_{c['key']}"],
                  "val": st.session_state.get(f"val_{c['key']}", True)} for c in CRITERIA}
active = [c for c in CRITERIA if cfg[c["key"]]["on"]]
store = {**bulk, **{s: d for s, d in st.session_state["extra"].items() if d}}
universe = sorted(set().union(*[set(d) for d in bulk.values()]))

st.title("📈 Screener de Ações da B3")
if not active:
    st.warning("Ligue ao menos um critério na barra lateral.")
    st.stop()
if bronze_min > prata_min:
    st.warning("O corte do Bronze está acima do corte da Prata. Ajuste em 'Regras de classificação'.")

scored, details = score_universe(universe, store, cfg, missing_fails, prata_min, bronze_min)
counts = scored["Categoria"].value_counts()

m = st.columns(5)
m[0].metric("Ações analisadas", len(scored))
for col, cat in zip(m[1:], CATEGORIES):
    col.metric(f"{CAT_ICON[cat]} {cat}", int(counts.get(cat, 0)))

tab_scr, tab_q, tab_info = st.tabs(["📋 Screener", "🧪 Qualidade dos dados", "ℹ️ Fontes e limites"])

# ------------------------------------------------------------- aba screener
with tab_scr:
    with st.expander("🔍 Validar candidatos com Yahoo Finance e Brapi (3ª e 4ª fonte)"):
        st.caption("As duas fontes em lote já são comparadas para todas as ações. Aqui você confirma só os "
                   "candidatos com mais duas fontes, que também trazem fluxo de caixa livre.")
        a, b, c3 = st.columns(3)
        scope = a.selectbox("Validar", ["Ouro", "Ouro + Prata", "Ouro + Prata + Bronze"])
        n_max = b.slider("Máximo de ações", 5, 60, 25)
        srcs = c3.multiselect("Fontes", ["yahoo", "brapi"], default=["yahoo", "brapi"], format_func=SOURCES.get)
        if st.button("Validar agora", type="primary"):
            cats = CATEGORIES[: ["Ouro", "Ouro + Prata", "Ouro + Prata + Bronze"].index(scope) + 1]
            cands = scored[scored["Categoria"].isin(cats)].head(n_max)["Ticker"].tolist()
            jobs = [(s, t) for s in srcs for t in cands if t not in st.session_state["extra"].get(s, {})]
            if not jobs:
                st.info("Nada novo para validar.")
            else:
                bar = st.progress(0.0, text="Consultando fontes…")
                got, errs = enrich(jobs, token, on_progress=lambda i, n: bar.progress(i / n))
                for s, d in got.items():
                    st.session_state["extra"].setdefault(s, {}).update(d)
                st.session_state["enrich_errs"] = errs
                st.rerun()
        errs = st.session_state.get("enrich_errs") or {}
        for s, lst in errs.items():
            st.warning(f"{SOURCES[s]}: {len(lst)} falha(s). Exemplo: {lst[0]}")

    f1, f2 = st.columns([1, 2])
    query = f1.text_input("Buscar ticker ou empresa", placeholder="ex.: BBAS, taesa")
    cats_sel = f2.multiselect("Categorias", CATEGORIES, default=["Ouro", "Prata", "Bronze"],
                              format_func=lambda x: f"{CAT_ICON[x]} {x}")
    shown = scored[scored["Categoria"].isin(cats_sel)]
    if query:
        q = query.strip().lower()
        shown = shown[shown["Ticker"].str.lower().str.contains(q) | shown["Empresa"].str.lower().str.contains(q)]
    shown = shown.reset_index(drop=True)

    if shown.empty:
        st.info("Nenhuma ação com esses filtros. Afrouxe algum critério na barra lateral.")
    else:
        cols = ["Cat.", "Ticker", "Empresa", "Aderência", "Fontes"] + [c["short"] for c in active]
        rows, css_rows = [], []
        for _, r in shown.iterrows():
            cells = details[r["Ticker"]]
            row = {"Cat.": CAT_ICON[r["Categoria"]], "Ticker": r["Ticker"], "Empresa": r["Empresa"],
                   "Aderência": f"{r['OK']}/{r['Avaliados']} · {r['Aderência']:.0%}", "Fontes": r["Fontes"]}
            cs = {k: "" for k in cols}
            cs["Aderência"] = CSS["ok"] if r["Categoria"] == "Ouro" else (
                CSS["fail"] if r["Categoria"] == "Descartada" else CSS["nd"])
            for c in active:
                res = cells[c["key"]]
                row[c["short"]] = "—" if res["status"] == "nd" else f"{ICON[res['status']]} {fmt(c, res['value'])}"
                cs[c["short"]] = CSS[res["status"]]
            rows.append(row)
            css_rows.append(cs)
        view, css = pd.DataFrame(rows)[cols], pd.DataFrame(css_rows)[cols]
        st.caption("🟩 passou · 🟥 falhou · 🟨 fontes divergem · ⬜ sem dado. "
                   "Fontes: F Fundamentus · S Status Invest · Y Yahoo · B Brapi. Clique numa linha para o detalhe.")
        ev = st.dataframe(view.style.apply(lambda _: css, axis=None), hide_index=True, width="stretch",
                          height=min(36 * (len(view) + 1) + 3, 620), key="tbl",
                          on_select="rerun", selection_mode="single-row")
        pos = ev.selection.rows
        ticker = shown.iloc[pos[0]]["Ticker"] if pos and pos[0] < len(shown) else None
        if ticker is None:
            ticker = st.selectbox("Ou escolha uma ação para detalhar", [None] + shown["Ticker"].tolist())

        if ticker:
            r = scored[scored["Ticker"] == ticker].iloc[0]
            st.subheader(f"{ticker} · {r['Empresa'] or 'sem nome'}")
            st.progress(float(r["Aderência"]),
                        text=f"{r['OK']}/{r['Avaliados']} critérios · {CAT_ICON[r['Categoria']]} {r['Categoria']}")
            drows, dcss = [], []
            for c in active:
                res = details[ticker][c["key"]]
                row = {"Critério": c["label"], "Regra": rule(c, cfg), "Valor adotado": fmt(c, res["value"])}
                cs = {"Critério": "", "Regra": "", "Valor adotado": "", "Resultado": CSS[res["status"]]}
                for s, name in SOURCES.items():
                    v = res["per"].get(s)
                    row[name] = fmt(c, v) if v is not None else "—"
                    cs[name] = "" if v is None else CSS["ok" if res["verdicts"][s] else "fail"]
                row["Resultado"] = LABEL[res["status"]]
                drows.append(row)
                dcss.append(cs)
            dv, dc = pd.DataFrame(drows), pd.DataFrame(dcss)
            st.dataframe(dv.style.apply(lambda _: dc[dv.columns], axis=None), hide_index=True, width="stretch")
            if (r["Falhas"] != "—"):
                st.caption(f"Reprovou ou divergiu em: {r['Falhas']}.")

# ----------------------------------------------------------- aba qualidade
with tab_q:
    stats, div = agreement(store)
    if stats.empty:
        st.info("Ainda não há dados de pelo menos duas fontes para comparar.")
    else:
        st.write("Quanto cada fonte concorda com as demais. Com 2 fontes a concordância é simétrica; "
                 "com 3 ou mais (após validar), a fonte que destoa aparece com percentual menor.")
        stats["ok"] = stats["Comparações"] * stats["Concordância"]
        tot = stats.groupby("Fonte").agg(Comparações=("Comparações", "sum"), ok=("ok", "sum"))
        tot["Concordância"] = tot["ok"] / tot["Comparações"]
        st.dataframe(tot[["Comparações", "Concordância"]], width="stretch",
                     column_config={"Concordância": st.column_config.ProgressColumn(format="percent", min_value=0, max_value=1)})
        pv = stats.pivot(index="Fonte", columns="Métrica", values="Concordância").rename(columns=METRIC_NAME)

        def tint(v):
            if pd.isna(v):
                return ""
            return CSS["ok"] if v >= 0.85 else (CSS["div"] if v >= 0.6 else CSS["fail"])

        st.dataframe(pv.style.format("{:.0%}", na_rep="—").apply(
            lambda d: pd.DataFrame([[tint(v) for v in row] for row in d.values], index=d.index, columns=d.columns),
            axis=None), width="stretch")
        st.subheader("Maiores divergências")
        if div.empty:
            st.success("Nenhuma divergência acima da tolerância.")
        else:
            d = div.head(60).copy()
            d["Métrica"] = d["Métrica"].map(METRIC_NAME)
            d["Desvio"] = d["Desvio"] * 100
            st.caption("Percentuais em fração (0,10 = 10%). Desvio = (maior − menor) / mediana.")
            st.dataframe(d, hide_index=True, width="stretch",
                         column_config={"Desvio": st.column_config.NumberColumn(format="%.0f%%")})

# ------------------------------------------------------------- aba fontes
with tab_info:
    st.markdown("""
**Fundamentus** · lote (todas as ações) · P/L, P/VP, DY, margem líquida, ROE, liquidez corrente, volume.
Raspagem de página, sem chave. Não tem margem bruta, dívida líquida nem FCL.

**Status Invest** · lote (todas as ações) · adiciona margem bruta e dívida líquida/PL.
Endpoint não oficial: pode mudar ou bloquear acessos de servidores de nuvem. Se falhar, o app segue com as demais.

**Yahoo Finance** (yfinance) · por ticker · tem FCL e dívida. Não oficial; pode limitar o ritmo.

**Brapi** · por ticker · API oficial. No plano gratuito: 1 ação por chamada, 15.000 chamadas/mês, sem dividendos
e fundamentos básicos. Com token, ajuda a confirmar P/L e preço.

**Como o veredito é decidido.** Cada fonte é comparada com o limite do critério. Se todas concordam, vale esse resultado.
Com 3 ou mais fontes, a maioria decide. Com divergência sem maioria, o critério fica 🟨 e conta como não atendido.

**Limites.** Concordância entre fontes não prova que o dado está certo: fontes podem copiar o mesmo erro.
A liquidez usada é a **corrente** (nenhuma fonte gratuita entrega a liquidez imediata).
Dados atrasados e indicadores de bancos (sem margem bruta) podem aparecer sem dado. Não é recomendação de investimento.
""")
