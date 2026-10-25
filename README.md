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

## Coleta

O TSE limita acessos por IP (responde 429), então cada rodada é enxuta:

- baixa o total do Brasil, o total de cada UF e o acompanhamento de cada UF (~57 arquivos,
  quase sempre 304); o acompanhamento traz seções totalizadas e eleitorado de cada município;
- baixa o resultado só dos municípios cujas seções totalizadas aumentaram (zonas, nas cidades
  grandes), no máximo 1.000 por rodada, priorizando onde há mais eleitores apurados a incorporar;
- ao receber 429/403 a rodada para e a seguinte espera de 1 a 5 min (aviso na tela).

Os níveis do TSE não são publicados em sincronia (municípios atrasam em relação à UF; o
exterior só aparece no total do Brasil). Para o total e por UF, cada UF é projetada pelos
municípios quando eles cobrem 90% do que o total da UF já mostra; senão, pela regra de 3 no
total da UF. O exterior é o total do Brasil menos as UFs. O "apurado" e as seções do topo vêm
do total oficial do Brasil.

Na tela, cada visitante confere a cada 5 s se há projeção nova e só redesenha a página quando
há (~10 KB/min por pessoa sem mudança). Se o TSE ficar 4 min sem dado novo aparece um aviso,
e se a coleta morrer ela é religada na próxima conferência. As fotos são servidas por
`static/` e ficam no cache do navegador.

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

**Atualizações:** cada push na `main` é publicado automaticamente em ~1 minuto, sem deploy manual.
- `app.py` (visual): entra sozinho; quem estiver com a página aberta vê na próxima atualização.
- `src/eleicao/` (coleta/projeção): a coleta roda numa thread iniciada uma vez, então é
  preciso **Reboot app** no menu *Manage app* do Cloud. O reboot zera o histórico do gráfico.
- `requirements.txt`/`pyproject.toml`: o Cloud reinstala e reinicia sozinho.

O Cloud não roda os testes: rode `poetry run pytest` antes do push.

Todas as pessoas que abrem o link compartilham uma única coleta no servidor, então o número
de acessos não aumenta as requisições ao TSE. O histórico do gráfico (`data/`) se perde se o
app reiniciar.
