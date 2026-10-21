"""Critérios, veredito por critério com votação entre fontes, categorias e concordância."""
import statistics

import pandas as pd

from sources import LETTER, SOURCES

VOL_OPTIONS = [0, 100_000, 250_000, 500_000, 1_000_000, 2_000_000, 5_000_000,
               10_000_000, 25_000_000, 50_000_000]

# kind: num | pct (usuário em %, comparação em fração) | money (R$) | bool
CRITERIA = [
    dict(key="pl",  label="P/L máximo",                 short="P/L",       op="<",  default=10.0, kind="num", lo=0.0, hi=50.0, step=0.5),
    dict(key="pvp", label="P/VP máximo",                short="P/VP",      op="<",  default=1.0,  kind="num", lo=0.0, hi=10.0, step=0.1),
    dict(key="dy",  label="Dividend Yield mínimo",      short="DY",        op=">=", default=10.0, kind="pct", lo=0.0, hi=30.0, step=0.5),
    dict(key="vol", label="Volume diário mínimo (R$)",  short="Volume R$", op=">",  default=500_000, kind="money", options=VOL_OPTIONS),
    dict(key="mb",  label="Margem bruta mínima",        short="M. Bruta",  op=">=", default=30.0, kind="pct", lo=0.0, hi=100.0, step=1.0),
    dict(key="ml",  label="Margem líquida mínima",      short="M. Líq.",   op=">=", default=15.0, kind="pct", lo=0.0, hi=100.0, step=1.0),
    dict(key="roe", label="ROE mínimo",                 short="ROE",       op=">=", default=10.0, kind="pct", lo=0.0, hi=60.0, step=1.0),
    dict(key="liq", label="Liquidez corrente mínima",   short="Liq. Corr.", op=">", default=0.70, kind="num", lo=0.0, hi=5.0, step=0.05),
    dict(key="dl",  label="Dívida líquida / PL máxima", short="DL/PL",     op="<=", default=0.40, kind="num", lo=0.0, hi=5.0, step=0.05),
    dict(key="fcl", label="Fluxo de caixa livre positivo", short="FCL",    op=">",  default=True, kind="bool"),
]
OP = {"<": "<", "<=": "≤", ">": ">", ">=": "≥"}

PRESETS = {
    "Meu padrão": {c["key"]: (True, c["default"]) for c in CRITERIA},
    "Dividendos": {"pl": (True, 12.0), "pvp": (True, 1.5), "dy": (True, 6.0), "vol": (True, 1_000_000),
                   "mb": (False, 30.0), "ml": (True, 10.0), "roe": (True, 10.0), "liq": (True, 1.0),
                   "dl": (True, 1.0), "fcl": (True, True)},
    "Valor (Graham)": {"pl": (True, 15.0), "pvp": (True, 1.5), "dy": (False, 5.0), "vol": (True, 1_000_000),
                       "mb": (False, 30.0), "ml": (False, 10.0), "roe": (True, 8.0), "liq": (True, 1.5),
                       "dl": (True, 1.0), "fcl": (False, True)},
    "Qualidade": {"pl": (True, 20.0), "pvp": (False, 3.0), "dy": (False, 5.0), "vol": (True, 1_000_000),
                  "mb": (True, 30.0), "ml": (True, 12.0), "roe": (True, 15.0), "liq": (True, 1.0),
                  "dl": (True, 0.5), "fcl": (True, True)},
}

# (tolerância relativa, tolerância absoluta) para considerar duas fontes "iguais"
TOL = {"pl": (0.15, 0.5), "pvp": (0.15, 0.1), "dy": (0.15, 0.01), "vol": (0.5, 0.0), "mb": (0.15, 0.02),
       "ml": (0.15, 0.02), "roe": (0.15, 0.02), "liq": (0.2, 0.15), "dl": (0.25, 0.1), "fcl": (0, 0)}
CATEGORIES = ["Ouro", "Prata", "Bronze", "Descartada"]
CAT_ICON = {"Ouro": "🥇", "Prata": "🥈", "Bronze": "🥉", "Descartada": "❌"}


def threshold(c, cfg):
    v = cfg[c["key"]]["val"]
    return v / 100 if c["kind"] == "pct" else v


def verdict(c, v, thr):
    if c["key"] == "fcl":
        return v > 0
    if c["key"] in ("pl", "pvp") and v <= 0:  # prejuízo / PL negativo não é "barato"
        return False
    return {"<": v < thr, "<=": v <= thr, ">": v > thr, ">=": v >= thr}[c["op"]]


