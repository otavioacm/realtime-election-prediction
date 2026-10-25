import asyncio
import re

import httpx
import pytest

from eleicao.coletor import Coletor, LimiteTSE
from eleicao.config import Config
from eleicao.demo import TransporteDemo

MUNICIPIOS = {"abr": [
    {"cd": "sp", "ds": "SÃO PAULO", "mu": [
        {"cd": "71072", "nm": "SÃO PAULO", "z": ["0001", "0002", "0003"]},
        {"cd": "00010", "nm": "PEQUENA", "z": ["0004"]},
    ]},
    {"cd": "ba", "ds": "BAHIA", "mu": [{"cd": "38490", "nm": "SALVADOR", "z": ["0005", "0006"]}]},
]}


def rodar(por_zona, tempos, transporte=None):
    agora = [0.0]
    cfg = Config(ambiente="demo", por_zona=por_zona)
    tr = TransporteDemo(cfg, duracao_s=100, municipios=MUNICIPIOS, relogio=lambda: agora[0])
    coletor = Coletor(cfg, transport=transporte(tr) if transporte else tr, pasta_dados=None)

    async def main():
        snaps, stats = [], []
        async with coletor.cliente() as cli:
            for t in tempos:
                agora[0] = t
                try:
                    stats.append(await coletor.rodada(cli))
                except LimiteTSE as exc:
                    stats.append(exc)
                snaps.append(coletor.snapshot)
        return snaps, stats

    snaps, stats = asyncio.run(main())
    return coletor, tr, snaps, stats


def test_unidades_por_zona_e_por_municipio():
    c, *_ = rodar(True, [0])
    assert sum(len(z) for z in c._zonas.values()) == 6
    c, *_ = rodar(False, [0])
    assert sum(len(z) for z in c._zonas.values()) == 3


def test_antes_da_apuracao_so_baixa_o_acompanhamento():
    c, _, snaps, stats = rodar(True, [0])
    assert stats[0]["requisicoes"] == 1 + 2 + 2  # total BR + total e acompanhamento das 2 UFs
    assert snaps[0] is not None and snaps[0].total.iloc[0]["te"] > 0  # eleitorado já aparece


def test_apuracao_completa_bate_com_a_verdade_e_para_de_buscar():
    c, tr, snaps, stats = rodar(True, [0, 50, 100, 101])
    final = snaps[2].total.iloc[0]
    assert final["pct_secoes"] == pytest.approx(100)
    assert final["13_proj_pct"] == pytest.approx(final["13_pct"])
    assert final["13_pct"] == pytest.approx(tr.verdade()["13"], abs=0.05)
    # nada mudou: só os totais (BR e UFs) e o acompanhamento das UFs, todos 304
    assert stats[3]["requisicoes"] == 5
    assert stats[3].get(200, 0) == 0 and stats[3].get(304) == 3 and stats[3].get("ab_304") == 2


def test_eleitorado_completo_mesmo_sem_baixar_todos_os_municipios(monkeypatch):
    monkeypatch.setattr("eleicao.coletor.ORCAMENTO", 1)
    c, tr, snaps, stats = rodar(True, [0, 60])
    assert stats[1]["adiados"] > 0
    assert snaps[1].total.iloc[0]["te"] == tr.te.sum()  # quem não foi baixado entra pelo ab
    # nas rodadas seguintes o que ficou para trás é alcançado
    c, tr, snaps, stats = rodar(True, [0, 60, 60, 60, 60, 60, 60, 60])
    assert snaps[-1].total.iloc[0]["13"] == tr._somar(tr.ufs == tr.ufs)["13"]


def test_limite_do_tse_interrompe_a_rodada_e_preserva_o_que_chegou():
    class Limitado(httpx.AsyncBaseTransport):
        def __init__(self, interno):
            self.interno, self.pedidos, self.limitar = interno, 0, True

        async def handle_async_request(self, request):
            self.pedidos += 1
            if self.limitar and re.search(r"/[a-z]{2}\d{5}", str(request.url)):  # municípios
                return httpx.Response(429, headers={"retry-after": "7"}, request=request)
            return await self.interno.handle_async_request(request)

    transportes = []
    c, tr, snaps, stats = rodar(True, [0, 60],
                                lambda t: transportes.append(Limitado(t)) or transportes[-1])
    antes = transportes[0].pedidos
    assert isinstance(stats[1], LimiteTSE) and stats[1].retry_after == 7
    assert snaps[1] is not None  # acompanhamento e total BR já tinham chegado
    assert snaps[1].total.iloc[0]["st"] > 0  # e as seções do acompanhamento aparecem
    # (na 1ª rodada nada estava apurado, então nenhum resultado foi pedido)
    assert stats[0]["requisicoes"] == 5 and antes >= 5


def test_historico_registra_apenas_rodadas_com_apuracao_nova():
    # t=0: nada apurado; 2º t=50: só 304
    c, *_ = rodar(True, [0, 50, 50, 100])
    assert len(c.historico) == 2 * 2  # 2 rodadas com mudança × 2 candidatos


def test_versao_so_muda_com_dado_novo():
    _, _, snaps, _ = rodar(True, [10, 50, 50])  # a 3ª rodada só recebe 304
    assert snaps[1].versao > snaps[0].versao
    assert snaps[2].versao == snaps[1].versao


def test_iniciar_religa_a_coleta_se_a_thread_morreu():
    import threading

    c = Coletor(Config(ambiente="demo"), transport=TransporteDemo(Config(ambiente="demo")),
                pasta_dados=None)
    execucoes, segurar = [], threading.Event()

    async def laco_falso():
        execucoes.append(1)
        segurar.wait(5)

    c._laco = laco_falso
    c.iniciar()
    c.iniciar()  # thread viva: não sobe outra
    segurar.set()
    c._thread.join(5)
    assert len(execucoes) == 1

    c.iniciar()  # thread morta: sobe outra
    c._thread.join(5)
    assert len(execucoes) == 2


def test_municipios_atrasados_usam_o_total_da_uf():
    """Como no TSE às vezes: arquivos de município atrás do da UF. O total segue o da UF."""
    class MunicipiosParados(httpx.AsyncBaseTransport):
        def __init__(self, interno):
            self.interno = interno

        async def handle_async_request(self, request):
            if re.search(r"/[a-z]{2}\d{5}", str(request.url)):
                return httpx.Response(404, request=request)
            return await self.interno.handle_async_request(request)

    c, tr, snaps, stats = rodar(True, [0, 60], MunicipiosParados)
    total = snaps[1].total.iloc[0]
    assert stats[1]["ufs_pelo_total"] == 2
    assert total["13"] == tr._somar(tr.ufs == tr.ufs)["13"]  # apurado = total oficial
    assert total["13_proj_pct"] > 0
