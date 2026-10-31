# -*- coding: utf-8 -*-
"""
CONFIGURAÇÃO DO MONITOR DE CONCURSOS
====================================
Este é o ÚNICO arquivo que você precisa editar no dia a dia.
Abra no Bloco de Notas (ou VS Code), altere os valores e salve.
"""

import os

# ----------------------------------------------------------------------------
# 1) TELEGRAM
# ----------------------------------------------------------------------------
# Cole aqui o token que o @BotFather te deu e o seu chat_id.
# (Dica: se for rodar no GitHub Actions, NÃO escreva aqui: use "Secrets".
#  O código lê primeiro as variáveis de ambiente e só depois estes valores.)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "9120643181:AAEsbFSKHbKqp2A523Emz6SwZ5zlnp2cvw0")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "8438567357")


# ----------------------------------------------------------------------------
# 2) FILTROS: O QUE VOCÊ QUER RECEBER
# ----------------------------------------------------------------------------
# Estados monitorados (use a sigla de 2 letras).
# Exemplos: ["RJ"]  |  ["RJ", "SP", "MG"]  |  [] (nenhum estado, só federais)
ESTADOS = ["RJ", "SP"]

# Receber também concursos FEDERAIS (INSS, PF, PRF, Receita, universidades
# federais, tribunais federais, bancos públicos etc.)?
INCLUIR_FEDERAIS = True

# Níveis de escolaridade desejados. Valores aceitos:
#   "Fundamental", "Médio", "Médio Técnico", "Superior"
NIVEIS = ["Médio", "Médio Técnico", "Superior"]

# Muitas manchetes não citam o nível (ex.: "INSS autoriza concurso").
# True  = avisa mesmo assim e escreve "Não identificado (veja no link)".
# False = só avisa quando o nível aparecer no texto (você pode perder avisos).
ACEITAR_NIVEL_NAO_IDENTIFICADO = True

# Quais eventos geram alerta: "Autorizado" e/ou "Edital Publicado"
STATUS_MONITORADOS = ["Autorizado", "Edital Publicado"]


# ----------------------------------------------------------------------------
# 3) FREQUÊNCIA E LIMITES
# ----------------------------------------------------------------------------
INTERVALO_MINUTOS = 30          # de quanto em quanto tempo varrer (modo contínuo)
MAX_IDADE_HORAS = 72            # ignora notícias mais antigas que isso
MAX_MENSAGENS_POR_RODADA = 15   # evita "enxurrada" de mensagens de uma vez
SIMILARIDADE_TITULOS = 0.6      # 0 a 1. Evita avisar 10x a mesma notícia
                                # publicada por portais diferentes (menor = mais
                                # agressivo em agrupar; 1.0 = desliga)


# ----------------------------------------------------------------------------
# 4) FONTES DE DADOS
# ----------------------------------------------------------------------------
# 4a) Google Notícias (RSS público). Agrega PCI Concursos, Folha Dirigida,
#     Estratégia, Gran, G1, Diário Oficial e muitos outros. É a fonte principal.
USAR_GOOGLE_NEWS = True
GOOGLE_NEWS_JANELA = "3d"       # "1d", "3d", "7d"... (só notícias dos últimos X dias)

# Buscas feitas para CADA estado de ESTADOS. {estado} = nome por extenso.
GOOGLE_BUSCAS_POR_ESTADO = [
    'concurso autorizado "{estado}"',
    'edital concurso publicado "{estado}"',
    'concurso "{estado}" inscrições abertas edital',
]

# Buscas feitas se INCLUIR_FEDERAIS = True.
GOOGLE_BUSCAS_FEDERAIS = [
    "concurso federal autorizado",
    "edital concurso federal publicado",
    "concurso nacional autorizado edital",
]

# Buscas livres extras. Útil para cidades ou órgãos específicos. Exemplos:
#   'concurso autorizado Niterói', 'edital concurso "Polícia Civil" RJ'
GOOGLE_BUSCAS_EXTRAS = [
    # 'concurso autorizado Niterói',
]

