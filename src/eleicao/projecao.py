"""Projeção por regra de 3: votos_proj = votos_apurados × (eleitorado / eleitorado_apurado).

A projeção é feita em cada unidade (zona nos municípios grandes, município nos demais) e
depois somada. Unidades ainda sem nenhuma seção totalizada recebem, se `fallback=True`, a
taxa votos/eleitor observada no nível acima: município -> UF -> Brasil.
"""

import numpy as np
import pandas as pd

CHAVES = ["uf", "mun", "zona"]
NIVEIS_FALLBACK = (("fallback município", ["uf", "mun"]), ("fallback UF", ["uf"]),
                   ("fallback Brasil", []))
EXTRAS = ["vb", "tvn"]  # brancos e nulos são projetados junto com os candidatos


def _soma(df: pd.DataFrame, nivel: list[str], cols: list[str]) -> pd.DataFrame:
    """Soma de `cols` no grupo `nivel` de cada linha, alinhada ao índice de `df`."""
    if nivel:
        return df.groupby(nivel, sort=False)[cols].transform("sum")
    total = df[cols].sum().to_numpy()
    return pd.DataFrame(np.broadcast_to(total, (len(df), len(cols))), index=df.index, columns=cols)


def projetar(unidades: pd.DataFrame, candidatos: list[str], fallback: bool = True) -> pd.DataFrame:
    """Votos projetados por unidade. Mantém as chaves, te/est/ts/st e adiciona `origem`."""
    cols = candidatos + EXTRAS
    apurado = unidades["est"] > 0
    fator = (unidades["te"] / unidades["est"].where(apurado)).fillna(0.0)

    proj = unidades[CHAVES + ["te", "est", "ts", "st"]].copy()
    proj[cols] = unidades[cols].mul(fator, axis=0)
    origem = pd.Series(np.where(apurado, "apurado", "sem dados"), index=unidades.index)

    if fallback:
        falta = ~apurado & (unidades["te"] > 0)
        for rotulo, nivel in NIVEIS_FALLBACK:
            if not falta.any():
                break
            # unidades sem apuração têm votos e est zerados, então a soma do grupo já é só do apurado
            soma = _soma(unidades, nivel, cols + ["est"])
            usar = falta & (soma["est"] > 0)
            taxa = soma.loc[usar, cols].div(soma.loc[usar, "est"], axis=0)
            proj.loc[usar, cols] = taxa.mul(unidades.loc[usar, "te"], axis=0)
            origem[usar] = rotulo
            falta &= ~usar

    proj["origem"] = origem
    return proj


def resumir(unidades: pd.DataFrame, proj: pd.DataFrame, candidatos: list[str],
            por: list[str]) -> pd.DataFrame:
    """Apurado × projetado agregados em `por` ([] = total geral).

    Colunas: te, est, ts, st, pct_secoes e, para cada candidato n, `n` (votos apurados),
    `n_pct` (% válidos apurado), `n_proj` (votos projetados), `n_proj_pct` (% válidos projetado).
    """
    base = ["te", "est", "ts", "st"]

    def agrupar(df, cols):
        if por:
            return df.groupby(por, sort=True)[cols].sum()
        return df[cols].sum().to_frame("total").T

    apu = agrupar(unidades, base + candidatos)
    prj = agrupar(proj, candidatos).add_suffix("_proj")
    tab = apu.join(prj)
    tab["pct_secoes"] = 100 * tab["st"] / tab["ts"].where(tab["ts"] > 0)

    validos = tab[candidatos].sum(axis=1)
    validos_proj = tab[[f"{n}_proj" for n in candidatos]].sum(axis=1)
    for n in candidatos:
        tab[f"{n}_pct"] = 100 * tab[n] / validos.where(validos > 0)
        tab[f"{n}_proj_pct"] = 100 * tab[f"{n}_proj"] / validos_proj.where(validos_proj > 0)
    return tab
