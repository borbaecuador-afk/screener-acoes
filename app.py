"""
Screener Dinâmico de Ações (B3, BDRs e EUA)
-------------------------------------------
- Filtros 100% ajustáveis pelo usuário (aba "Configuração de Critérios").
- Dados: Brapi.dev (B3/BDRs) e yfinance (EUA e fallback).
- Pontuação: Ouro / Prata / Bronze / Descartadas, recalculada em tempo real.

Executar:  streamlit run app.py
"""

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="Screener Dinâmico de Ações", page_icon="📈", layout="wide")

# ----------------------------------------------------------------------------
# 1. CONFIGURAÇÃO DOS CRITÉRIOS (valores padrão pedidos)
# ----------------------------------------------------------------------------
# kind: "num" (número puro) | "pct" (usuário digita em %, comparação em fração)
#       | "int" (inteiro) | "bool" (checkbox Sim/Não)
CRITERIA = [
    dict(key="pl",  label="P/L máximo",                 col="P/L",           op="<",  default=10.0,    kind="num", step=0.5),
    dict(key="pvp", label="P/VP máximo",                col="P/VP",          op="<",  default=1.0,     kind="num", step=0.1),
    dict(key="dy",  label="Dividend Yield mínimo (%)",  col="DY",            op=">=", default=10.0,    kind="pct", step=0.5),
    dict(key="vol", label="Volume diário mínimo",       col="Volume (R$)",   op=">",  default=500000,  kind="int", step=50000),
    dict(key="mb",  label="Margem bruta mínima (%)",    col="Margem Bruta",  op=">=", default=30.0,    kind="pct", step=1.0),
    dict(key="ml",  label="Margem líquida mínima (%)",  col="Margem Líquida", op=">=", default=15.0,   kind="pct", step=1.0),
    dict(key="roe", label="ROE mínimo (%)",             col="ROE",           op=">=", default=10.0,    kind="pct", step=1.0),
    dict(key="liq", label="Liquidez imediata mínima",   col="Liquidez",      op=">",  default=0.70,    kind="num", step=0.05),
    dict(key="dl",  label="Dívida líquida / PL máxima", col="DL/PL",         op="<=", default=0.40,    kind="num", step=0.05),
    dict(key="fcl", label="Fluxo de caixa livre positivo", col="FCL",        op=">",  default=True,    kind="bool", step=None),
]
OP_SYMBOL = {"<": "<", "<=": "≤", ">": ">", ">=": "≥"}
PCT_COLS = ["DY", "Margem Bruta", "Margem Líquida", "ROE"]

DEFAULT_UNIVERSES = {
    "B3 (Brapi)": "BBAS3, BBSE3, ITSA4, ITUB4, BBDC4, SANB11, CXSE3, PETR4, PRIO3, VBBR3, UGPA3, VALE3, CMIN3, "
                  "SUZB3, KLBN11, GGBR4, CSNA3, USIM5, BRAP4, TAEE11, TRPL4, ALUP11, CMIG4, CPLE6, EGIE3, "
                  "CPFE3, SAPR11, CSMG3, VIVT3, TIMS3, ABEV3, B3SA3, WEGE3, RADL3, LREN3, MGLU3, RENT3, "
                  "RAIL3, MULT3, CYRE3, SLCE3, SMTO3, AGRO3, HYPE3, FLRY3, BRSR6, TGMA3, BRKM5",
    "BDRs (Brapi)": "AAPL34, MSFT34, AMZO34, GOGL34, COCA34, JNJB34, PGCO34, XOMP34, DISB34, NFLX34",
    "EUA (yfinance)": "AAPL, MSFT, KO, PEP, JNJ, PG, XOM, CVX, T, VZ, MO, PM, ABBV, MRK, PFE, IBM, INTC, CSCO, "
                      "JPM, BAC, WFC, C, MMM, CAT, HD, WMT, MCD, UPS",
}

