import pandas as pd
import pytest

from eleicao.projecao import projetar, resumir

CAND = ["13", "22"]


def unidades(*linhas):
    cols = ["uf", "mun", "zona", "te", "est", "ts", "st", "vb", "tvn", "13", "22"]
    return pd.DataFrame(linhas, columns=cols)


def test_regra_de_tres_do_exemplo():
    # 10% apurado: 400 x 600  ->  100%: 4000 x 6000
    df = unidades(("sp", "00001", "", 10000, 1000, 10, 1, 0, 0, 600, 400))
    p = projetar(df, CAND)
    assert p.loc[0, "13"] == pytest.approx(6000)
    assert p.loc[0, "22"] == pytest.approx(4000)
    assert p.loc[0, "origem"] == "apurado"


def test_soma_das_projecoes_e_nao_projecao_da_soma():
    df = unidades(
        ("ba", "00001", "", 1000, 1000, 1, 1, 0, 0, 800, 200),  # 100% apurado, 80% no 13
        ("sp", "00002", "", 1000, 100, 10, 1, 0, 0, 20, 80),  # 10% apurado, 20% no 13
    )
    tot = resumir(df, projetar(df, CAND), CAND, []).iloc[0]
    assert tot["13_pct"] == pytest.approx(820 / 1100 * 100)  # apurado puxado pela BA
    assert tot["13_proj_pct"] == pytest.approx(50.0)  # (800 + 200) / 2000


def test_zonas_somam_no_municipio():
    df = unidades(
        ("sp", "71072", "0001", 1000, 500, 2, 1, 0, 0, 300, 200),
        ("sp", "71072", "0002", 3000, 300, 10, 1, 0, 0, 100, 200),
    )
    mun = resumir(df, projetar(df, CAND), CAND, ["uf", "mun"]).loc[("sp", "71072")]
    assert mun["13_proj"] == pytest.approx(600 + 1000)
    assert mun["22_proj"] == pytest.approx(400 + 2000)


def test_fallback_hierarquico():
    df = unidades(
        ("sp", "71072", "0001", 1000, 1000, 1, 1, 0, 0, 600, 400),
        ("sp", "71072", "0002", 2000, 0, 1, 0, 0, 0, 0, 0),  # herda da zona irmã
        ("sp", "00010", "", 500, 0, 1, 0, 0, 0, 0, 0),  # herda da UF
        ("ba", "00020", "", 100, 0, 1, 0, 0, 0, 0, 0),  # herda do Brasil
    )
    p = projetar(df, CAND)
    assert list(p["origem"]) == ["apurado", "fallback município", "fallback UF",
                                 "fallback Brasil"]
    assert p.loc[1, ["13", "22"]].tolist() == pytest.approx([1200, 800])
    assert p.loc[2, ["13", "22"]].tolist() == pytest.approx([300, 200])
    assert p.loc[3, ["13", "22"]].tolist() == pytest.approx([60, 40])


def test_sem_fallback_unidade_sem_apuracao_fica_zerada():
    df = unidades(
        ("sp", "00001", "", 1000, 1000, 1, 1, 0, 0, 600, 400),
        ("sp", "00002", "", 1000, 0, 1, 0, 0, 0, 0, 0),
    )
    p = projetar(df, CAND, fallback=False)
    assert p.loc[1, ["13", "22"]].sum() == 0
    assert p.loc[1, "origem"] == "sem dados"


def test_nada_apurado_nao_quebra():
    df = unidades(("sp", "00001", "", 1000, 0, 1, 0, 0, 0, 0, 0))
    tot = resumir(df, projetar(df, CAND), CAND, []).iloc[0]
    assert pd.isna(tot["13_proj_pct"]) and tot["pct_secoes"] == 0
