import json
from pathlib import Path

from eleicao.parse import candidatos, parse_acompanhamento, parse_resultado

DADOS = Path(__file__).parent / "dados"


def test_parse_arquivo_real_do_tse():
    d = json.loads((DADOS / "br-c0001-e006258-u.json").read_text())
    r = parse_resultado(d)
    assert r.ts == 499248 and r.te == 158745502
    assert r.st == 0 and r.est == 0
    assert set(r.votos) == {"13", "22"}
    assert {c["nmu"] for c in candidatos(d)} == {"LULA", "FLAVIO BOLSONARO"}
    assert not r.finalizado


def test_parse_numeros_como_string_e_vazios():
    d = {"s": {"ts": "10", "st": "10"}, "e": {"te": "1000", "est": "1000"},
         "v": {"vb": "", "tvn": "3"},
         "carg": [{"agr": [{"par": [{"cand": [{"n": "13", "vap": "500"}]}]}]}]}
    r = parse_resultado(d)
    assert r.votos == {"13": 500} and r.vb == 0 and r.tvn == 3 and r.finalizado


def test_parse_acompanhamento():
    d = {"abr": [{"cdabr": "sp", "s": {"ts": "100", "st": "40"}},
                 {"cdabr": "br", "s": {"ts": "500", "st": "40"}}]}
    assert parse_acompanhamento(d) == {"sp": (100, 40), "br": (500, 40)}