# ----------------------------------------------------------------------------
# 2. COLETA DE DADOS
# ----------------------------------------------------------------------------
def num(x):
    """Converte para float; devolve None se vazio/inválido/NaN."""
    if isinstance(x, dict):
        x = x.get("raw")
    try:
        if x is None:
            return None
        v = float(x)
        return None if v != v else v
    except (TypeError, ValueError):
        return None


def pick(d, *keys):
    """Primeiro valor numérico disponível entre várias chaves possíveis."""
    if not isinstance(d, dict):
        return None
    for k in keys:
        v = num(d.get(k))
        if v is not None:
            return v
    return None


def finalize(ticker, name, price, pl, pvp, dy, vol, mb, ml, roe, liq, total_debt, total_cash,
             mcap, de_ratio, fcf, currency):
    """Monta a linha padronizada (todas as fontes passam por aqui)."""
    equity = mcap / pvp if (mcap and pvp and pvp > 0) else None
    dl = None
    if total_debt is not None and total_cash is not None and equity and equity > 0:
        dl = (total_debt - total_cash) / equity
    elif de_ratio is not None:  # fallback: dívida bruta / PL (mais conservador)
        dl = de_ratio
    return {
        "Ticker": ticker, "Empresa": name or ticker, "Preço": price, "Moeda": currency,
        "P/L": pl, "P/VP": pvp, "DY": dy,
        "Volume (ações)": vol,
        "Volume (R$)": vol * price if (vol is not None and price) else None,
        "Margem Bruta": mb, "Margem Líquida": ml, "ROE": roe,
        "Liquidez": liq, "DL/PL": dl, "FCL": fcf,
    }


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_brapi(ticker: str, token: str):
    url = f"https://brapi.dev/api/quote/{ticker}"
    params = {"fundamental": "true", "dividends": "true",
              "modules": "financialData,defaultKeyStatistics,summaryDetail"}
    if token:
        params["token"] = token
    r = requests.get(url, params=params, timeout=25)
    if r.status_code in (400, 402, 403):  # plano sem acesso aos módulos extras
        params.pop("modules"), params.pop("dividends")
        r = requests.get(url, params=params, timeout=25)
    r.raise_for_status()
    res = r.json()["results"][0]

    fd, ks, sd = (res.get(k) or {} for k in ("financialData", "defaultKeyStatistics", "summaryDetail"))
    price = pick(res, "regularMarketPrice")
    pl = pick(res, "priceEarnings") or pick(sd, "trailingPE")
    pvp = pick(ks, "priceToBook")
    book = pick(ks, "bookValue")
    if pvp is None and book and price:
        pvp = price / book

    # DY = proventos pagos nos últimos 12 meses / preço
    dy = None
    cash = (res.get("dividendsData") or {}).get("cashDividends") or []
    if cash and price:
        cutoff = (datetime.utcnow() - timedelta(days=365)).strftime("%Y-%m-%d")
        total = sum(num(d.get("rate")) or 0 for d in cash
                    if str(d.get("paymentDate") or d.get("lastDatePrior") or "")[:10] >= cutoff)
        dy = total / price
    if dy is None:
        dy = pick(sd, "trailingAnnualDividendYield", "dividendYield")

    de = pick(fd, "debtToEquity")
    return finalize(
        ticker, res.get("shortName") or res.get("longName"), price, pl, pvp, dy,
        pick(res, "averageDailyVolume3Month", "regularMarketVolume"),
        pick(fd, "grossMargins"), pick(fd, "profitMargins"), pick(fd, "returnOnEquity"),
        pick(fd, "quickRatio", "currentRatio"),
        pick(fd, "totalDebt"), pick(fd, "totalCash"), pick(res, "marketCap"),
        de / 100 if de is not None else None, pick(fd, "freeCashflow"),
        res.get("currency") or "BRL",
    )


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_yf(ticker: str):
    import yfinance as yf
    info = yf.Ticker(ticker).info or {}
    if not info or not (info.get("currentPrice") or info.get("regularMarketPrice")):
        raise ValueError("sem dados no yfinance")
    price = pick(info, "currentPrice", "regularMarketPrice")
    dy = None
    rate = pick(info, "trailingAnnualDividendRate")
    if rate is not None and price:
        dy = rate / price
    if dy is None:
        dy = pick(info, "trailingAnnualDividendYield")
    de = pick(info, "debtToEquity")
    return finalize(
        ticker.replace(".SA", ""), info.get("shortName") or info.get("longName"), price,
        pick(info, "trailingPE"), pick(info, "priceToBook"), dy, pick(info, "averageVolume"),
        pick(info, "grossMargins"), pick(info, "profitMargins"), pick(info, "returnOnEquity"),
        pick(info, "quickRatio", "currentRatio"),
        pick(info, "totalDebt"), pick(info, "totalCash"), pick(info, "marketCap"),
        de / 100 if de is not None else None, pick(info, "freeCashflow"),
        info.get("currency") or "USD",
    )


