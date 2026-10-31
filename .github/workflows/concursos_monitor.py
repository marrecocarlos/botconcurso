#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MONITOR DE CONCURSOS PÚBLICOS -> TELEGRAM
=========================================
Varre fontes de notícias de concursos (Google Notícias RSS, outros RSS e,
opcionalmente, páginas HTML), identifica notícias de concurso AUTORIZADO ou
EDITAL PUBLICADO, aplica seus filtros (estado, federal, nível) e manda um
alerta formatado para o seu Telegram.

Como usar (resumo):
    python concursos_monitor.py --chat-id    # descobre o seu chat_id
    python concursos_monitor.py --teste      # envia mensagem de teste
    python concursos_monitor.py --dry-run    # mostra o que seria enviado
    python concursos_monitor.py              # roda continuamente
    python concursos_monitor.py --uma-vez    # roda 1 vez e sai (agendadores)
    python concursos_monitor.py --uma-vez --debug   # explica cada descarte

IMPORTANTE: a classificação (órgão, cargo, nível, estado, status) é feita por
heurísticas sobre manchetes. É boa, mas não perfeita: sempre confira no link.
"""

from __future__ import annotations

import argparse
import html
import json
import logging
import re
import sys
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote_plus, urljoin

import feedparser
import requests
from bs4 import BeautifulSoup

import config as cfg

# ----------------------------------------------------------------------------
# CONSTANTES
# ----------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
ARQUIVO_ESTADO = BASE_DIR / "estado_visto.json"   # memória: o que já foi avisado
ARQUIVO_LOG = BASE_DIR / "monitor.log"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; MonitorConcursosPessoal/1.0)"}
TIMEOUT = 20  # segundos

UF_NOMES = {
    "AC": "Acre", "AL": "Alagoas", "AP": "Amapá", "AM": "Amazonas",
    "BA": "Bahia", "CE": "Ceará", "DF": "Distrito Federal",
    "ES": "Espírito Santo", "GO": "Goiás", "MA": "Maranhão",
    "MT": "Mato Grosso", "MS": "Mato Grosso do Sul", "MG": "Minas Gerais",
    "PA": "Pará", "PB": "Paraíba", "PR": "Paraná", "PE": "Pernambuco",
    "PI": "Piauí", "RJ": "Rio de Janeiro", "RN": "Rio Grande do Norte",
    "RS": "Rio Grande do Sul", "RO": "Rondônia", "RR": "Roraima",
    "SC": "Santa Catarina", "SP": "São Paulo", "SE": "Sergipe",
    "TO": "Tocantins",
}

log = logging.getLogger("concursos")


# ----------------------------------------------------------------------------
# MODELO DE DADOS
# ----------------------------------------------------------------------------
@dataclass
class Item:
    """Uma notícia coletada de alguma fonte."""
    id: str                       # identificador único (normalmente o link)
    titulo: str
    link: str
    fonte: str                    # nome do portal/fonte
    resumo: str = ""
    publicado: datetime | None = None
    dica_uf: str | None = None    # UF da busca que trouxe o item (se houver)
    dica_federal: bool = False    # veio de uma busca "federal"?
    ruidoso: bool = False         # True = resumo não é confiável (Google News)


# ----------------------------------------------------------------------------
# UTILITÁRIOS DE TEXTO
# ----------------------------------------------------------------------------
def normalizar(txt: str) -> str:
    """Minúsculas e sem acentos, para comparar textos com segurança."""
    txt = unicodedata.normalize("NFKD", txt or "")
    return "".join(c for c in txt if not unicodedata.combining(c)).lower()


def limpar_html(txt: str) -> str:
    """Remove tags HTML de um resumo."""
    return BeautifulSoup(txt or "", "html.parser").get_text(" ", strip=True)


def esc(txt: str) -> str:
    """Escapa texto para o modo HTML do Telegram."""
    return html.escape(txt or "", quote=False)


def seguro(msg: str) -> str:
    """Esconde o token do bot em mensagens de erro/log."""
    tok = cfg.TELEGRAM_BOT_TOKEN
    return msg.replace(tok, "***") if tok else msg


# ----------------------------------------------------------------------------
# CLASSIFICAÇÃO: STATUS (Autorizado / Edital Publicado)
# ----------------------------------------------------------------------------
# Padrões aplicados ao texto SEM acentos e em minúsculas.
EDITAL_FORTE = [  # indicam com certeza que o edital já saiu
    r"\b(?:abre|abrem|abriu|abriram)\s+(?:as\s+|suas\s+)?inscri",
    r"inscricoes\s+(?:estao\s+)?abertas",
    r"inscricoes\s+(?:comecam|iniciam)",
    r"\b(?:publicou|divulgou|lancou|liberou|soltou)\s+(?:o\s+|os\s+|novo\s+|novos\s+|seu\s+)?edital",
    r"\bsaiu\s+(?:o\s+)?edital",
    r"edital\s+(?:do\s+concurso\s+)?saiu",
]
EDITAL_FRACO = [  # podem aparecer em frases no futuro ("edital será publicado")
    r"edital\s+(?:do\s+concurso\s+)?(?:foi\s+)?(?:publicado|divulgado|lancado|oficial|disponivel|liberado)",
    r"\b(?:publica|divulga|lanca|libera|solta|sai)\s+(?:o\s+|os\s+|novo\s+|novos\s+)?edital",
    r"\bnovo\s+edital\b",
]
AUTORIZADO = [
    r"autoriz\w+",
    r"\bliberou\s+(?:o\s+)?concurso",
    r"\bconcurso\s+liberado",
    r"sinal\s+verde",
    r"\baval\b",
]
FUTURO = re.compile(
    r"\b(?:deve|devem|devera|deverao|previsto|prevista|previstos|em\s+breve|"
    r"proximos\s+dias|proxima\s+semana|aguard\w+|expectativa|quando|iminente|"
    r"ser(?:a|ao)|vai\s+(?:publicar|sair|divulgar|lancar|abrir|ser))\b"
)


def detectar_status(texto: str) -> str | None:
    """Retorna 'Edital Publicado', 'Autorizado' ou None."""
    t = normalizar(texto)
    forte = any(re.search(p, t) for p in EDITAL_FORTE)
    fraco = any(re.search(p, t) for p in EDITAL_FRACO)
    futuro = bool(FUTURO.search(t))
    autorizado = any(re.search(p, t) for p in AUTORIZADO)

    if forte or (fraco and not futuro):
        return "Edital Publicado"
    if autorizado:
        return "Autorizado"
    return None


# ----------------------------------------------------------------------------
# CLASSIFICAÇÃO: NÍVEL DE ESCOLARIDADE
# ----------------------------------------------------------------------------
def detectar_niveis(texto: str) -> list[str]:
    """Retorna lista com 'Fundamental', 'Médio', 'Médio Técnico', 'Superior'."""
    t = normalizar(texto)
    # "Superior Tribunal", "Tribunal Superior" etc. NÃO são nível superior
    t = re.sub(r"tribunal superior|superior tribunal|conselho superior|escola superior", " ", t)
    niveis = []
    if re.search(r"\bfundamental\b", t):
        niveis.append("Fundamental")
    if re.search(r"\bmedio\b(?!\s+(?:prazo|porte))", t):
        niveis.append("Médio")
    if re.search(r"\btecnic[oa]s?\b", t):
        niveis.append("Médio Técnico")
    if re.search(r"\bsuperior\b|\bgraduacao\b", t):
        niveis.append("Superior")
    return niveis


# ----------------------------------------------------------------------------
# CLASSIFICAÇÃO: ESTADO E FEDERAL
# ----------------------------------------------------------------------------
def detectar_ufs(texto: str) -> list[str]:
    """Descobre quais estados o texto menciona (nome, sigla ou cidade/órgão)."""
    achados: list[str] = []
    restante = texto

    # 1) Nome por extenso. Os mais longos primeiro, e removemos o trecho
    #    achado ("Mato Grosso do Sul" não deve contar também como "Mato Grosso").
    for uf, nome in sorted(UF_NOMES.items(), key=lambda kv: -len(kv[1])):
        padrao = r"(?<!\w)" + re.escape(nome) + r"(?!\w)"
        if re.search(padrao, restante, re.I):
            achados.append(uf)
            restante = re.sub(padrao, " ", restante, flags=re.I)

    # 2) Cidades/órgãos configurados em LOCAIS_POR_UF
    for uf, locais in cfg.LOCAIS_POR_UF.items():
        if uf in achados:
            continue
        for local in locais:
            if re.search(r"(?<!\w)" + re.escape(local) + r"(?!\w)", texto, re.I):
                achados.append(uf)
                break

    # 3) Sigla em maiúsculas: "- RJ", "/RJ", "(RJ)" ou palavra isolada.
    #    Em títulos TODOS em maiúsculas só aceitamos com separador.
    tudo_maiusculo = texto.isupper()
    for uf in UF_NOMES:
        if uf in achados:
            continue
        com_separador = rf"[/(\-–,]\s*{uf}(?![A-Za-z])"
        isolada = rf"(?<![A-Za-z]){uf}(?![A-Za-z])"
        if re.search(com_separador, restante) or (
            not tudo_maiusculo and re.search(isolada, restante)
        ):
            achados.append(uf)
    return achados


def eh_federal(texto: str) -> bool:
    """True se o texto cita algum órgão/termo federal de PALAVRAS_FEDERAIS."""
    t = normalizar(texto).replace("distrito federal", " ")  # GDF não é federal
    for palavra in cfg.PALAVRAS_FEDERAIS:
        if re.search(r"(?<!\w)" + re.escape(normalizar(palavra)) + r"(?!\w)", t):
            return True
    return False


# ----------------------------------------------------------------------------
# EXTRAÇÃO: ÓRGÃO, CARGO, VAGAS (heurísticas sobre a manchete)
# ----------------------------------------------------------------------------
_VERBOS = (
    r"(?:autoriza\w*|publica\w*|divulga\w*|abre\w*|abriu|lan[cç]a\w*|libera\w*|"
    r"anuncia\w*|confirma\w*|tem|ter[aá]|ter[aã]o|oferece\w*|recebe\w*|sai|saiu|"
    r"solta\w*|define\w*|aprova\w*|prev[eê]|planeja\w*|pede\w*)"
)
_STOP_ORGAO = re.compile(
    r"\s+(?:com|para|tem|ter[aá]|autoriza\w*|publica\w*|divulga\w*|abre|abriu|"
    r"oferece|lan[cç]a|libera|anuncia|confirma|em\s+breve|[eé]|foi|est[aá]|ser[aá]|nos?|nas?)\s+"
    r"|[:;,|\u2013\u2014(]|\s-\s|\s\d",
    re.I,
)
_ORGAO_INVALIDO = re.compile(
    r"^(?:autoriz|publicad|divulgad|abert|liberad|confirmad|sai\b|saiu|tem\b|"
    r"com\b|para\b|\d|inscri|edital|vagas|novo)",
    re.I,
)
_STOP_CARGO = re.compile(
    r"\s+(?:com|n[ií]vel|n[ií]veis|sal[aá]rio|at[eé]|que|no|na|nos|nas)\s+|[:;,|.!?(]|\s[-\u2013\u2014]\s",
    re.I,
)
_CARGO_GENERICO = re.compile(
    r"^(?:n[ií]vel|n[ií]veis|ensino|candidatos?|quem|todos|v[aá]rios|diversos|"
    r"diferentes|profissionais|vagas|\d)",
    re.I,
)


def _cortar(txt: str, padrao: re.Pattern) -> str:
    m = padrao.search(txt)
    return (txt[: m.start()] if m else txt).strip(" -–:")


def extrair_orgao(titulo: str) -> str:
    """Tenta descobrir o órgão a partir da manchete."""
    t = titulo.strip()

    # Ex.: "Concurso Prefeitura de Niterói: edital publicado"
    m = re.match(
        r"^(?:novo\s+)?concursos?(?:\s+p[uú]blicos?)?\s+"
        r"(?:(?:d[aoe]s?|para(?:\s+(?:o|a))?)\s+)?(?P<r>.+)$", t, re.I)
    if m:
        org = _cortar(m.group("r"), _STOP_ORGAO)
        if 2 <= len(org) <= 60 and not _ORGAO_INVALIDO.match(org):
            return org

    # Ex.: "INSS autoriza concurso com 1.000 vagas"
    m = re.match(rf"^(?P<r>.+?)\s+{_VERBOS}\b", t, re.I)
    if m:
        org = re.sub(
            r"^(?:concursos?(?:\s+p[uú]blicos?)?|edital)\s+(?:d[aoe]s?\s+)?",
            "", m.group("r").strip(" -–:"), flags=re.I)
        org = _cortar(org, _STOP_ORGAO)
        if 2 <= len(org) <= 60 and not _ORGAO_INVALIDO.match(org):
            return org

    # Ex.: "Autorizado concurso da Polícia Federal"
    m = re.search(r"concursos?(?:\s+p[uú]blicos?)?\s+d[aoe]s?\s+(?P<r>[A-ZÀ-Ú].+)", t)
    if m:
        org = _cortar(m.group("r"), _STOP_ORGAO)
        if 2 <= len(org) <= 60 and not _ORGAO_INVALIDO.match(org):
            return org
    return "Não identificado (veja no link)"


def extrair_cargo(texto: str) -> str:
    """Tenta descobrir o(s) cargo(s) em trechos como 'vagas para Analista'."""
    m = re.search(
        r"\bpara\s+(?:o\s+|a\s+|os\s+|as\s+)?(?:cargos?\s+(?:de\s+)?)?(?P<c>[^:;|.!?]+)",
        texto, re.I)
    if m:
        cargo = _cortar(m.group("c"), _STOP_CARGO)
        if 3 <= len(cargo) <= 70 and not _CARGO_GENERICO.match(cargo):
            return cargo
    return "Diversos (veja no edital/link)"


def extrair_vagas(texto: str) -> str | None:
    m = re.search(r"(\d[\d\.]*)\s+vagas?", texto, re.I)
    return m.group(1) if m else None


# ----------------------------------------------------------------------------
# ANÁLISE COMPLETA DE UMA NOTÍCIA (aplica todos os filtros)
# ----------------------------------------------------------------------------
def analisar(item: Item) -> tuple[dict | None, str]:
    """
    Retorna (resultado, motivo). Se a notícia não passar nos filtros,
    resultado = None e 'motivo' explica o descarte (útil com --debug).
    """
    titulo = item.titulo
    t_norm = normalizar(titulo)

    if not re.search(r"concurso|edital", t_norm):
        return None, "título sem 'concurso' ou 'edital'"
    for palavra in cfg.IGNORAR_SE_CONTIVER:
        if normalizar(palavra) in t_norm:
            return None, f"contém termo ignorado '{palavra.strip()}'"

    # Texto usado na análise: título + resumo (se o resumo for confiável)
    texto = titulo if item.ruidoso else f"{titulo}. {item.resumo[:400]}"

    # --- Status -------------------------------------------------------------
    status = detectar_status(titulo) or (None if item.ruidoso else detectar_status(texto))
    if not status:
        return None, "não parece 'autorizado' nem 'edital publicado'"
    status_ok = {normalizar(s) for s in cfg.STATUS_MONITORADOS}
    if normalizar(status) not in status_ok:
        return None, f"status '{status}' não monitorado"

    # --- Estado / federal ---------------------------------------------------
    ufs = detectar_ufs(texto)
    federal = eh_federal(texto)
    provavel = False
    if not ufs and not federal:      # usa a "dica" da busca que trouxe a notícia
        if item.dica_uf:
            ufs, provavel = [item.dica_uf], True
        elif item.dica_federal:
            federal, provavel = True, True

    alvo = [u for u in ufs if u.upper() in {e.upper() for e in cfg.ESTADOS}]
    if not alvo and not (cfg.INCLUIR_FEDERAIS and federal):
        return None, f"fora do filtro de estado (UFs={ufs}, federal={federal})"

    # --- Nível --------------------------------------------------------------
    niveis = detectar_niveis(texto)
    desejados = {normalizar(n) for n in cfg.NIVEIS}
    if niveis:
        if not any(normalizar(n) in desejados for n in niveis):
            return None, f"nível {niveis} fora do filtro"
        nivel_txt = " / ".join(niveis)
    else:
        if not cfg.ACEITAR_NIVEL_NAO_IDENTIFICADO:
            return None, "nível não identificado"
        nivel_txt = "Não identificado (veja no link)"

    # --- Texto do estado ----------------------------------------------------
    if ufs and federal:
        estado_txt = f"{', '.join(ufs)} (Federal)"
    elif ufs:
        estado_txt = ", ".join(ufs)
    else:
        estado_txt = "Federal / Nacional"
    if provavel:
        estado_txt += " (provável)"

    return {
        "status": status,
        "orgao": extrair_orgao(titulo),
        "cargo": extrair_cargo(texto),
        "nivel": nivel_txt,
        "estado": estado_txt,
        "vagas": extrair_vagas(titulo),
    }, "ok"


# ----------------------------------------------------------------------------
# COLETA DE DADOS
# ----------------------------------------------------------------------------
def baixar(url: str) -> requests.Response:
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r


def separar_titulo_fonte(titulo: str, fonte_padrao: str) -> tuple[str, str]:
    """No Google Notícias o título vem como 'Manchete - Portal'."""
    if " - " in titulo:
        t, f = titulo.rsplit(" - ", 1)
        if 0 < len(f) <= 45:
            return t.strip(), f.strip()
    return titulo.strip(), fonte_padrao


def coletar_rss(nome: str, url: str, google: bool = False,
                dica_uf: str | None = None, dica_federal: bool = False) -> list[Item]:
    """Lê um feed RSS/Atom e devolve a lista de notícias."""
    feed = feedparser.parse(baixar(url).content)
    itens = []
    for e in feed.entries:
        titulo = (e.get("title") or "").strip()
        link = (e.get("link") or "").strip()
        if not titulo or not link:
            continue
        fonte = nome
        if google:
            fonte = (e.get("source") or {}).get("title") or nome
            titulo, fonte = separar_titulo_fonte(titulo, fonte)
        pub = None
        if e.get("published_parsed"):
            pub = datetime(*e.published_parsed[:6], tzinfo=timezone.utc)
        itens.append(Item(
            id=link, titulo=titulo, link=link, fonte=fonte,
            resumo="" if google else limpar_html(e.get("summary", "")),
            publicado=pub, dica_uf=dica_uf, dica_federal=dica_federal,
            ruidoso=google,
        ))
    return itens


def coletar_html(fonte: dict) -> list[Item]:
    """Raspagem simples: pega links de uma página usando um seletor CSS."""
    soup = BeautifulSoup(baixar(fonte["url"]).text, "html.parser")
    itens, vistos = [], set()
    for a in soup.select(fonte["seletor"]):
        titulo = a.get_text(" ", strip=True)
        href = a.get("href")
        if not href or len(titulo) < 20:
            continue
        link = urljoin(fonte["url"], href)
        if link in vistos:
            continue
        vistos.add(link)
        itens.append(Item(id=link, titulo=titulo, link=link, fonte=fonte["nome"]))
    return itens


def url_google_news(consulta: str) -> str:
    q = quote_plus(f"{consulta} when:{cfg.GOOGLE_NEWS_JANELA}")
    return f"https://news.google.com/rss/search?q={q}&hl=pt-BR&gl=BR&ceid=BR:pt-419"


def coletar_tudo() -> list[tuple[str, list[Item]]]:
    """Percorre todas as fontes configuradas. Retorna [(nome_fonte, itens)]."""
    tarefas = []  # (nome, função que coleta)

    if cfg.USAR_GOOGLE_NEWS:
        for uf in cfg.ESTADOS:
            nome_estado = UF_NOMES.get(uf.upper())
            if not nome_estado:
                log.warning("Sigla de estado desconhecida em ESTADOS: %s", uf)
                continue
            for modelo in cfg.GOOGLE_BUSCAS_POR_ESTADO:
                q = modelo.format(estado=nome_estado, uf=uf.upper())
                tarefas.append((f"GNews: {q}", lambda q=q, uf=uf: coletar_rss(
                    "Google Notícias", url_google_news(q), google=True, dica_uf=uf.upper())))
        if cfg.INCLUIR_FEDERAIS:
            for q in cfg.GOOGLE_BUSCAS_FEDERAIS:
                tarefas.append((f"GNews: {q}", lambda q=q: coletar_rss(
                    "Google Notícias", url_google_news(q), google=True, dica_federal=True)))
        for q in cfg.GOOGLE_BUSCAS_EXTRAS:
            tarefas.append((f"GNews: {q}", lambda q=q: coletar_rss(
                "Google Notícias", url_google_news(q), google=True)))

    for f in cfg.FONTES_RSS:
        tarefas.append((f"RSS: {f['nome']}", lambda f=f: coletar_rss(f["nome"], f["url"])))
    for f in cfg.FONTES_HTML:
        tarefas.append((f"HTML: {f['nome']}", lambda f=f: coletar_html(f)))

    resultados = []
    for nome, funcao in tarefas:
        try:
            itens = funcao()
            log.info("Fonte '%s': %d itens", nome, len(itens))
            resultados.append((nome, itens))
        except Exception as exc:  # uma fonte fora do ar não derruba as outras
            log.warning("Falha na fonte '%s': %s", nome, seguro(str(exc)))
        time.sleep(1)  # educação com os servidores
    return resultados


# ----------------------------------------------------------------------------
# MEMÓRIA (para não avisar duas vezes a mesma coisa)
# ----------------------------------------------------------------------------
def carregar_estado() -> dict:
    if ARQUIVO_ESTADO.exists():
        try:
            return json.loads(ARQUIVO_ESTADO.read_text(encoding="utf-8"))
        except Exception:
            log.warning("Arquivo de estado corrompido; recomeçando do zero.")
    return {"vistos": {}, "enviados": [], "fontes_ok": []}


def salvar_estado(estado: dict) -> None:
    agora = datetime.now(timezone.utc)
    # limpa memória antiga (30 dias para vistos, 7 dias para enviados)
    estado["vistos"] = {k: v for k, v in estado["vistos"].items()
                        if datetime.fromisoformat(v) > agora - timedelta(days=30)}
    estado["enviados"] = [e for e in estado["enviados"]
                          if datetime.fromisoformat(e["ts"]) > agora - timedelta(days=7)][-300:]
    tmp = ARQUIVO_ESTADO.with_suffix(".tmp")
    tmp.write_text(json.dumps(estado, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(ARQUIVO_ESTADO)


def _tokens(titulo: str) -> set[str]:
    return {w for w in re.findall(r"\w+", normalizar(titulo)) if len(w) > 3}


def eh_repetida(titulo: str, status: str, enviados: list[dict]) -> bool:
    """True se já avisamos uma notícia muito parecida (mesmo status)."""
    a = _tokens(titulo)
    if not a:
        return False
    for e in enviados:
        if e["status"] != status:
            continue
        b = _tokens(e["titulo"])
        if b and len(a & b) / len(a | b) >= cfg.SIMILARIDADE_TITULOS:
            return True
    return False


# ----------------------------------------------------------------------------
# TELEGRAM
# ----------------------------------------------------------------------------
def config_telegram_ok() -> bool:
    return ("COLE_AQUI" not in cfg.TELEGRAM_BOT_TOKEN
            and "COLE_AQUI" not in str(cfg.TELEGRAM_CHAT_ID))


def formatar_mensagem(r: dict, item: Item) -> str:
    icone = "✅" if r["status"] == "Autorizado" else "📢"
    linhas = [
        f"{icone} <b>CONCURSO {esc(r['status'].upper())}</b>",
        "",
        f"🏢 <b>Órgão:</b> {esc(r['orgao'])}",
        f"💼 <b>Cargo:</b> {esc(r['cargo'])}",
        f"🎓 <b>Nível:</b> {esc(r['nivel'])}",
        f"📍 <b>Estado:</b> {esc(r['estado'])}",
        f"📌 <b>Status:</b> {esc(r['status'])}",
    ]
    if r.get("vagas"):
        linhas.append(f"👥 <b>Vagas:</b> {esc(r['vagas'])}")
    linhas += [
        "",
        f"📰 <i>{esc(item.titulo)}</i>",
        f'🔗 <a href="{html.escape(item.link, quote=True)}">Abrir notícia ({esc(item.fonte)})</a>',
    ]
    return "\n".join(linhas)


def enviar_telegram(texto: str) -> bool:
    """Envia a mensagem. Tenta de novo se o Telegram pedir para esperar."""
    url = f"https://api.telegram.org/bot{cfg.TELEGRAM_BOT_TOKEN}/sendMessage"
    dados = {"chat_id": cfg.TELEGRAM_CHAT_ID, "text": texto, "parse_mode": "HTML"}
    for _ in range(3):
        try:
            r = requests.post(url, data=dados, timeout=TIMEOUT)
        except requests.RequestException as exc:
            log.error("Erro de rede ao enviar: %s", seguro(str(exc)))
            time.sleep(5)
            continue
        if r.status_code == 200:
            return True
        if r.status_code == 429:   # limite de envio: espera e tenta de novo
            espera = r.json().get("parameters", {}).get("retry_after", 5)
            time.sleep(espera + 1)
            continue
        log.error("Telegram recusou (%s): %s", r.status_code, seguro(r.text))
        return False
    return False


def descobrir_chat_id() -> None:
    """Mostra o chat_id de quem mandou mensagem para o bot."""
    if "COLE_AQUI" in cfg.TELEGRAM_BOT_TOKEN:
        print("Primeiro coloque o TELEGRAM_BOT_TOKEN no arquivo config.py.")
        return
    url = f"https://api.telegram.org/bot{cfg.TELEGRAM_BOT_TOKEN}/getUpdates"
    try:
        dados = requests.get(url, timeout=TIMEOUT).json()
    except requests.RequestException as exc:
        print("Erro de rede:", seguro(str(exc)))
        return
    if not dados.get("ok"):
        print("Token inválido ou erro:", dados)
        return
    chats = {}
    for upd in dados.get("result", []):
        msg = upd.get("message") or upd.get("channel_post") or {}
        chat = msg.get("chat")
        if chat:
            nome = chat.get("first_name") or chat.get("title") or chat.get("username") or "?"
            chats[chat["id"]] = nome
    if not chats:
        print("Nenhuma mensagem encontrada. Abra o seu bot no Telegram, "
              "toque em INICIAR (Start), envie um 'oi' e rode este comando de novo.")
        return
    for cid, nome in chats.items():
        print(f"chat_id = {cid}   (conversa com: {nome})")


# ----------------------------------------------------------------------------
# CICLO PRINCIPAL
# ----------------------------------------------------------------------------
def ciclo(dry_run: bool = False, debug: bool = False) -> None:
    """Uma varredura completa: coleta, filtra, envia e memoriza."""
    estado = carregar_estado()
    agora = datetime.now(timezone.utc)
    limite_idade = agora - timedelta(hours=cfg.MAX_IDADE_HORAS)
    candidatos: list[tuple[Item, dict]] = []
    ids_na_rodada: set[str] = set()

    for nome_fonte, itens in coletar_tudo():
        # Fontes sem data de publicação (HTML): na 1ª vez só memorizamos,
        # senão você receberia todo o histórico da página de uma vez.
        primeira_vez_sem_data = nome_fonte not in estado["fontes_ok"] and all(
            i.publicado is None for i in itens)
        for item in itens:
            if item.id in estado["vistos"] or item.id in ids_na_rodada:
                continue
            ids_na_rodada.add(item.id)
            if primeira_vez_sem_data:
                estado["vistos"][item.id] = agora.isoformat()
                continue
            if item.publicado and item.publicado < limite_idade:
                continue
            resultado, motivo = analisar(item)
            if debug:
                log.info("[%s] %s -> %s", "OK" if resultado else "descartado",
                         item.titulo[:90], motivo)
            if resultado:
                candidatos.append((item, resultado))
            else:
                estado["vistos"][item.id] = agora.isoformat()  # não reanalisar
        if nome_fonte not in estado["fontes_ok"]:
            estado["fontes_ok"].append(nome_fonte)

    # mais antigas primeiro
    candidatos.sort(key=lambda c: c[0].publicado or agora)
    enviadas = 0
    for item, r in candidatos:
        if eh_repetida(item.titulo, r["status"], estado["enviados"]):
            log.info("Repetida (similar a outra já enviada): %s", item.titulo[:80])
            estado["vistos"][item.id] = agora.isoformat()
            continue
        if enviadas >= cfg.MAX_MENSAGENS_POR_RODADA:
            log.info("Limite por rodada atingido; o restante vai na próxima.")
            break
        mensagem = formatar_mensagem(r, item)
        if dry_run:
            print("-" * 60)
            print(re.sub(r"<[^>]+>", "", html.unescape(mensagem)))
            enviadas += 1
            continue
        if enviar_telegram(mensagem):
            enviadas += 1
            estado["vistos"][item.id] = agora.isoformat()
            estado["enviados"].append(
                {"titulo": item.titulo, "status": r["status"], "ts": agora.isoformat()})
            time.sleep(1)
        # se falhou, NÃO marca como visto: tentará de novo na próxima rodada

    if not dry_run:
        salvar_estado(estado)
    log.info("Rodada concluída: %d alerta(s) %s.", enviadas,
             "simulados" if dry_run else "enviados")


def configurar_log() -> None:
    # Evita erro de acentos/emojis no terminal do Windows
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%d/%m %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout),
                  logging.FileHandler(ARQUIVO_LOG, encoding="utf-8")],
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="Monitor de concursos -> Telegram")
    ap.add_argument("--chat-id", action="store_true", help="descobre o seu chat_id")
    ap.add_argument("--teste", action="store_true", help="envia mensagem de teste")
    ap.add_argument("--dry-run", action="store_true", help="não envia nem memoriza; só mostra")
    ap.add_argument("--uma-vez", action="store_true", help="faz uma varredura e encerra")
    ap.add_argument("--debug", action="store_true", help="explica por que cada notícia passou/foi descartada")
    args = ap.parse_args()
    configurar_log()

    if args.chat_id:
        descobrir_chat_id()
        return
    if args.teste:
        ok = config_telegram_ok() and enviar_telegram(
            "✅ <b>Monitor de concursos conectado!</b>\nVocê receberá os alertas aqui.")
        print("Mensagem de teste enviada!" if ok else
              "Falhou. Confira TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID em config.py.")
        return
    if not args.dry_run and not config_telegram_ok():
        print("Configure TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID em config.py "
              "(ou use --dry-run para só visualizar).")
        return

    if args.uma_vez or args.dry_run:
        ciclo(dry_run=args.dry_run, debug=args.debug)
        return

    log.info("Monitor iniciado. Varredura a cada %d min. Ctrl+C para parar.",
             cfg.INTERVALO_MINUTOS)
    while True:
        try:
            ciclo(debug=args.debug)
        except KeyboardInterrupt:
            raise
        except Exception:
            log.exception("Erro inesperado na varredura (seguirá na próxima).")
        try:
            time.sleep(cfg.INTERVALO_MINUTOS * 60)
        except KeyboardInterrupt:
            break
    log.info("Monitor encerrado.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nEncerrado.")