def judge(c, vals, thr):
    """Veredito de um critério usando todas as fontes disponíveis.
    ok/fail: todas concordam, ou há maioria clara entre 3+ fontes.
    div: fontes discordam sem maioria. nd: nenhuma fonte tem o dado."""
    vals = {s: v for s, v in vals.items() if v is not None}
    if not vals:
        return {"status": "nd", "value": None, "per": {}, "verdicts": {}}
    verdicts = {s: verdict(c, v, thr) for s, v in vals.items()}
    yes = [s for s, ok in verdicts.items() if ok]
    no = [s for s, ok in verdicts.items() if not ok]
    if not no:
        status, used = "ok", list(vals)
    elif not yes:
        status, used = "fail", list(vals)
    elif len(vals) >= 3 and len(yes) != len(no):
        status, used = ("ok", yes) if len(yes) > len(no) else ("fail", no)
    else:
        status, used = "div", list(vals)
    return {"status": status, "value": statistics.median(vals[s] for s in used),
            "per": vals, "verdicts": verdicts}


def categorize(adh, evaluated, ok, prata_min, bronze_min):
    if evaluated and ok == evaluated:
        return "Ouro"
    if adh >= prata_min:
        return "Prata"
    if adh >= bronze_min:
        return "Bronze"
    return "Descartada"


def first(store, t, key, order=("statusinvest", "yahoo", "brapi", "fundamentus")):
    for s in order:
        v = (store.get(s, {}).get(t) or {}).get(key)
        if v is not None:
            return v
    return None


def score_universe(universe, store, cfg, missing_fails, prata_min, bronze_min):
    active = [c for c in CRITERIA if cfg[c["key"]]["on"]]
    rows, details = [], {}
    for t in universe:
        present = [s for s in SOURCES if t in store.get(s, {})]
        cells, ok, bad, nd = {}, 0, 0, 0
        for c in active:
            vals = {s: store[s][t].get(c["key"]) for s in present}
            res = judge(c, vals, threshold(c, cfg))
            cells[c["key"]] = res
            if res["status"] == "ok":
                ok += 1
            elif res["status"] == "nd":
                nd += 1
            else:
                bad += 1
        evaluated = ok + bad + (nd if missing_fails else 0)
        adh = ok / evaluated if evaluated else 0.0
        failed = [c["short"] for c in active if cells[c["key"]]["status"] in ("fail", "div")]
        rows.append({
            "Ticker": t, "Empresa": first(store, t, "name") or "", "Preço": first(store, t, "price"),
            "Aderência": adh, "OK": ok, "Avaliados": evaluated,
            "Categoria": categorize(adh, evaluated, ok, prata_min, bronze_min),
            "Fontes": "·".join(LETTER[s] for s in present), "Falhas": ", ".join(failed) or "—",
        })
        details[t] = cells
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(["Aderência", "Ticker"], ascending=[False, True]).reset_index(drop=True)
    return df, details


def close(metric, a, b):
    if metric == "fcl":
        return (a > 0) == (b > 0)
    rel, ab = TOL[metric]
    return abs(a - b) <= max(rel * max(abs(a), abs(b)), ab)


def agreement(store):
    """Concordância de cada fonte com as demais (mediana das outras), por métrica.
    Com 2 fontes a medida é simétrica; com 3+ aparece qual delas destoa."""
    counts, diverg = {}, []
    tickers = set().union(*[set(d) for d in store.values()]) if store else set()
    for t in tickers:
        for m in TOL:
            vals = {s: store[s][t].get(m) for s in store if t in store[s] and store[s][t].get(m) is not None}
            if len(vals) < 2:
                continue
            for s, v in vals.items():
                ref = statistics.median(x for k, x in vals.items() if k != s)
                cnt = counts.setdefault((s, m), [0, 0])
                cnt[0] += 1
                cnt[1] += close(m, v, ref)
            if not all(close(m, a, b) for a in vals.values() for b in vals.values()):
                mid = statistics.median(vals.values())
                spread = (max(vals.values()) - min(vals.values())) / max(abs(mid), 1e-9)
                diverg.append({"Ticker": t, "Métrica": m, "Desvio": spread,
                               **{SOURCES[s]: v for s, v in vals.items()}})
    stats = pd.DataFrame([{"Fonte": SOURCES[s], "Métrica": m, "Comparações": n, "Concordância": a / n}
                          for (s, m), (n, a) in counts.items()])
    div = pd.DataFrame(diverg)
    if not div.empty:
        div = div.sort_values("Desvio", ascending=False).reset_index(drop=True)
    return stats, div