def load_universe(tickers, market, token, workers=4):
    rows, errors = [], []
    bar = st.progress(0.0, text="Buscando fundamentos…")

    def job(t):
        if market == "EUA (yfinance)":
            return fetch_yf(t)
        try:
            return fetch_brapi(t, token)
        except Exception:
            if market == "B3 (Brapi)":  # fallback: yfinance com sufixo .SA
                return fetch_yf(f"{t}.SA")
            raise

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(job, t): t for t in tickers}
        for i, fut in enumerate(as_completed(futures), 1):
            t = futures[fut]
            try:
                rows.append(fut.result())
            except Exception as e:
                errors.append(f"{t}: {type(e).__name__}")
            bar.progress(i / len(tickers), text=f"Buscando fundamentos… {i}/{len(tickers)}")
    bar.empty()
    return pd.DataFrame(rows), errors


# ----------------------------------------------------------------------------
# 3. MOTOR DE PONTUAÇÃO
# ----------------------------------------------------------------------------
def compare(value, op, thr):
    return {"<": value < thr, "<=": value <= thr, ">": value > thr, ">=": value >= thr}[op]


def fmt(v, kind):
    if v is None or pd.isna(v):
        return "—"
    if kind == "pct":
        return f"{v * 100:.2f}%"
    if kind == "int":
        return f"{v:,.0f}".replace(",", ".")
    if kind == "bool":
        return f"{v:,.0f}".replace(",", ".")
    return f"{v:.2f}"


def score(df, cfg, vol_col, missing_fails, prata_min, bronze_min):
    active = [c for c in CRITERIA if cfg[c["key"]]["on"]]
    details, out = {}, []
    for _, r in df.iterrows():
        items, ok, failed, nd = [], 0, [], 0
        for c in active:
            col = vol_col if c["key"] == "vol" else c["col"]
            val = r.get(col)
            thr = cfg[c["key"]]["val"]
            if c["kind"] == "pct":
                thr = thr / 100
            rule = f"{OP_SYMBOL[c['op']]} {fmt(thr, c['kind'])}" if c["kind"] != "bool" else "positivo"
            if val is None or pd.isna(val):
                status = "nd"
            else:
                passed = compare(val, c["op"], 0 if c["kind"] == "bool" else thr)
                if c["key"] in ("pl", "pvp") and val <= 0:  # lucro/PL negativo não vale como "barato"
                    passed = False
                status = "ok" if passed else "fail"
            if status == "ok":
                ok += 1
            elif status == "fail" or (status == "nd" and missing_fails):
                failed.append(c["label"])
            else:
                nd += 1
            label = {"ok": "✅ Passou", "fail": "❌ Falhou", "nd": "➖ Sem dado"}[status]
            items.append((c["label"], rule, fmt(val, c["kind"]), label))
        evaluated = ok + len(failed)
        adh = ok / evaluated if evaluated else 0.0
        if adh >= 0.999999 and evaluated:
            cat = "Ouro"
        elif adh >= prata_min:
            cat = "Prata"
        elif adh >= bronze_min:
            cat = "Bronze"
        else:
            cat = "Descartada"
        details[r["Ticker"]] = items
        out.append({"Ticker": r["Ticker"], "Aderência": adh, "Critérios": f"{ok}/{evaluated}",
                    "OK": ok, "Avaliados": evaluated, "Falhou em": ", ".join(failed) or "—",
                    "Categoria": cat})
    return df.merge(pd.DataFrame(out), on="Ticker"), details


