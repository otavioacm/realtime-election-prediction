"""Simulação local da API do TSE, para testar o app antes/fora da divulgação oficial.

Usa a configuração real de municípios/zonas e gera, por zona, uma apuração que avança no
tempo. Há viés de horário de propósito, como na vida real: o Nordeste e as zonas grandes
apuram mais tarde, e as primeiras seções de cada zona têm tendência um pouco diferente do
total. Arquivos de município/UF/BR são somas das zonas, e o transporte responde ETag/304.
"""

import hashlib
import json
import re
import time
from datetime import datetime

import httpx
import numpy as np

from .config import CARGO, ELEICAO, Config

CANDIDATOS = (("13", "LULA", "PT"), ("22", "FLAVIO BOLSONARO", "PL"))
REGIAO = {**dict.fromkeys("al ba ce ma pb pe pi rn se".split(), (0.66, 0.15)),
          **dict.fromkeys("ac am ap pa ro rr to".split(), (0.50, 0.05)),
          **dict.fromkeys("es mg rj sp".split(), (0.47, 0.0)),
          **dict.fromkeys("pr rs sc".split(), (0.38, 0.0)),
          **dict.fromkeys("df go ms mt".split(), (0.40, 0.0)),
          "zz": (0.45, 0.2)}  # (fração de votos do 13, atraso relativo de início)

_NOME = re.compile(r"^(?P<uf>[a-z]{2})(?P<mun>\d{5})?(?:-z(?P<zona>\d{4}))?-c\d{4}-e\d{6}-u\.json$")


