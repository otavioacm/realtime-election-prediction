# Projeção em tempo real – 2º turno 2026

Webapp em Streamlit que acompanha a apuração do TSE e projeta o resultado final com uma
regra de 3 por unidade (zona nos municípios com várias zonas, município nos demais),
somando tudo em município → UF → Brasil.

## Instalação

```bash
poetry install
```

> Se o seu shell exporta `VIRTUAL_ENV` (ex.: pyenv), o Poetry usa esse interpretador em vez
> do `.venv` do projeto. Rode com `env -u VIRTUAL_ENV poetry ...` ou remova a variável.

## Uso

```bash
poetry run streamlit run app.py          # app
poetry run eleicao-coletar --rodadas 3   # coleta no terminal, sem interface
poetry run pytest                         # testes
```

O app é só de visualização (sem controles de configuração) e lê o TSE oficial, presidente,
2º turno. Para testar sem esperar a divulgação, rode com a **demonstração local**, que simula
uma apuração de 10 minutos sobre a configuração real de municípios/zonas:

```bash
ELEICAO_AMBIENTE=demo poetry run streamlit run app.py
```

Os parâmetros da coleta (projeção por zona, fallback, intervalo de 30 s, 8 requisições
simultâneas) ficam em `src/eleicao/config.py`. As fotos dos candidatos estão em `fotos/`.

## Projeção

Para cada unidade: `votos_proj = votos_apurados × te / est` (eleitorado total / eleitorado
das seções totalizadas). Unidades sem nenhuma seção totalizada herdam, com o fallback ligado,
a taxa votos/eleitor observada no município → UF → Brasil. Os percentuais são recalculados
sobre os válidos projetados.

## Desempenho

São ~6.300 arquivos por rodada completa (5.757 municípios; 190 deles com várias zonas
viram 725 zonas). Com 8 requisições simultâneas em HTTP/2, ~70 s. Para não depender disso:

- requisições condicionais (ETag → 304 sem corpo);
- unidades 100% totalizadas deixam de ser consultadas;
- o `ab.json` traz as seções totalizadas por UF: se a soma das nossas unidades da UF bate,
  a UF inteira é pulada;
- unidades maiores primeiro, e a projeção parcial é publicada a cada 10 s durante a rodada.

O histórico de projeções vai para `data/historico-<fonte>.csv` e é recarregado ao reiniciar.

## Código

- `src/eleicao/config.py`: URLs, códigos (eleição 6258, cargo 0001) e parâmetros da coleta
- `src/eleicao/parse.py`: leitura do `-u.json` e do `ab.json`
- `src/eleicao/projecao.py`: regra de 3, fallback e agregação (pandas, vetorizado)
- `src/eleicao/coletor.py`: rodadas assíncronas em segundo plano
- `src/eleicao/demo.py`: simulador da API do TSE
- `app.py`: interface Streamlit

## Publicação (Streamlit Community Cloud)

1. Em <https://share.streamlit.io>, entre com a conta do GitHub dona do repositório.
2. **Create app** → repositório `otavioacm/realtime-election-prediction`, branch `main`,
   arquivo `app.py`. Em **Advanced settings**, Python 3.13.
3. O Cloud instala o `requirements.txt` (que instala este pacote) e lê `.streamlit/config.toml`.

Todas as pessoas que abrem o link compartilham uma única coleta no servidor, então o número
de acessos não aumenta as requisições ao TSE. O histórico do gráfico (`data/`) se perde se o
app reiniciar.