# ----------------------------------------------------------------------------
# 4. INTERFACE
# ----------------------------------------------------------------------------
def reset_defaults():
    for c in CRITERIA:
        st.session_state[f"on_{c['key']}"] = True
        if c["kind"] != "bool":
            st.session_state[f"val_{c['key']}"] = c["default"]


def table_column_config(vol_col):
    return {
        "Aderência": st.column_config.NumberColumn(format="%.0f%%"),
        "Preço": st.column_config.NumberColumn(format="%.2f"),
        "P/L": st.column_config.NumberColumn(format="%.1f"),
        "P/VP": st.column_config.NumberColumn(format="%.2f"),
        "DY": st.column_config.NumberColumn(format="%.1f%%"),
        "Margem Bruta": st.column_config.NumberColumn(format="%.1f%%"),
        "Margem Líquida": st.column_config.NumberColumn(format="%.1f%%"),
        "ROE": st.column_config.NumberColumn(format="%.1f%%"),
        "Liquidez": st.column_config.NumberColumn(format="%.2f"),
        "DL/PL": st.column_config.NumberColumn(format="%.2f"),
        vol_col: st.column_config.NumberColumn(format="%d"),
    }


def show_category(df, cat, icon, vol_col, details):
    sub = df[df["Categoria"] == cat].sort_values(["Aderência", "DY"], ascending=False)
    st.subheader(f"{icon} {cat} ({len(sub)})")
    if sub.empty:
        st.caption("Nenhuma ação nesta categoria com os critérios atuais.")
        return
    cols = ["Ticker", "Empresa", "Aderência", "Critérios", "Falhou em", "Preço", "P/L", "P/VP", "DY",
            vol_col, "Margem Bruta", "Margem Líquida", "ROE", "Liquidez", "DL/PL"]
    view = sub[cols].copy().reset_index(drop=True)
    for c in PCT_COLS + ["Aderência"]:
        if c in view:
            view[c] = view[c] * 100
    event = st.dataframe(view, hide_index=True, width="stretch", key=f"tbl_{cat}",
                         on_select="rerun", selection_mode="single-row",
                         column_config=table_column_config(vol_col))
    if event.selection.rows:
        row = view.iloc[event.selection.rows[0]]
        st.markdown(f"**{row['Ticker']} — {row['Empresa']}** · {row['Critérios']} critérios atendidos")
        st.progress(float(row["Aderência"]) / 100)
        st.dataframe(pd.DataFrame(details[row["Ticker"]], columns=["Critério", "Regra", "Valor", "Resultado"]),
                     hide_index=True, width="stretch")
    else:
        st.caption("Clique em uma linha para ver quais critérios a ação passou ou falhou.")


# ---- Sidebar: universo e opções -------------------------------------------
with st.sidebar:
    st.header("Universo de ações")
    market = st.radio("Mercado", list(DEFAULT_UNIVERSES), index=0)
    try:
        default_token = st.secrets.get("BRAPI_TOKEN", "")
    except Exception:
        default_token = ""
    token = st.text_input("Token Brapi.dev", type="password",
                          value=os.getenv("BRAPI_TOKEN", default_token),
                          help="Gratuito em brapi.dev. Sem token só funcionam alguns tickers de teste.")
    tickers_txt = st.text_area("Tickers (separados por vírgula)", DEFAULT_UNIVERSES[market], height=160,
                               key=f"tickers_{market}")
    st.header("Regras de pontuação")
    vol_mode = st.radio("Volume diário medido em", ["Financeiro (R$/US$)", "Quantidade de ações"])
    missing_fails = st.checkbox("Dado ausente conta como falha", value=True,
                                help="Desmarcado: critérios sem dado saem do cálculo da aderência.")
    prata_min = st.slider("Prata a partir de (% de aderência)", 50, 99, 80) / 100
    bronze_min = st.slider("Bronze a partir de (% de aderência)", 10, 90, 50) / 100
    fetch = st.button("🔄 Buscar / atualizar dados", type="primary")

