import asyncio

import pytest

from eleicao.coletor import Coletor
from eleicao.config import Config
from eleicao.demo import TransporteDemo

MUNICIPIOS = {"abr": [
    {"cd": "sp", "ds": "SÃO PAULO", "mu": [
        {"cd": "71072", "nm": "SÃO PAULO", "z": ["0001", "0002", "0003"]},
        {"cd": "00010", "nm": "PEQUENA", "z": ["0004"]},
    ]},
    {"cd": "ba", "ds": "BAHIA", "mu": [{"cd": "38490", "nm": "SALVADOR", "z": ["0005", "0006"]}]},
]}


def rodar(por_zona, tempos):
    agora = [0.0]
    cfg = Config(ambiente="demo", por_zona=por_zona)
    tr = TransporteDemo(cfg, duracao_s=100, municipios=MUNICIPIOS, relogio=lambda: agora[0])
    coletor = Coletor(cfg, transport=tr, pasta_dados=None)

    async def main():
        snaps = []
        async with coletor.cliente() as cli:
            for t in tempos:
                agora[0] = t
                snaps.append(await coletor.rodada(cli))
        return snaps

    return coletor, tr, asyncio.run(main())


def test_unidades_por_zona_e_por_municipio():
    c, _, _ = rodar(True, [0])
    assert len(c.unidades) == 6
    c, _, _ = rodar(False, [0])
    assert len(c.unidades) == 3


def test_apuracao_completa_bate_com_a_verdade_e_para_de_buscar():
    c, tr, snaps = rodar(True, [0, 50, 100, 101])
    final = snaps[2].total.iloc[0]
    assert final["pct_secoes"] == pytest.approx(100)
    assert final["13_proj_pct"] == pytest.approx(final["13_pct"])
    assert final["13_pct"] == pytest.approx(tr.verdade()["13"], abs=0.05)
    # tudo finalizado e batendo com o ab.json: só ab.json + total BR
    assert snaps[3].stats["requisicoes"] == 2
    assert snaps[3].stats["ufs_puladas"] == 2


def test_historico_registra_apenas_rodadas_com_apuracao_nova():
    # t=0: nada apurado; 2º t=50: só 304
    c, _, _ = rodar(True, [0, 50, 50, 100])
    assert len(c.historico) == 2 * 2  # 2 rodadas com mudança × 2 candidatos
