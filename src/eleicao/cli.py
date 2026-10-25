"""Coleta sem interface: `poetry run eleicao-coletar --rodadas 1`."""

import argparse
import asyncio
import logging

from .coletor import Coletor, LimiteTSE
from .config import AMBIENTES, Config


def _imprimir(coletor: Coletor) -> None:
    snap = coletor.snapshot
    if snap is None:
        print("sem dados ainda")
        return
    s = snap.stats
    print(f"[{s['fim']:%H:%M:%S}] {s['duracao_s']:.1f}s | req={s['requisicoes']} "
          f"ab 200/304={s.get('ab_200', 0)}/{s.get('ab_304', 0)} "
          f"resultados 200/304={s.get(200, 0)}/{s.get(304, 0)} novos={s.get('novos', 0)} "
          f"adiados={s.get('adiados', 0)} erro={s.get('erro', 0)}"
          + (f" | {coletor.erro}" if coletor.erro else ""))
    linha = snap.total.iloc[0]
    partes = [f"{snap.nomes.get(n, n)}: {linha[f'{n}_pct']:.2f}% -> {linha[f'{n}_proj_pct']:.2f}%"
              for n in snap.candidatos]
    print(f"  seções {linha['pct_secoes']:6.2f}% | " + " | ".join(partes))


async def _rodar(config: Config, rodadas: int) -> None:
    coletor = Coletor(config)
    async with coletor.cliente() as cli:
        for i in range(rodadas):
            try:
                await coletor.rodada(cli)
                coletor.erro = None
            except LimiteTSE as exc:
                coletor.erro = f"TSE limitando acessos ({exc})"
            _imprimir(coletor)
            if i < rodadas - 1:
                await asyncio.sleep(config.intervalo_s)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ambiente", choices=list(AMBIENTES), default="oficial")
    p.add_argument("--sem-zona", action="store_true", help="projeta só por município")
    p.add_argument("--sem-fallback", action="store_true")
    p.add_argument("--rodadas", type=int, default=1)
    p.add_argument("--intervalo", type=int, default=30)
    p.add_argument("--concorrencia", type=int, default=8)
    a = p.parse_args()
    logging.basicConfig(level=logging.WARNING)
    config = Config(ambiente=a.ambiente, por_zona=not a.sem_zona,
                    fallback=not a.sem_fallback, intervalo_s=a.intervalo,
                    concorrencia=a.concorrencia)
    asyncio.run(_rodar(config, a.rodadas))


if __name__ == "__main__":
    main()