# 4b) Outros feeds RSS/Atom (opcional). Para testar: cole a URL no navegador;
#     se aparecer um texto em XML, é um feed válido. Muitos sites WordPress
#     têm feed em  https://site.com.br/feed/
#     Formato: {"nome": "Nome do site", "url": "https://..."}
FONTES_RSS = [
    # {"nome": "Exemplo Concursos", "url": "https://www.exemplo.com.br/feed/"},
]

# 4c) Páginas HTML sem RSS (raspagem simples de links). Use com moderação e
#     respeite os termos de uso/robots.txt do site.
#     "seletor" é um seletor CSS dos links de notícias (clique direito > Inspecionar).
FONTES_HTML = [
    # {"nome": "Site X", "url": "https://www.site.com.br/noticias/", "seletor": "h2 a"},
]


# ----------------------------------------------------------------------------
# 5) PALAVRAS QUE AJUDAM A CLASSIFICAR (ajuste fino)
# ----------------------------------------------------------------------------
# Manchetes que contenham qualquer um destes termos são DESCARTADAS
# (gabarito, resultado, retificação etc. não são "autorizado" nem "edital novo").
IGNORAR_SE_CONTIVER = [
    "gabarito", "resultado", "convoca", "homologa", "retifica", "prorroga",
    "suspende", "suspenso", "cancela", "adiad", "adia ", "local de prova",
    "cartao de confirmacao", "apostila", "simulado", "preparatorio",
    "aprovados", "classificados", "nomeacao", "prova objetiva",
    "concursos abertos", "lista de concursos", "isencao",
]

# Se o título ou resumo tiver alguma destas palavras, o concurso é tratado
# como FEDERAL. Acrescente órgãos que você acompanha.
PALAVRAS_FEDERAIS = [
    "federal", "concurso nacional", "concurso unificado", "cnu",
    "inss", "pf", "prf", "banco central", "bacen", "ibge", "cgu", "tcu",
    "stf", "stj", "tst", "tse", "superior tribunal", "tribunal superior", "trt", "tre", "trf", "mpu", "mpf", "mpt",
    "dpu", "agu", "anvisa", "anatel", "aneel", "ans", "ancine", "antt",
    "anac", "antaq", "anp", "cvm", "susep", "cade", "ibama", "icmbio",
    "funai", "fiocruz", "ebserh", "serpro", "dataprev", "correios",
    "caixa economica", "banco do brasil", "bndes", "petrobras", "embrapa",
    "dnit", "exercito", "marinha", "aeronautica", "senado",
    "camara dos deputados", "itamaraty", "inpi", "inmetro", "ipea", "capes",
    "cnpq", "cefet", "colegio pedro ii", "ufrj", "uff", "unirio", "ufrrj",
    "ifrj", "ministerio da", "ministerio do", "ministerio dos",
    "ministerio publico da uniao", "ministerio publico do trabalho",
]

# Locais que indicam o estado mesmo sem citar a sigla/nome do estado.
# Adicione cidades e órgãos que você acompanha.
LOCAIS_POR_UF = {
    "RJ": [
        "Niterói", "Duque de Caxias", "Nova Iguaçu", "São Gonçalo",
        "Belford Roxo", "Campos dos Goytacazes", "Petrópolis", "Volta Redonda",
        "Macaé", "Angra dos Reis", "Cabo Frio", "Itaboraí", "Maricá",
        "Resende", "Teresópolis", "Nova Friburgo", "Barra Mansa", "Magé",
        "Prefeitura do Rio", "Câmara do Rio", "Alerj", "Uerj", "Uenf",
        "Faetec", "Cedae", "PMERJ", "PCERJ", "TJRJ", "MPRJ", "DPRJ",
        "TCE-RJ", "Seeduc",
    ],
    "SP": ["Campinas", "Santos", "Guarulhos", "Osasco", "Sorocaba", "Alesp"],
    "MG": ["Belo Horizonte", "Uberlândia", "Contagem", "Juiz de Fora"],
}