class TransporteDemo(httpx.AsyncBaseTransport):
    def __init__(self, config: Config, duracao_s: float = 600, municipios: dict | None = None,
                 relogio=time.monotonic, semente: int = 2026):
        self.config = config
        self.duracao = duracao_s
        self._municipios = municipios
        self._relogio = relogio
        self._semente = semente
        self._t0: float | None = None
        self._cache: tuple[int, dict] | None = None

    # ---- montagem do eleitorado sintético ---------------------------------------------------

    def _montar(self, municipios: dict) -> None:
        rng = np.random.default_rng(self._semente)
        ufs, chaves = [], []
        for abr in municipios["abr"]:
            uf = abr["cd"].lower()
            for mu in abr["mu"]:
                zonas = mu.get("z") or ["0001"]
                for z in zonas:
                    ufs.append(uf)
                    chaves.append((uf, mu["cd"], z, len(zonas)))
        n = len(chaves)
        n_zonas = np.array([c[3] for c in chaves])
        base, atraso = np.array([REGIAO.get(u, (0.5, 0.0)) for u in ufs]).T

        self.ufs = np.array(ufs)
        self.mun = np.array([c[0] + c[1] for c in chaves])
        self.zona = np.array([c[0] + c[1] + c[2] for c in chaves])
        # zonas de municípios grandes têm mais eleitores
        self.te = np.round(rng.lognormal(9.0, 0.9, n) * np.where(n_zonas > 1, 12, 1)).astype(int) + 200
        self.ts = np.ceil(self.te / 330).astype(int)
        self.share = np.clip(base + rng.normal(0, 0.08, n), 0.05, 0.95)
        self.deriva = rng.normal(0, 0.04, n)  # tendência das primeiras seções da zona
        self.comparecimento = rng.uniform(0.74, 0.86, n)
        tamanho = np.log(self.te) / np.log(self.te).max()
        self.inicio = rng.uniform(0.0, 0.25, n) + atraso
        self.duracao_rel = rng.uniform(0.15, 0.35, n) + 0.3 * tamanho
        escala = (self.inicio + self.duracao_rel).max()
        self.inicio /= escala
        self.duracao_rel /= escala

    def _estado(self) -> dict:
        t = int(self._relogio() - self._t0)
        if self._cache and self._cache[0] == t:
            return self._cache[1]
        p = np.clip((t / self.duracao - self.inicio) / self.duracao_rel, 0, 1)
        st = np.floor(p * self.ts).astype(int)
        frac = st / self.ts
        est = np.round(self.te * frac).astype(int)
        comp = np.floor(est * self.comparecimento)
        vb, tvn = np.floor(comp * 0.02), np.floor(comp * 0.03)
        validos = comp - vb - tvn
        share = np.clip(self.share + self.deriva * (1 - frac), 0, 1)
        v13 = np.floor(validos * share)
        estado = {"st": st, "est": est, "vb": vb.astype(int), "tvn": tvn.astype(int),
                  "13": v13.astype(int), "22": (validos - v13).astype(int)}
        self._cache = (t, estado)
        return estado

    # ---- arquivos ----------------------------------------------------------------------------

    def _somar(self, mascara: np.ndarray) -> dict:
        e = self._estado()
        return {"ts": int(self.ts[mascara].sum()), "te": int(self.te[mascara].sum()),
                **{k: int(v[mascara].sum()) for k, v in e.items()}}

    @staticmethod
    def _pct(a: int, b: int) -> str:
        return f"{100 * a / b:.2f}".replace(".", ",") if b else "0,00"

    def _resultado(self, tp: str, cd: str, mascara: np.ndarray) -> dict:
        s = self._somar(mascara)
        vv = s["13"] + s["22"]
        return {
            "ele": ELEICAO, "tpabr": tp, "cdabr": cd,
            "carg": [{"cd": str(int(CARGO)), "agr": [
                {"par": [{"sg": sg, "cand": [{"n": n, "nmu": nome, "vap": str(s[n]),
                                              "pvap": self._pct(s[n], vv)}]}]}
                for n, nome, sg in CANDIDATOS]}],
            "s": {"ts": str(s["ts"]), "st": str(s["st"]), "pst": self._pct(s["st"], s["ts"])},
            "e": {"te": str(s["te"]), "est": str(s["est"])},
            "v": {"vv": str(vv), "vb": str(s["vb"]), "tvn": str(s["tvn"])},
        }

    def _acompanhamento(self) -> dict:
        abr = []
        for uf in sorted(set(self.ufs)):
            s = self._somar(self.ufs == uf)
            abr.append({"tpabr": "uf", "cdabr": uf, "s": {"ts": str(s["ts"]), "st": str(s["st"])}})
        s = self._somar(np.ones(len(self.ufs), bool))
        abr.append({"tpabr": "br", "cdabr": "br", "s": {"ts": str(s["ts"]), "st": str(s["st"])}})
        return {"ele": ELEICAO, "abr": abr}

    def _resposta(self, request: httpx.Request, corpo: dict) -> httpx.Response:
        etag = '"' + hashlib.md5(json.dumps(corpo, sort_keys=True).encode()).hexdigest() + '"'
        if request.headers.get("if-none-match") == etag:
            return httpx.Response(304, headers={"etag": etag}, request=request)
        agora = datetime.now()
        corpo = corpo | {"dg": f"{agora:%d/%m/%Y}", "hg": f"{agora:%H:%M:%S}"}
        return httpx.Response(200, json=corpo, headers={"etag": etag}, request=request)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if self._municipios is None:
            async with httpx.AsyncClient(timeout=30) as cli:
                r = await cli.get(self.config.url_municipios())
                r.raise_for_status()
                self._municipios = r.json()
        if self._t0 is None:
            self._montar(self._municipios)
            self._t0 = self._relogio()

        if url == self.config.url_municipios():
            return httpx.Response(200, json=self._municipios, request=request)
        nome = url.rsplit("/", 1)[-1]
        if nome.endswith("-ab.json"):
            return self._resposta(request, self._acompanhamento())
        m = _NOME.match(nome)
        if not m:
            return httpx.Response(404, request=request)
        uf, mun, zona = m["uf"], m["mun"], m["zona"]
        if uf == "br":
            mascara, tp, cd = np.ones(len(self.ufs), bool), "br", "br"
        elif zona:
            mascara, tp, cd = self.zona == uf + mun + zona, "zona", zona
        elif mun:
            mascara, tp, cd = self.mun == uf + mun, "mu", mun
        else:
            mascara, tp, cd = self.ufs == uf, "uf", uf
        if not mascara.any():
            return httpx.Response(404, request=request)
        return self._resposta(request, self._resultado(tp, cd, mascara))

    def verdade(self) -> dict[str, float]:
        """Resultado final (100% apurado) da simulação, em % dos válidos."""
        validos = np.floor(self.te * self.comparecimento * 0.95)
        v13 = np.floor(validos * self.share).sum()
        return {"13": 100 * v13 / validos.sum(), "22": 100 * (1 - v13 / validos.sum())}
