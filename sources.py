"""Coleta de dados em 4 fontes, todas normalizadas para o mesmo formato.

Convenções (iguais para todas as fontes):
  - percentuais como fração (0.10 = 10%)
  - volume em R$ por dia
  - cada fonte devolve um dict {ticker: {metrica: valor}}
Métricas: pl, pvp, dy, vol, mb (margem bruta), ml (margem líquida), roe,
          liq (liquidez corrente), dl (dívida líquida/PL), fcl (fluxo de caixa livre)
"""
import io
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests

SOURCES = {"fundamentus": "Fundamentus", "statusinvest": "Status Invest",
           "yahoo": "Yahoo Finance", "brapi": "Brapi"}
LETTER = {"fundamentus": "F", "statusinvest": "S", "yahoo": "Y", "brapi": "B"}
METRICS = ["pl", "pvp", "dy", "vol", "mb", "ml", "roe", "liq", "dl", "fcl"]
TICKER_RE = re.compile(r"^[A-Z]{4}\d{1,2}$")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
}


# ---------------------------------------------------------------- utilitários
def num(x):
    """float válido ou None (trata None, NaN, inf, dict {'raw': x})."""
    if isinstance(x, dict):
        x = x.get("raw")
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if v != v or v in (float("inf"), float("-inf")) else v


def br_num(x):
    """Converte '1.234,56' / '12,3%' / 12.3 em float."""
    if isinstance(x, str):
        s = x.replace("%", "").replace("R$", "").strip()
        if s in ("", "-", "—", "N/D"):
            return None
        if "," in s:
            s = s.replace(".", "").replace(",", ".")
        elif s.count(".") > 1:
            s = s.replace(".", "")
        return num(s)
    return num(x)


def frac(v):
    return None if v is None else v / 100


def nz(v):
    """Zero em indicadores de múltiplo/liquidez significa 'sem dado' no Fundamentus."""
    return None if v in (None, 0) else v


def pick(d, *keys):
    if not isinstance(d, dict):
        return None
    for k in keys:
        v = num(d.get(k))
        if v is not None:
            return v
    return None


def to_store(df):
    return df.astype(object).where(df.notna(), None).to_dict("index")


# ------------------------------------------------- fontes em lote (mercado todo)
def fetch_fundamentus():
    """Todas as ações da B3 numa só requisição. Colunas mapeadas por posição."""
    try:
        r = requests.get("https://www.fundamentus.com.br/resultado.php", headers=HEADERS, timeout=30)
        r.raise_for_status()
        raw = pd.read_html(io.StringIO(r.content.decode("latin-1")), decimal=",", thousands=".")[0]
        if raw.shape[1] < 18:
            raise ValueError(f"layout inesperado ({raw.shape[1]} colunas)")
        rows = {}
        for _, x in raw.iterrows():
            t = str(x.iloc[0]).strip().upper()
            if not TICKER_RE.match(t):
                continue
            rows[t] = {
                "price": br_num(x.iloc[1]), "pl": nz(br_num(x.iloc[2])), "pvp": nz(br_num(x.iloc[3])),
                "dy": frac(br_num(x.iloc[5])), "ml": frac(br_num(x.iloc[13])),
                "liq": nz(br_num(x.iloc[14])), "roe": frac(br_num(x.iloc[16])),
                "vol": br_num(x.iloc[17]),
            }
        if not rows:
            raise ValueError("nenhuma ação encontrada na tabela")
        return to_store(pd.DataFrame.from_dict(rows, orient="index")), None
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"


_SI_FIELDS = ["dy", "p_L", "peg_Ratio", "p_VP", "p_Ativo", "margemBruta", "margemEbit", "margemLiquida",
              "p_Ebit", "eV_Ebit", "dividaLiquidaEbit", "dividaliquidaPatrimonioLiquido", "p_SR",
              "p_CapitalGiro", "p_AtivoCirculante", "roe", "roic", "roa", "liquidezCorrente", "pl_Ativo",
              "passivo_Ativo", "giroAtivos", "receitas_Cagr5", "lucros_Cagr5", "liquidezMediaDiaria",
              "vpa", "lpa"]


