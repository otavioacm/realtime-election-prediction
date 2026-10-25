"""Endereços e códigos da divulgação de resultados do TSE (2º turno 2026, presidente)."""

import os
from dataclasses import dataclass

# ambiente -> (host, ambiente, ciclo)
AMBIENTES = {
    "oficial": ("https://resultados.tse.jus.br", "oficial", "ele2026"),
    "simulado": ("https://resultados-sim.tse.jus.br", "simulado", "simulado2026"),
    # "demo" usa a configuração de municípios oficial, mas os resultados são gerados localmente
    "demo": ("https://resultados.tse.jus.br", "oficial", "ele2026"),
}

ELEICAO = "6258"  # 2º turno federal
CARGO = "0001"  # presidente


@dataclass(frozen=True)
class Config:
    ambiente: str = "oficial"
    por_zona: bool = True  # projeta por zona nos municípios com várias zonas
    min_zonas: int = 2  # a partir de quantas zonas o município é projetado por zona
    fallback: bool = True  # unidades sem apuração herdam a taxa do nível acima
    intervalo_s: int = 30
    concorrencia: int = 8

    @classmethod
    def do_ambiente(cls) -> "Config":
        """Config do app publicado; ELEICAO_AMBIENTE=demo|simulado troca a fonte (testes)."""
        ambiente = os.environ.get("ELEICAO_AMBIENTE", "oficial")
        if ambiente not in AMBIENTES:
            raise ValueError(f"ELEICAO_AMBIENTE inválido: {ambiente}")
        return cls(ambiente=ambiente, intervalo_s=5 if ambiente == "demo" else 30)

    @property
    def base(self) -> str:
        host, ambiente, ciclo = AMBIENTES[self.ambiente]
        return f"{host}/{ambiente}/{ciclo}/{ELEICAO}"

    @property
    def _e(self) -> str:
        return f"e{int(ELEICAO):06d}"

    def url_municipios(self) -> str:
        return f"{self.base}/config/mun-{self._e}-cm.json"

    def url_acompanhamento(self) -> str:
        return f"{self.base}/dados/br/br-{self._e}-ab.json"

    def url_resultado(self, uf: str, mun: str = "", zona: str = "") -> str:
        """Resultado unificado (-u.json) de BR, UF, município ou zona."""
        nome = uf + mun + (f"-z{zona}" if zona else "")
        pasta = "br" if uf == "br" else uf
        return f"{self.base}/dados/{pasta}/{nome}-c{CARGO}-{self._e}-u.json"
