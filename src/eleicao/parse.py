"""Leitura do resultado unificado (-u.json, spec EA20)."""

from dataclasses import dataclass, field


def _int(v) -> int:
    if v in (None, ""):
        return 0
    return int(v)


@dataclass(slots=True)
class Resultado:
    ts: int = 0  # seções
    st: int = 0  # seções totalizadas
    te: int = 0  # eleitorado
    est: int = 0  # eleitorado das seções totalizadas
    vb: int = 0  # brancos
    tvn: int = 0  # nulos
    votos: dict[str, int] = field(default_factory=dict)  # número do candidato -> votos apurados
    gerado: str = ""  # "dd/mm/aaaa hh:mm:ss"

    @property
    def finalizado(self) -> bool:
        return self.ts > 0 and self.st >= self.ts


def candidatos(d: dict) -> list[dict]:
    """Candidatos do arquivo: [{"n", "nmu", "sg", "vap"}]."""
    saida = []
    for cargo in d.get("carg", []):
        for agr in cargo.get("agr", []):
            for par in agr.get("par", []):
                for cand in par.get("cand", []):
                    saida.append({"n": cand["n"], "nmu": cand.get("nmu", cand["n"]),
                                  "sg": par.get("sg", ""), "vap": _int(cand.get("vap"))})
    return saida


def parse_resultado(d: dict) -> Resultado:
    s, e, v = d.get("s", {}), d.get("e", {}), d.get("v", {})
    return Resultado(
        ts=_int(s.get("ts")),
        st=_int(s.get("st")),
        te=_int(e.get("te")),
        est=_int(e.get("est")),
        vb=_int(v.get("vb")),
        tvn=_int(v.get("tvn")),
        votos={c["n"]: c["vap"] for c in candidatos(d)},
        gerado=f"{d.get('dg', '')} {d.get('hg', '')}".strip(),
    )


def parse_acompanhamento(d: dict) -> dict[str, tuple[int, int]]:
    """ab.json -> {abrangência: (seções, seções totalizadas)}, inclusive "br"."""
    return {a["cdabr"]: (_int(a["s"]["ts"]), _int(a["s"]["st"])) for a in d.get("abr", [])}
