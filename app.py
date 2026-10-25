"""Acompanhamento e projeção do 2º turno 2026: `poetry run streamlit run app.py`.

Painel só de visualização, pensado para celular. A fonte é o TSE oficial; para testar,
ELEICAO_AMBIENTE=demo.
"""

import time
import unicodedata
from datetime import timedelta

import altair as alt
import pandas as pd
import streamlit as st

from eleicao.coletor import Coletor, Snapshot
from eleicao.config import Config, agora

CORES = {"13": "#d62828", "22": "#1d4e9e"}  # Lula vermelho, Flávio azul escuro
# servidas por static/ (enableStaticServing): o navegador baixa uma vez e guarda
FOTOS = {"13": "./app/static/lula.jpg", "22": "./app/static/flavio.jpg"}
CINZA = "#8a8984"
VIGIA_S = 5  # de quanto em quanto tempo cada tela confere se há projeção nova
SEM_DADOS = timedelta(minutes=4)  # sem dado novo do TSE por mais que isso, avisa na tela

st.set_page_config(page_title="Projeção 2º turno 2026", page_icon="🗳️", layout="centered",
                   initial_sidebar_state="collapsed")


@st.cache_resource
def coletor() -> Coletor:
    c = Coletor(Config.do_ambiente())
    c.iniciar()
    # o filtro de estados fica fora do refresh automático: espera a lista de municípios (~1 s)
    limite = time.monotonic() + 20
    while not c.nomes_uf and c.erro is None and time.monotonic() < limite:
        time.sleep(0.2)
    return c


def votos(v: float) -> str:
    return f"{v:,.0f}".replace(",", ".")


def pct(v: float, casas: int = 2) -> str:
    return "—" if pd.isna(v) else f"{v:.{casas}f}%".replace(".", ",")


def curto(snap: Snapshot, n: str) -> str:
    return snap.nomes.get(n, n).split()[0].title()


MINUSCULAS = {"de", "da", "do", "das", "dos", "e", "d'"}


def titulo(s: str) -> str:
    """'RIO DE JANEIRO' -> 'Rio de Janeiro'."""
    return " ".join(p.lower() if i and p.lower() in MINUSCULAS else p.capitalize()
                    for i, p in enumerate(s.split()))


def sem_acento(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)).lower()


st.markdown("""
<style>
[data-testid="stMainBlockContainer"] { padding-top: 2.75rem; padding-bottom: 3rem; }
@media (max-width: 640px) {
  [data-testid="stMainBlockContainer"] { padding-left: 1rem; padding-right: 1rem; }
}
.titulo { font-size: clamp(1.35rem, 5.5vw, 1.9rem); font-weight: 800; color: #1f1f1d;
          line-height: 1.15; margin: 0; }
.status { display: flex; align-items: center; gap: 8px; font-size: .8rem; color: #6b6a66;
          margin: 4px 0 14px; }
.aovivo { display: inline-flex; align-items: center; gap: 5px; font-weight: 800; color: #d62828;
          letter-spacing: .06em; }
.aovivo i { width: 8px; height: 8px; border-radius: 50%; background: #d62828;
            animation: pulsa 1.6s ease-in-out infinite; }
@keyframes pulsa { 50% { opacity: .25; } }
.secoes { background: #fff; border: 1px solid #e3e2de; border-radius: 14px; padding: 12px 14px; }
.secoes-lin { display: flex; justify-content: space-between; align-items: baseline;
              color: #52514e; font-size: .9rem; }
.secoes-lin b { font-size: 1.35rem; color: #1f1f1d; }
.barra { height: 8px; border-radius: 4px; background: #ecebe8; margin-top: 8px; overflow: hidden; }
.barra > div { height: 100%; background: #52514e; }
.cands { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 10px; }
.cand { position: relative; background: #fff; border: 2px solid var(--cor); border-radius: 14px;
        padding: 14px 10px 12px; text-align: center; }
.chip { display: inline-block; margin-top: 8px; font-size: .68rem; font-weight: 800;
        color: #fff; background: var(--cor); border-radius: 999px; padding: 2px 8px; }
.anel { width: clamp(72px, 22vw, 104px); aspect-ratio: 1; margin: 0 auto; border-radius: 50%;
        padding: 4px; background: conic-gradient(var(--cor) 0 var(--p), #ecebe8 var(--p) 100%); }
.anel img { width: 100%; height: 100%; border-radius: 50%; border: 3px solid #fff;
            object-fit: cover; display: block; background: #fff; }
.nome { font-weight: 800; color: #1f1f1d; font-size: clamp(.85rem, 3.6vw, 1.1rem);
        line-height: 1.2; margin-top: 8px; min-height: 2.4em; display: flex;
        align-items: center; justify-content: center; }
.partido { font-size: .72rem; font-weight: 700; color: #6b6a66; letter-spacing: .08em; }
.rot { font-size: .68rem; font-weight: 700; color: #6b6a66; letter-spacing: .08em;
       text-transform: uppercase; margin-top: 10px; }
.proj { font-size: clamp(1.7rem, 8vw, 2.5rem); font-weight: 800; color: var(--cor);
        line-height: 1.05; }
.apu { font-size: .85rem; color: #52514e; margin-top: 6px; }
.apu b { color: #1f1f1d; }
.votos { font-size: .75rem; color: #6b6a66; }
.split { display: flex; gap: 2px; height: 14px; margin-top: 14px; position: relative; }
.split > div { height: 100%; }
.split > div:first-child { border-radius: 4px 0 0 4px; }
.split > div:last-of-type { border-radius: 0 4px 4px 0; }
.split .meio { position: absolute; left: 50%; top: -4px; bottom: -4px; width: 2px;
               background: #1f1f1d; }
.frente { font-size: .9rem; color: #1f1f1d; margin-top: 8px; text-align: center; }
.legenda { display: flex; flex-wrap: wrap; gap: 4px 16px; font-size: .8rem; color: #52514e; }
.legenda span { display: inline-flex; align-items: center; gap: 6px; }
.legenda i { display: inline-block; width: 18px; border-radius: 2px; }
</style>
""", unsafe_allow_html=True)