st.title("📈 Screener Dinâmico de Ações")

tab_cfg, tab_res = st.tabs(["⚙️ Configuração de Critérios", "📊 Resultados"])

# ---- Aba 1: filtros dinâmicos ---------------------------------------------
cfg = {}
with tab_cfg:
    st.write("Altere os valores e marque quais critérios entram no cálculo. "
             "Os resultados são recalculados na hora, sem nova consulta à API.")
    st.button("Restaurar padrões", on_click=reset_defaults)
    left, right = st.columns(2)
    for i, c in enumerate(CRITERIA):
        with (left if i % 2 == 0 else right):
            box = st.container(border=True)
            k = c["key"]
            if c["kind"] == "bool":
                on = box.checkbox("Exigir fluxo de caixa livre positivo (Sim/Não)", value=True, key=f"on_{k}")
                cfg[k] = {"on": on, "val": True}
                continue
            on = box.checkbox(c["label"], value=True, key=f"on_{k}")
            fmt_str = "%d" if c["kind"] == "int" else "%.2f"
            val = box.number_input(c["label"], value=c["default"], step=c["step"], format=fmt_str,
                                   key=f"val_{k}", disabled=not on, label_visibility="collapsed")
            cfg[k] = {"on": on, "val": val}

# ---- Coleta ---------------------------------------------------------------
if fetch:
    tickers = sorted({t.strip().upper().replace(".SA", "") for t in
                      tickers_txt.replace("\n", ",").replace(" ", ",").split(",") if t.strip()})
    if not tickers:
        st.sidebar.error("Informe ao menos um ticker.")
    else:
        raw, errs = load_universe(tickers, market, token)
        st.session_state["raw"], st.session_state["errs"] = raw, errs

# ---- Aba 2: resultados ----------------------------------------------------
with tab_res:
    raw = st.session_state.get("raw")
    if raw is None or raw.empty:
        st.info("Clique em **Buscar / atualizar dados** na barra lateral para carregar os fundamentos.")
        if st.session_state.get("errs"):
            st.warning("Falhas: " + "; ".join(st.session_state["errs"]))
    elif not any(v["on"] for v in cfg.values()):
        st.warning("Ative ao menos um critério na aba de configuração.")
    else:
        vol_col = "Volume (R$)" if vol_mode.startswith("Financeiro") else "Volume (ações)"
        scored, details = score(raw, cfg, vol_col, missing_fails, prata_min, bronze_min)

        counts = scored["Categoria"].value_counts()
        m = st.columns(4)
        for col, (cat, icon) in zip(m, [("Ouro", "🥇"), ("Prata", "🥈"), ("Bronze", "🥉"), ("Descartada", "❌")]):
            col.metric(f"{icon} {cat}", int(counts.get(cat, 0)))

        if st.session_state.get("errs"):
            with st.expander(f"{len(st.session_state['errs'])} ticker(s) sem dados"):
                st.write(", ".join(st.session_state["errs"]))

        for cat, icon in [("Ouro", "🥇"), ("Prata", "🥈"), ("Bronze", "🥉")]:
            show_category(scored, cat, icon, vol_col, details)
        with st.expander(f"❌ Descartadas / fora do alvo ({int(counts.get('Descartada', 0))})"):
            show_category(scored, "Descartada", "❌", vol_col, details)

        st.download_button("⬇️ Baixar resultado completo (CSV)",
                           scored.to_csv(index=False).encode("utf-8-sig"), "screener.csv", "text/csv")