def fetch_statusinvest():
    """Endpoint de busca avançada do Status Invest (não oficial): todas as ações em JSON."""
    try:
        rng = {"Item1": None, "Item2": None}
        search = {"Sector": "", "SubSector": "", "Segment": "", "my_range": "-20;100",
                  "forecast": {"upsideDownside": rng, "estimatesNumber": rng, "revisedUp": True,
                               "revisedDown": True, "consensus": []}}
        search.update({k: rng for k in _SI_FIELDS})
        r = requests.get(
            "https://statusinvest.com.br/category/advancedsearchresult",
            params={"search": json.dumps(search, separators=(",", ":")), "CategoryType": 1},
            headers={**HEADERS, "Accept": "application/json",
                     "Referer": "https://statusinvest.com.br/acoes/busca-avancada"},
            timeout=40,
        )
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict):
            data = data.get("list") or next((v for v in data.values() if isinstance(v, list)), [])
        rows = {}
        for item in data:
            k = {str(a).lower(): b for a, b in item.items()}  # chaves sem diferenciar maiúsculas
            t = str(k.get("ticker", "")).strip().upper()
            if not TICKER_RE.match(t):
                continue
            rows[t] = {
                "name": k.get("companyname"), "price": num(k.get("price")),
                "pl": num(k.get("p_l")), "pvp": num(k.get("p_vp")), "dy": frac(num(k.get("dy"))),
                "mb": frac(num(k.get("margembruta"))), "ml": frac(num(k.get("margemliquida"))),
                "roe": frac(num(k.get("roe"))), "liq": num(k.get("liquidezcorrente")),
                "dl": num(k.get("dividaliquidapatrimonioliquido")),
                "vol": num(k.get("liquidezmediadiaria")),
            }
        if not rows:
            raise ValueError("resposta sem ações (estrutura mudou ou acesso bloqueado)")
        return to_store(pd.DataFrame.from_dict(rows, orient="index")), None
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"


# ------------------------------------------------------ fontes por ticker
def fetch_yahoo(ticker):
    import yfinance as yf
    info = yf.Ticker(f"{ticker}.SA").info or {}
    price = pick(info, "currentPrice", "regularMarketPrice")
    if not price:
        raise ValueError("sem cotação")
    pvp, mcap = pick(info, "priceToBook"), pick(info, "marketCap")
    equity = mcap / pvp if (mcap and pvp and pvp > 0) else None
    debt, cash = pick(info, "totalDebt"), pick(info, "totalCash")
    rate = pick(info, "trailingAnnualDividendRate")
    vol = pick(info, "averageVolume")
    return {
        "name": info.get("shortName"), "price": price, "pl": pick(info, "trailingPE"), "pvp": pvp,
        "dy": rate / price if rate is not None else pick(info, "trailingAnnualDividendYield"),
        "vol": vol * price if vol else None, "mb": pick(info, "grossMargins"),
        "ml": pick(info, "profitMargins"), "roe": pick(info, "returnOnEquity"),
        "liq": pick(info, "currentRatio"),
        "dl": (debt - cash) / equity if (debt is not None and cash is not None and equity) else None,
        "fcl": pick(info, "freeCashflow"),
    }


def fetch_brapi(ticker, token):
    headers, params = dict(HEADERS), {"fundamental": "true",
                                      "modules": "financialData,defaultKeyStatistics,summaryDetail"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
        params["token"] = token
    url = f"https://brapi.dev/api/quote/{ticker}"
    r = requests.get(url, params=params, headers=headers, timeout=25)
    if r.status_code in (400, 402, 403):  # módulos fora do plano gratuito: tenta o básico
        params.pop("modules")
        r = requests.get(url, params=params, headers=headers, timeout=25)
    if r.status_code == 401:
        raise RuntimeError("token ausente ou inválido")
    if r.status_code == 429:
        raise RuntimeError("limite de requisições atingido")
    r.raise_for_status()
    res = r.json()["results"][0]
    fd, ks, sd = (res.get(k) or {} for k in ("financialData", "defaultKeyStatistics", "summaryDetail"))
    price = pick(res, "regularMarketPrice")
    pvp = pick(ks, "priceToBook")
    if pvp is None and price and pick(ks, "bookValue"):
        pvp = price / pick(ks, "bookValue")
    mcap = pick(res, "marketCap")
    equity = mcap / pvp if (mcap and pvp and pvp > 0) else None
    debt, cash = pick(fd, "totalDebt"), pick(fd, "totalCash")
    vol = pick(res, "averageDailyVolume3Month", "regularMarketVolume")
    return {
        "name": res.get("shortName"), "price": price,
        "pl": pick(res, "priceEarnings") or pick(sd, "trailingPE"), "pvp": pvp,
        "dy": pick(sd, "trailingAnnualDividendYield", "dividendYield"),
        "vol": vol * price if (vol and price) else None, "mb": pick(fd, "grossMargins"),
        "ml": pick(fd, "profitMargins"), "roe": pick(fd, "returnOnEquity"),
        "liq": pick(fd, "currentRatio"),
        "dl": (debt - cash) / equity if (debt is not None and cash is not None and equity) else None,
        "fcl": pick(fd, "freeCashflow"),
    }


def enrich(jobs, token="", workers=3, on_progress=None):
    """jobs: lista de (fonte, ticker). Retorna ({fonte: {ticker: dict}}, {fonte: [erros]})."""
    fns = {"yahoo": fetch_yahoo, "brapi": lambda t: fetch_brapi(t, token)}
    out, errs = {}, {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(fns[s], t): (s, t) for s, t in jobs}
        for i, fut in enumerate(as_completed(futures), 1):
            s, t = futures[fut]
            try:
                out.setdefault(s, {})[t] = fut.result()
            except Exception as e:  # noqa: BLE001
                errs.setdefault(s, []).append(f"{t}: {e}")
            if on_progress:
                on_progress(i, len(jobs))
    return out, errs