# ---- blocos ----------------------------------------------------------------------------------

def card(snap: Snapshot, linha: pd.Series, n: str, lider: bool) -> str:
    proj, apu = linha[f"{n}_proj_pct"], linha[f"{n}_pct"]
    img = FOTOS.get(n)
    return f"""
<div class="cand" style="--cor:{CORES.get(n, CINZA)}; --p:{0 if pd.isna(proj) else proj:.2f}%">
  <div class="anel">{f'<img src="{img}" alt="" width="108" height="108">' if img else ''}</div>
  <div class="nome">{snap.nomes.get(n, n)}</div>
  <div class="partido">{snap.partidos.get(n, "")} · {n}</div>
  <div class="rot">Projeção</div>
  <div class="proj">{pct(proj)}</div>
  <div class="apu">Apurado <b>{pct(apu)}</b></div>
  <div class="votos">{votos(linha[n])} votos</div>
  <span class="chip"{'' if lider else ' style="visibility:hidden"'}>À frente na projeção</span>
</div>"""


def topo(snap: Snapshot, linha: pd.Series) -> str:
    ordem = sorted(snap.candidatos,
                   key=lambda n: -(0 if pd.isna(linha[f"{n}_proj_pct"]) else linha[f"{n}_proj_pct"]))
    tem_proj = not pd.isna(linha[f"{ordem[0]}_proj_pct"])
    secoes = 0 if pd.isna(linha["pct_secoes"]) else linha["pct_secoes"]
    cards = "".join(card(snap, linha, n, tem_proj and i == 0) for i, n in enumerate(ordem))

    if tem_proj and len(ordem) >= 2:
        a, b = ordem[:2]
        fatias = "".join(f'<div style="width:{linha[f"{n}_proj_pct"]:.2f}%;'
                         f'background:{CORES.get(n, CINZA)}"></div>' for n in ordem)
        dif = pct(linha[f"{a}_proj_pct"] - linha[f"{b}_proj_pct"]).rstrip("%")
        rodape = (f'<div class="split">{fatias}<span class="meio"></span></div>'
                  f'<div class="frente"><b>{curto(snap, a)}</b> à frente na projeção por '
                  f'<b>{dif} p.p.</b></div>')
    else:
        rodape = '<div class="frente">Divulgação dos resultados a partir das 17h (Brasília).</div>'

    return f"""
<div class="secoes">
  <div class="secoes-lin"><span>Seções totalizadas</span><b>{pct(secoes)}</b></div>
  <div class="barra"><div style="width:{secoes:.2f}%"></div></div>
</div>
<div class="cands">{cards}</div>
{rodape}"""


def aviso_confiabilidade(unidades: pd.DataFrame) -> None:
    te = unidades.groupby("origem")["te"].sum()
    if te.sum() == 0 or unidades["st"].sum() == 0:
        return
    apurado = 100 * te.get("apurado", 0) / te.sum()
    if apurado < 30:
        st.warning(f"Projeção ainda instável: só {pct(apurado, 0)} do eleitorado está em "
                   "cidades/zonas que já têm votos apurados.", icon="⚠️")


