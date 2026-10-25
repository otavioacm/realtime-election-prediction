"""Coleta periódica dos resultados do TSE e cálculo da projeção, numa thread em segundo plano.

Para não ficar lento a cada rodada:
- uma única conexão HTTP/2 com no máximo `concorrencia` requisições simultâneas;
- requisições condicionais (If-None-Match/ETag): arquivo sem mudança volta 304, sem corpo;
- unidades com 100% das seções totalizadas não são mais consultadas;
- o ab.json (1 arquivo) traz as seções totalizadas por UF; se a soma das nossas unidades
  daquela UF já bate com ele, a UF inteira é pulada na rodada;
- unidades maiores são buscadas primeiro e, durante uma rodada longa, a projeção parcial é
  publicada a cada `PARCIAL_S` segundos (cada unidade usa o último dado que temos dela).
"""

import asyncio
import logging
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import httpx
import pandas as pd

from . import projecao
from .config import Config, agora
from .parse import Resultado, candidatos, parse_acompanhamento, parse_resultado

log = logging.getLogger(__name__)

PARCIAL_S = 10

Chave = tuple[str, str, str]  # (uf, município, zona); zona "" = município inteiro; ("br", "", "") = Brasil


@dataclass
class Snapshot:
    momento: datetime
    candidatos: list[str]
    nomes: dict[str, str]  # número -> nome de urna
    partidos: dict[str, str]  # número -> sigla do partido
    brutos: pd.DataFrame  # apurado por unidade (zona/município)
    unidades: pd.DataFrame  # projeção por unidade
    por_mun: pd.DataFrame
    por_uf: pd.DataFrame
    total: pd.DataFrame
    oficial: Resultado | None  # total do Brasil divulgado pelo TSE
    stats: dict = field(default_factory=dict)
    versao: int = 0  # cresce a cada projeção nova; a tela só é redesenhada quando muda


