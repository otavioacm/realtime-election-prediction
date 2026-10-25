"""Coleta periódica dos resultados do TSE e cálculo da projeção, numa thread em segundo plano.

Para pesar pouco no TSE (que limita acessos por IP e responde 429):
- a cada rodada baixa só o acompanhamento de cada UF (28 arquivos), que traz as seções
  totalizadas e o eleitorado de cada município;
- só baixa o resultado (-u.json) dos municípios cujas seções totalizadas aumentaram desde o
  último resultado que temos deles (nas cidades com várias zonas, das zonas não finalizadas);
- no máximo `ORCAMENTO` arquivos por rodada, priorizando onde há mais eleitores apurados que
  ainda não incorporamos; o resto fica para a rodada seguinte;
- requisições condicionais (ETag/304) numa única conexão HTTP/2;
- ao receber 429/403 a rodada para na hora e a próxima espera mais (1 a 5 min).

Municípios (ou zonas) ainda não baixados entram na projeção com o eleitorado do acompanhamento
e sem votos, e recebem a taxa do nível acima (fallback).

O TSE não publica os níveis em sincronia: os arquivos de município costumam atrasar em relação
ao da UF, e o exterior só aparece no total do Brasil. Por isso, para o total e por UF, cada UF é
projetada pelos municípios quando eles cobrem pelo menos `COBERTURA_MIN` do que o arquivo da
UF já mostra; senão, pela regra de 3 no próprio total da UF. O exterior é o total do Brasil
menos as UFs. A tabela por município usa sempre os arquivos de município.
"""

import asyncio
import logging
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pandas as pd

from . import projecao
from .config import Config, agora
from .parse import Resultado, candidatos, parse_acompanhamento, parse_resultado

log = logging.getLogger(__name__)

PARCIAL_S = 10  # durante uma rodada longa, publica a projeção parcial nesse intervalo
ORCAMENTO = 1000  # máximo de arquivos de resultado por rodada
ESPERA_LIMITE_S = 60  # primeira espera depois de um 429; dobra a cada repetição, até 5 min
ESPERA_LIMITE_MAX_S = 300
COBERTURA_MIN = 0.9  # fração do eleitorado apurado da UF que os municípios precisam cobrir
EXTERIOR = "zz"

Chave = tuple[str, str, str]  # (uf, município, zona); zona "" = município inteiro; ("br", "", "") = Brasil