def grafico(c: Coletor, snap: Snapshot) -> None:
    h = pd.DataFrame(c.historico)
    if not h.empty:
        h = h[h["pct_secoes"] >= 1]  # o primeiro 1% é muito ruidoso e achata o resto do gráfico
    if h.empty or h["momento"].nunique() < 2:
        st.info("O gráfico aparece quando 1% das seções estiver apurado.")
        return
    h["momento"] = pd.to_datetime(h["momento"])
    h = h.melt(id_vars=["momento", "pct_secoes", "candidato"],
               value_vars=["pct_apurado", "pct_projetado"], var_name="tipo", value_name="pct")
    h["tipo"] = h["tipo"].map({"pct_apurado": "Apurado", "pct_projetado": "Projeção"})
    h["nome"] = h["candidato"].map(lambda n: curto(snap, n))

    itens = [f'<span><i style="height:4px;background:{CORES.get(n, CINZA)}"></i>'
             f'{curto(snap, n)}</span>' for n in snap.candidatos]
    itens += [f'<span><i style="height:4px;background:{CINZA}"></i>Apurado</span>',
              f'<span><i style="height:2px;background:{CINZA};opacity:.45"></i>Projeção</span>']
    st.markdown(f'<div class="legenda">{"".join(itens)}</div>', unsafe_allow_html=True)

    tipos = ["Apurado", "Projeção"]
    escala_cor = alt.Scale(domain=[curto(snap, n) for n in snap.candidatos],
                           range=[CORES.get(n, CINZA) for n in snap.candidatos])
    escala_op = alt.Scale(domain=tipos, range=[1, 0.4])
    x = alt.X("momento:T", title=None,
              axis=alt.Axis(format="%H:%M", grid=False, tickMinStep=60_000, labelOverlap=True))
    y = alt.Y("pct:Q", title=None, scale=alt.Scale(zero=False),
              axis=alt.Axis(labelExpr="datum.value + '%'", tickCount=5))

    base = alt.Chart(h).encode(x=x)
    perto = alt.selection_point(nearest=True, on="pointerover", fields=["momento"],
                                empty=False, clear="pointerout")
    linhas = base.mark_line(interpolate="monotone").encode(
        y=y, color=alt.Color("nome:N", legend=None, scale=escala_cor),
        strokeWidth=alt.StrokeWidth("tipo:N", legend=None,
                                    scale=alt.Scale(domain=tipos, range=[4, 2])),
        opacity=alt.Opacity("tipo:N", legend=None, scale=escala_op), detail="tipo:N")
    alvo = base.mark_rule(opacity=0, strokeWidth=24).add_params(perto)
    regua = base.mark_rule(color=CINZA, strokeWidth=1).transform_filter(perto)
    pontos = base.mark_point(size=70, filled=True, stroke="white", strokeWidth=2).encode(
        y=y, color=alt.Color("nome:N", legend=None, scale=escala_cor),
        opacity=alt.Opacity("tipo:N", legend=None, scale=escala_op),
        tooltip=[alt.Tooltip("momento:T", title="Horário", format="%H:%M"),
                 alt.Tooltip("nome:N", title="Candidato"), alt.Tooltip("tipo:N", title="Série"),
                 alt.Tooltip("pct:Q", title="% válidos", format=".2f"),
                 alt.Tooltip("pct_secoes:Q", title="% seções", format=".1f")],
    ).transform_filter(perto)
    meio = alt.Chart(pd.DataFrame({"y": [50]})).mark_rule(
        color=CINZA, strokeDash=[3, 3], strokeWidth=1).encode(y="y:Q")
    grafico = alt.layer(meio, linhas, alvo, regua, pontos).properties(height=240)
    st.altair_chart(grafico.configure_axis(labelFontSize=11), width="stretch")


# ---- página ---------------------------------------------------------------------------------

st.markdown('<div class="titulo">Presidente · 2º turno 2026</div>', unsafe_allow_html=True)


@st.fragment(run_every=VIGIA_S)
def vigia(c: Coletor, versao_exibida: int | None) -> None:
    """Roda sozinho a cada VIGIA_S s, sem redesenhar o resto da página.

    Mostra que o painel está vivo, avisa se o TSE parou de mandar dados, religa a coleta se
    ela tiver morrido e, quando há projeção nova, redesenha a página inteira.
    """
    c.iniciar()  # não faz nada se a coleta estiver rodando
    snap = c.snapshot
    if (snap.versao if snap else None) != versao_exibida:
        st.rerun()
    ultima = c.ultima_atualizacao
    if ultima is None:
        return
    if agora() - ultima > SEM_DADOS:
        st.warning(f"Sem novos dados do TSE desde {ultima:%H:%M:%S}. Os números podem estar "
                   "desatualizados; tentando de novo.", icon="⚠️")
    else:
        st.markdown(f'<div class="status"><span class="aovivo"><i></i>AO VIVO</span>'
                    f"atualizado às {ultima:%H:%M:%S}</div>", unsafe_allow_html=True)