class Coletor:
    def __init__(self, config: Config, transport: httpx.AsyncBaseTransport | None = None,
                 pasta_dados: Path | None = Path("data")):
        self.config = config
        if transport is None and config.ambiente == "demo":
            from .demo import TransporteDemo
            transport = TransporteDemo(config)
        self._transport = transport
        self._estado: dict[Chave, tuple[str | None, Resultado | None]] = {}
        self._nomes: dict[str, str] = {}
        self._partidos: dict[str, str] = {}
        self.unidades: list[Chave] = []
        self._por_uf: dict[str, list[Chave]] = {}
        self.nomes_mun: dict[tuple[str, str], str] = {}
        self.nomes_uf: dict[str, str] = {}
        self.snapshot: Snapshot | None = None
        self.erro: str | None = None
        self.rodando = False
        self.ultima_atualizacao: datetime | None = None  # último dado recebido com sucesso
        self._versao = 0

        self._arquivo_hist = None
        if pasta_dados is not None and config.ambiente != "demo":
            pasta_dados.mkdir(exist_ok=True)
            self._arquivo_hist = pasta_dados / f"historico-{config.ambiente}.csv"
        self.historico = self._ler_historico()

        self._parar = threading.Event()
        self._acordar = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock_thread = threading.Lock()

    # ---- ciclo de vida -------------------------------------------------------------------

    def iniciar(self) -> None:
        """Inicia a coleta. Pode ser chamado sempre: se a thread estiver viva não faz nada, e se
        tiver morrido (erro inesperado) sobe outra, mantendo o estado já coletado."""
        with self._lock_thread:
            if self._thread is not None:
                if self._thread.is_alive():
                    return
                log.error("a thread de coleta tinha parado; reiniciando")
            self._thread = threading.Thread(target=lambda: asyncio.run(self._laco()),
                                            daemon=True, name=f"coletor-{self.config.ambiente}")
            self._thread.start()

    def parar(self) -> None:
        self._parar.set()
        self._acordar.set()

    def atualizar_agora(self) -> None:
        self._acordar.set()

    def cliente(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            http2=True, transport=self._transport, timeout=httpx.Timeout(15.0),
            limits=httpx.Limits(max_connections=self.config.concorrencia),
            headers={"User-Agent": "realtime-election-prediction/0.1"},
        )

    async def _laco(self) -> None:
        async with self.cliente() as cli:
            while not self._parar.is_set():
                try:
                    await self.rodada(cli)
                    self.erro = None
                    self.ultima_atualizacao = agora()
                except Exception as exc:  # a coleta não pode morrer por uma rodada ruim
                    log.exception("falha na rodada")
                    self.erro = f"{type(exc).__name__}: {exc}"
                await asyncio.to_thread(self._acordar.wait, self.config.intervalo_s)
                self._acordar.clear()

    # ---- coleta ----------------------------------------------------------------------------

    async def _get(self, cli: httpx.AsyncClient, url: str, etag: str | None):
        headers = {"If-None-Match": etag} if etag else {}
        for tentativa in range(3):
            try:
                async with self._sem:
                    r = await cli.get(url, headers=headers)
                break
            except httpx.TransportError:
                if tentativa == 2:
                    raise
                await asyncio.sleep(1 + tentativa)
        if r.status_code == 200:
            return 200, r.json(), r.headers.get("etag")
        return r.status_code, None, etag

    async def _carregar_municipios(self, cli: httpx.AsyncClient) -> None:
        status, d, _ = await self._get(cli, self.config.url_municipios(), None)
        if status != 200:
            raise RuntimeError(f"configuração de municípios indisponível (HTTP {status})")
        cfg = self.config
        unidades: list[Chave] = []
        for abr in d["abr"]:
            uf = abr["cd"].lower()
            self.nomes_uf[uf] = abr.get("ds", uf.upper())
            for mu in abr["mu"]:
                self.nomes_mun[(uf, mu["cd"])] = mu["nm"]
                zonas = mu.get("z", [])
                if cfg.por_zona and len(zonas) >= max(cfg.min_zonas, 2):
                    unidades += [(uf, mu["cd"], z) for z in zonas]
                else:
                    unidades.append((uf, mu["cd"], ""))
        self.unidades = unidades
        self._por_uf = {}
        for k in unidades:
            self._por_uf.setdefault(k[0], []).append(k)

    async def _atualizar(self, cli: httpx.AsyncClient, chave: Chave, stats: Counter) -> None:
        etag, res = self._estado.get(chave, (None, None))
        try:
            status, d, etag = await self._get(cli, self.config.url_resultado(*chave), etag)
        except httpx.HTTPError:
            stats["erro"] += 1
            return
        stats[status] += 1
        if d is not None:
            res = parse_resultado(d)
            if chave[0] == "br":  # o total do Brasil traz nomes e partidos dos candidatos
                for c in candidatos(d):
                    self._nomes[c["n"]], self._partidos[c["n"]] = c["nmu"], c["sg"]
        self._estado[chave] = (etag, res)

    def _alvos(self, acomp: dict[str, tuple[int, int]], stats: Counter) -> list[Chave]:
        alvos = []
        for uf, chaves in self._por_uf.items():
            res = [self._estado.get(k, (None, None))[1] for k in chaves]
            completo = all(r is not None for r in res)
            if uf in acomp and completo and sum(r.st for r in res) == acomp[uf][1]:
                stats["ufs_puladas"] += 1
                continue
            alvos += [k for k, r in zip(chaves, res) if r is None or not r.finalizado]
        # sem dado ainda primeiro, depois do maior eleitorado para o menor
        return sorted(alvos, key=lambda k: -(r.te if (r := self._estado.get(k, (None, None))[1])
                                             else float("inf")))

    async def rodada(self, cli: httpx.AsyncClient) -> Snapshot | None:
        self.rodando = True
        t0 = time.perf_counter()
        self._sem = asyncio.Semaphore(self.config.concorrencia)
        stats: Counter = Counter()
        try:
            if not self.unidades:
                await self._carregar_municipios(cli)

            status, ab, _ = await self._get(cli, self.config.url_acompanhamento(), None)
            acomp = parse_acompanhamento(ab) if ab else {}
            alvos = self._alvos(acomp, stats)
            totais = [("br", "", "")]
            tarefa = asyncio.gather(*(self._atualizar(cli, k, stats) for k in totais + alvos))
            vistos = 0
            while not (await asyncio.wait({tarefa}, timeout=PARCIAL_S))[0]:
                if stats[200] > vistos and (parcial := self._calcular(stats)) is not None:
                    vistos = stats[200]
                    parcial.stats = dict(stats, duracao_s=time.perf_counter() - t0,
                                         fim=agora(), acompanhamento=acomp, parcial=True)
                    self.snapshot = parcial
                    self.ultima_atualizacao = agora()
            await tarefa

            stats["requisicoes"] = len(alvos) + len(totais) + 1
            stats["unidades"] = len(self.unidades)
            stats["sem_arquivo"] = sum(1 for k in self.unidades
                                       if self._estado.get(k, (None, None))[1] is None)
            mudou = self.snapshot is None or stats[200] > 0
            snap = self._calcular(stats) if mudou else self.snapshot
            if snap is not None:
                snap.stats = dict(stats, duracao_s=time.perf_counter() - t0,
                                  fim=agora(), acompanhamento=acomp)
                if mudou:
                    self._registrar_historico(snap)
                self.snapshot = snap
            return snap
        finally:
            self.rodando = False

    # ---- projeção --------------------------------------------------------------------------

    def _calcular(self, stats) -> Snapshot | None:
        linhas = []
        cands: set[str] = set()
        for uf, mun, zona in self.unidades:
            res = self._estado.get((uf, mun, zona), (None, None))[1]
            linha = {"uf": uf, "mun": mun, "zona": zona}
            if res is not None:
                linha |= {"te": res.te, "est": res.est, "ts": res.ts, "st": res.st,
                          "vb": res.vb, "tvn": res.tvn, **res.votos}
                cands.update(res.votos)
            linhas.append(linha)
        if not cands:
            return None

        cand = sorted(cands)
        num = ["te", "est", "ts", "st"] + projecao.EXTRAS + cand
        df = pd.DataFrame(linhas).reindex(columns=projecao.CHAVES + num)
        df[num] = df[num].fillna(0).astype("int64")

        proj = projecao.projetar(df, cand, fallback=self.config.fallback)
        self._versao += 1
        por_mun = projecao.resumir(df, proj, cand, ["uf", "mun"])
        por_mun.insert(0, "municipio", [self.nomes_mun.get(i, i[1]) for i in por_mun.index])
        return Snapshot(
            momento=agora(),
            candidatos=cand,
            nomes=dict(self._nomes),
            partidos=dict(self._partidos),
            brutos=df,
            unidades=proj,
            por_mun=por_mun,
            por_uf=projecao.resumir(df, proj, cand, ["uf"]),
            total=projecao.resumir(df, proj, cand, []),
            oficial=self._estado.get(("br", "", ""), (None, None))[1],
            versao=self._versao,
            stats=stats,
        )

    # ---- histórico ---------------------------------------------------------------------------

    def _registrar_historico(self, snap: Snapshot) -> None:
        linha = snap.total.iloc[0]
        if not linha["st"]:
            return  # antes da divulgação não há o que registrar
        novas = [{
            "momento": snap.momento.isoformat(timespec="seconds"),
            "pct_secoes": linha["pct_secoes"], "candidato": n, "nome": snap.nomes.get(n, n),
            "pct_apurado": linha[f"{n}_pct"], "pct_projetado": linha[f"{n}_proj_pct"],
        } for n in snap.candidatos]
        self.historico += novas
        if self._arquivo_hist is not None and novas:
            df = pd.DataFrame(novas)
            df.to_csv(self._arquivo_hist, mode="a", index=False,
                      header=not self._arquivo_hist.exists())

    def _ler_historico(self) -> list[dict]:
        if self._arquivo_hist is None or not self._arquivo_hist.exists():
            return []
        df = pd.read_csv(self._arquivo_hist, dtype={"candidato": str})
        return df.to_dict("records")