class LimiteTSE(Exception):
    """O TSE recusou por excesso de acessos (429/403)."""

    def __init__(self, status: int, retry_after: float | None):
        super().__init__(f"HTTP {status}")
        self.retry_after = retry_after


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
        self._ab: dict[tuple[str, str], tuple[int, int, int]] = {}  # (uf, mun) -> (te, ts, st)
        self._etag_ab: dict[str, str | None] = {}
        self._nomes: dict[str, str] = {}
        self._partidos: dict[str, str] = {}
        self._zonas: dict[tuple[str, str], list[str]] = {}  # (uf, mun) -> zonas ([""] = inteiro)
        self.nomes_mun: dict[tuple[str, str], str] = {}
        self.nomes_uf: dict[str, str] = {}
        self.snapshot: Snapshot | None = None
        self.erro: str | None = None
        self.aviso: str | None = None  # mensagem para a tela (ex.: TSE limitando acessos)
        self.rodando = False
        self.ultima_atualizacao: datetime | None = None  # última rodada completa sem erro
        self._versao = 0
        self._limites_seguidos = 0
        self._bloqueado: LimiteTSE | None = None

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
                espera = self.config.intervalo_s
                try:
                    stats = await self.rodada(cli)
                    self._limites_seguidos = 0
                    self.aviso = None
                    if stats.get("erro"):
                        self.erro = f"{stats['erro']} arquivos falharam"
                    else:
                        self.erro = None
                        self.ultima_atualizacao = agora()
                except LimiteTSE as exc:
                    self._limites_seguidos += 1
                    espera = exc.retry_after or min(
                        ESPERA_LIMITE_S * 2 ** (self._limites_seguidos - 1), ESPERA_LIMITE_MAX_S)
                    self.erro = f"TSE limitando acessos ({exc})"
                    self.aviso = (f"O TSE está limitando os acessos. Nova tentativa às "
                                  f"{agora() + timedelta(seconds=espera):%H:%M:%S}.")
                    log.warning("TSE respondeu %s; esperando %ss", exc, espera)
                except Exception as exc:  # a coleta não pode morrer por uma rodada ruim
                    log.exception("falha na rodada")
                    self.erro = f"{type(exc).__name__}: {exc}"
                await asyncio.to_thread(self._acordar.wait, espera)
                self._acordar.clear()

    # ---- coleta ----------------------------------------------------------------------------

    async def _get(self, cli: httpx.AsyncClient, url: str, etag: str | None):
        """GET condicional. Devolve (status, json|None, etag); 429/403 viram LimiteTSE e fazem
        as demais requisições da rodada desistirem sem ir ao servidor."""
        headers = {"If-None-Match": etag} if etag else {}
        for tentativa in range(3):
            async with self._sem:
                if self._bloqueado is not None:
                    raise self._bloqueado
                try:
                    r = await cli.get(url, headers=headers)
                    break
                except httpx.TransportError:
                    if tentativa == 2:
                        raise
            await asyncio.sleep(1 + tentativa)
        if r.status_code in (429, 403):
            try:
                retry = float(r.headers.get("retry-after", ""))
            except ValueError:
                retry = None
            self._bloqueado = LimiteTSE(r.status_code, retry)
            raise self._bloqueado
        if r.status_code == 200:
            return 200, r.json(), r.headers.get("etag")
        return r.status_code, None, etag

    async def _carregar_municipios(self, cli: httpx.AsyncClient) -> None:
        status, d, _ = await self._get(cli, self.config.url_municipios(), None)
        if status != 200:
            raise RuntimeError(f"configuração de municípios indisponível (HTTP {status})")
        cfg = self.config
        for abr in d["abr"]:
            uf = abr["cd"].lower()
            self.nomes_uf[uf] = abr.get("ds", uf.upper())
            for mu in abr["mu"]:
                self.nomes_mun[(uf, mu["cd"])] = mu["nm"]
                zonas = mu.get("z", [])
                por_zona = cfg.por_zona and len(zonas) >= max(cfg.min_zonas, 2)
                self._zonas[(uf, mu["cd"])] = list(zonas) if por_zona else [""]

    def _res(self, chave: Chave) -> Resultado | None:
        return self._estado.get(chave, (None, None))[1]

    async def _atualizar(self, cli: httpx.AsyncClient, chave: Chave, stats: Counter) -> None:
        etag, res = self._estado.get(chave, (None, None))
        try:
            status, d, etag = await self._get(cli, self.config.url_resultado(*chave), etag)
        except httpx.HTTPError:
            stats["erro"] += 1
            return
        stats[status] += 1
        if d is not None:
            novo = parse_resultado(d)
            if novo != res:
                stats["novos"] += 1
            res = novo
            if chave[0] == "br":  # o total do Brasil traz nomes e partidos dos candidatos
                for c in candidatos(d):
                    self._nomes[c["n"]], self._partidos[c["n"]] = c["nmu"], c["sg"]
        self._estado[chave] = (etag, res)

    async def _atualizar_ab(self, cli: httpx.AsyncClient, uf: str, stats: Counter) -> None:
        try:
            status, d, etag = await self._get(cli, self.config.url_acompanhamento(uf),
                                              self._etag_ab.get(uf))
        except httpx.HTTPError:
            stats["erro"] += 1
            return
        stats[f"ab_{status}"] += 1
        if d is None:
            return
        self._etag_ab[uf] = etag
        for cd, valores in parse_acompanhamento(d).items():
            if (uf, cd) in self._zonas and self._ab.get((uf, cd)) != valores:
                self._ab[(uf, cd)] = valores
                stats["novos"] += 1

    def _alvos(self, stats: Counter) -> list[Chave]:
        """Unidades a baixar: municípios cujas seções totalizadas no acompanhamento passaram das
        que já temos, por ordem de eleitores apurados ainda não incorporados, até o orçamento."""
        grupos = []
        for (uf, mun), zonas in self._zonas.items():
            te, ts, st = self._ab.get((uf, mun), (0, 0, 0))
            chaves = [(uf, mun, z) for z in zonas]
            conhecido = sum(r.st for k in chaves if (r := self._res(k)))
            if st <= conhecido:
                continue
            pendentes = [k for k in chaves if not ((r := self._res(k)) and r.finalizado)]
            grupos.append((te * (st - conhecido) / max(ts, 1), pendentes))
        grupos.sort(key=lambda g: -g[0])
        alvos: list[Chave] = []
        for _, chaves in grupos:
            if alvos and len(alvos) + len(chaves) > ORCAMENTO:
                stats["adiados"] += 1
                continue
            alvos += chaves
        return alvos

    async def rodada(self, cli: httpx.AsyncClient) -> Counter:
        """Uma rodada de coleta. Levanta LimiteTSE se o TSE recusar; o que já tiver chegado
        até ali entra na projeção mesmo assim."""
        self.rodando = True
        t0 = time.perf_counter()
        self._sem = asyncio.Semaphore(self.config.concorrencia)
        self._bloqueado = None
        stats: Counter = Counter()
        try:
            if not self._zonas:
                await self._carregar_municipios(cli)

            ufs = sorted({uf for uf, _ in self._zonas})
            self._conferir(stats, await asyncio.gather(
                self._atualizar(cli, ("br", "", ""), stats),
                *(self._atualizar(cli, (uf, "", ""), stats) for uf in ufs),
                *(self._atualizar_ab(cli, uf, stats) for uf in ufs), return_exceptions=True))
            alvos = [] if self._bloqueado else self._alvos(stats)
            tarefa = asyncio.gather(*(self._atualizar(cli, k, stats) for k in alvos),
                                    return_exceptions=True)
            vistos = stats["novos"]
            while not (await asyncio.wait({tarefa}, timeout=PARCIAL_S))[0]:
                if stats["novos"] > vistos and (parcial := self._calcular(stats)) is not None:
                    vistos = stats["novos"]
                    parcial.stats = dict(stats, duracao_s=time.perf_counter() - t0,
                                         fim=agora(), parcial=True)
                    self.snapshot = parcial
            self._conferir(stats, await tarefa)

            stats["requisicoes"] = len(alvos) + 2 * len(ufs) + 1
            if self.snapshot is None or stats["novos"]:
                snap = self._calcular(stats)
                if snap is not None:
                    self._registrar_historico(snap)
                    self.snapshot = snap
            if self.snapshot is not None:
                self.snapshot.stats = dict(stats, duracao_s=time.perf_counter() - t0, fim=agora())
            if self._bloqueado is not None:
                raise self._bloqueado
            return stats
        finally:
            self.rodando = False

    @staticmethod
    def _conferir(stats: Counter, resultados: list) -> None:
        """Erros inesperados de um arquivo não derrubam a rodada, mas são contados e logados."""
        for r in resultados:
            if isinstance(r, Exception) and not isinstance(r, LimiteTSE):
                log.error("falha inesperada ao ler um arquivo: %r", r)
                stats["erro"] += 1

    # ---- projeção --------------------------------------------------------------------------

    def _calcular(self, stats) -> Snapshot | None:
        linhas = []
        cands: set[str] = set(self._nomes)  # candidatos do total do Brasil, mesmo sem votos
        for (uf, mun), zonas in self._zonas.items():
            te_ab, ts_ab, st_ab = self._ab.get((uf, mun), (0, 0, 0))
            te = ts = st = 0
            for z in zonas:
                if (r := self._res((uf, mun, z))) is None:
                    continue
                linhas.append({"uf": uf, "mun": mun, "zona": z, "te": r.te, "est": r.est,
                               "ts": r.ts, "st": r.st, "vb": r.vb, "tvn": r.tvn, **r.votos})
                cands.update(r.votos)
                te, ts, st = te + r.te, ts + r.ts, st + r.st
            # o que ainda não baixamos (o município todo, ou zonas dele): eleitorado e seções do
            # acompanhamento, sem votos conhecidos; a projeção usa a taxa do nível acima
            if te_ab > te or ts_ab > ts:
                linhas.append({"uf": uf, "mun": mun, "zona": "", "te": max(te_ab - te, 0),
                               "est": 0, "ts": max(ts_ab - ts, 0), "st": max(st_ab - st, 0)})
        if not cands:
            return None

        cand = sorted(cands)
        num = ["te", "est", "ts", "st"] + projecao.EXTRAS + cand

        def tabela(linhas):
            df = pd.DataFrame(linhas).reindex(columns=projecao.CHAVES + num)
            df[num] = df[num].fillna(0).astype("int64")
            return df, projecao.projetar(df, cand, fallback=self.config.fallback)

        df_mun, proj_mun = tabela(linhas)
        df, proj = tabela(self._linhas_por_uf(df_mun, stats))
        self._versao += 1
        por_mun = projecao.resumir(df_mun, proj_mun, cand, ["uf", "mun"])
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
            oficial=self._res(("br", "", "")),
            versao=self._versao,
            stats=stats,
        )

    def _linhas_por_uf(self, df_mun: pd.DataFrame, stats) -> list[dict]:
        """Unidades para o total e a visão por UF: os municípios da UF quando estão em dia com o
        arquivo da UF; senão, a UF inteira como uma unidade (dados do próprio arquivo dela)."""
        def linha(uf, r: Resultado, te=None, ts=None) -> dict:
            return {"uf": uf, "mun": "", "zona": "", "te": r.te if te is None else te,
                    "est": r.est, "ts": r.ts if ts is None else ts, "st": r.st,
                    "vb": r.vb, "tvn": r.tvn, **r.votos}

        totais = {uf: self._res((uf, "", "")) for uf in df_mun["uf"].unique()}
        br = self._res(("br", "", ""))
        # o arquivo do exterior não é atualizado: o exterior é o total do Brasil menos as UFs
        nacionais = [r for uf, r in totais.items() if uf != EXTERIOR]
        if EXTERIOR in totais and br is not None and all(r is not None for r in nacionais):
            resto = Resultado(
                st=br.st - sum(r.st for r in nacionais), est=br.est - sum(r.est for r in nacionais),
                vb=br.vb - sum(r.vb for r in nacionais), tvn=br.tvn - sum(r.tvn for r in nacionais),
                votos={n: v - sum(r.votos.get(n, 0) for r in nacionais) for n, v in br.votos.items()})
            atual = totais[EXTERIOR]
            if resto.est > (atual.est if atual else 0) and min(resto.votos.values(), default=0) >= 0:
                totais[EXTERIOR] = resto

        linhas = []
        for uf, grupo in df_mun.groupby("uf", sort=False):
            r = totais.get(uf)
            if r is not None and r.est > 0 and grupo["est"].sum() < COBERTURA_MIN * r.est:
                # municípios atrasados em relação à UF: projeta a UF pelo total dela
                linhas.append(linha(uf, r, te=int(grupo["te"].sum()), ts=int(grupo["ts"].sum())))
                stats["ufs_pelo_total"] += 1
            else:
                linhas += grupo.to_dict("records")
        return linhas

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