def tabela(c: Coletor, snap: Snapshot) -> None:
    uf = st.session_state.get("uf", "todas")
    busca = sem_acento(st.session_state.get("busca", "").strip())

    if busca:
        tab = snap.por_mun if uf == "todas" else snap.por_mun.loc[[uf]]
        tab = tab[tab["municipio"].map(sem_acento).str.contains(busca, regex=False)]
        local = [f"{titulo(m)} ({u.upper()})" for (u, _), m in zip(tab.index, tab["municipio"])]
    elif uf == "todas":
        tab = snap.por_uf
        local = [titulo(c.nomes_uf.get(u, u)) for u in tab.index]
    else:
        tab = snap.por_mun.loc[uf]
        local = [titulo(m) for m in tab["municipio"]]
    ordem = tab["te"].to_numpy().argsort()[::-1]  # maior eleitorado primeiro
    tab, local = tab.iloc[ordem], [local[i] for i in ordem]
    if tab.empty:
        st.info("Nenhum município encontrado.")
        return

    def celula(a, p):
        return "—" if pd.isna(p) else f"{pct(a, 1).rstrip('%')} → {pct(p, 1).rstrip('%')}"

    df = pd.DataFrame({"Local": local,
                       "Seções": [pct(v, 1) for v in tab["pct_secoes"]]})
    for n in snap.candidatos:
        df[curto(snap, n)] = [celula(a, p) for a, p in zip(tab[f"{n}_pct"], tab[f"{n}_proj_pct"])]

    # quem lidera a projeção em cada linha aparece em negrito, na cor do candidato
    proj = tab[[f"{n}_proj_pct" for n in snap.candidatos]].fillna(-1).to_numpy()
    lider = [snap.candidatos[i] if proj[j, i] >= 0 else None
             for j, i in enumerate(proj.argmax(axis=1))]

    def estilo(linha):
        n = lider[linha.name]
        alvo = curto(snap, n) if n else None
        return [f"color: {CORES.get(n, CINZA)}; font-weight: 700" if col == alvo else ""
                for col in linha.index]

    config = {"Local": st.column_config.TextColumn(width=125, pinned=True),
              "Seções": st.column_config.TextColumn(width=60)}
    config |= {curto(snap, n): st.column_config.TextColumn(width=88) for n in snap.candidatos}
    st.dataframe(df.style.apply(estilo, axis=1), hide_index=True, width="stretch",
                 height=min(36 * (len(df) + 1) + 3, 470), column_config=config)


c = coletor()
snap = c.snapshot
vigia(c, snap.versao if snap else None)
if snap is None:
    if c.erro:
        st.error("Não foi possível ler os dados do TSE agora. Tentando de novo…")
    else:
        st.info("Carregando os dados do TSE. A primeira leitura leva cerca de 1 minuto.")
    st.stop()

st.markdown(topo(snap, snap.total.iloc[0]), unsafe_allow_html=True)
aviso_confiabilidade(snap.unidades)
st.markdown("#### Evolução")
grafico(c, snap)

st.markdown("#### Por estado e município")
st.caption("Em cada célula: % apurado → % projetado. Em destaque, quem lidera a projeção.")
ufs = sorted(c.nomes_uf, key=lambda u: c.nomes_uf[u])
st.selectbox("Estado", ["todas"] + ufs, key="uf",
             format_func=lambda u: "Todos os estados" if u == "todas"
             else f"{titulo(c.nomes_uf[u])} ({u.upper()})")
st.text_input("Buscar município", key="busca", placeholder="Ex.: Campinas")
tabela(c, snap)

with st.expander("Como a projeção é calculada"):
    st.markdown("""
**Regra de 3 em cada município** (e em cada zona eleitoral, nas cidades com várias zonas):
`votos projetados = votos apurados × eleitorado total / eleitorado das seções já apuradas`.
Ex.: um município com 10% apurado, 600 votos para A e 400 para B, termina com 6.000 × 4.000.
As projeções são somadas para o Brasil e os percentuais recalculados sobre os votos válidos.

**Por que não usar só o apurado?** A apuração não chega de forma uniforme: cidades pequenas
terminam antes das capitais e cada região apura num ritmo. Projetar cada lugar pela própria
apuração corrige esse viés de ordem.

**Lugares ainda sem nenhuma seção apurada** recebem a média de votos por eleitor do município
(outras zonas), da UF ou, em último caso, do Brasil.

**Limitação.** Dentro de cada município/zona, assume que as seções que faltam votam como as
já apuradas.
""")
