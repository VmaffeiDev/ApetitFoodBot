"""Exporta o conteudo do app em dados, nao em texto pronto.

    python scripts/demo_dados.py demo/dados.json [Receitas.xlsx]

A interface precisa do dado, nao do paragrafo: o cardapio como lista de pratos
com veredito de alergenico, a sugestao como medidas separadas do nome, o
progresso como numero. Entao o conteudo sai daqui ja em JSON.

O que ele exporta e de duas naturezas, e a divisao importa:

* o que e **do dia e igual para a unidade inteira** sai de `apetit/payload.py`,
  o mesmo modulo que o servidor usa para responder `GET /api/dia`. Uma fonte so,
  senao a demonstracao e a producao mostram coisas diferentes;
* o que e **do exemplo** — o perfil da Mariana, o historico dela, os relatorios
  da gestao — so existe aqui, porque o servidor nao devolve nada disso.

Fiel continua sendo fiel: o veredito de alergenico, a sugestao de porcao, os
pontos e os relatorios saem de `apetit/`, nao de texto reescrito a mao.
"""

import json
import os
import sys
import tempfile
from dataclasses import replace
from datetime import date, timedelta
from itertools import product
from math import prod
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "demo")

from apetit.allergens import ALLERGENS, Declaration, Restriction, Verdict, check_item  # noqa: E402
from apetit.catalog import item_allergens  # noqa: E402
from apetit.allergen_sheet import coverage_summary  # noqa: E402
from apetit.catalog import allergen_coverage  # noqa: E402
from apetit.feedback import all_unit_reports  # noqa: E402
from apetit.pilot import pilot_report  # noqa: E402
from apetit.profile import aggregate_by_sector  # noqa: E402
from apetit.humanize import DIAS  # noqa: E402
from apetit.humanize import (  # noqa: E402
    category_label, clean_dish_name, friendly_date, week_summary,
)
from apetit import payload  # noqa: E402
from apetit.diario import (  # noqa: E402
    alvo_de, cardapio_de, descrever_restricoes, inicio_da_semana, sugestao_de,
)
from apetit.nudges import LEMBRETE_ALMOCO, RESUMO_SEMANAL  # noqa: E402
from apetit.profile import TARGETS, Employee, load_employee, save_employee  # noqa: E402
from apetit.portions import FREE_CATEGORIES, MEASURES, MEDIDA_PADRAO, measure_label  # noqa: E402
from apetit.prescription import extract_meal_plan, lunch_from_plan, read_pdf  # noqa: E402
from apetit.tracking import (  # noqa: E402
    RULES, favorites, history_by_day, log_consumption, points_breakdown,
    score_day, total_points,
)
from scripts.demo_banco import DIA, USUARIO, abrir, preparar_banco  # noqa: E402

# Uma conexao para o script inteiro. Abrir uma por chamada — `cardapio_de(banco(),
# ...)` — vazava um descritor a cada uso; some quando o processo sai, mas e o
# tipo de coisa que vira defeito de verdade no dia em que este codigo virar
# servidor.
_CONEXAO = None
_CAMINHO = None


def banco():
    global _CONEXAO
    if _CONEXAO is None:
        _CONEXAO = abrir(_CAMINHO)
    return _CONEXAO


MARCA = {"bloqueio": "bloqueio", "atencao": "atencao", "liberado": "liberado", "sem_restricao": "liberado"}


def etiqueta(check) -> str:
    """Duas ou tres palavras para caber na linha do prato.

    E resumo de espaco, nunca de sentido: "pode conter" e "sem declaracao" sao
    coisas diferentes e continuam diferentes aqui. O texto inteiro segue junto
    em `motivo`, e e ele que a tela abre quando a pessoa toca no prato.
    """
    nomes = lambda codes: ", ".join(ALLERGENS.get(c, c) for c in codes).lower()
    if check.contem:
        return f"contem {nomes(check.contem)}"
    if check.no_nome:
        return f"tem {', '.join(check.no_nome)}"
    if check.pode_conter:
        return f"pode conter {nomes(check.pode_conter)}"
    if check.nao_declarado:
        return f"sem declaracao de {nomes(check.nao_declarado)}"
    if check.nao_verificavel:
        return f"nao da para checar {', '.join(check.nao_verificavel)}"
    if check.safe_to_affirm:
        return "conferido para a sua ficha"
    return ""


def declaracoes_do_prato(conn, codigo: str) -> dict:
    """O que a ficha do prato declara sobre cada alergenico da RDC 26/2015.

    Alergenico que ninguem declarou sai como `nao_declarado` explicito, e nao
    ausente: no navegador, chave faltando viraria `undefined`, e a unica coisa
    pior que "nao sei" e um "nao sei" que parece um "pode".
    """
    declarado = item_allergens(conn, codigo)
    return {
        codigo_alergenico: declarado.get(codigo_alergenico, Declaration.NAO_DECLARADO).value
        for codigo_alergenico in ALLERGENS
    }


# Combinacoes usadas para provar que o navegador concorda com o Python.
# Sao as que o cadastro do app consegue produzir: nenhuma, uma, duas e uma
# larga. Alergia de texto livre nao entra porque a tela de cadastro oferece
# lista, e o que ela nao oferece o app nao finge saber checar.
# As combinacoes que a conferencia no navegador percorre. Sao escolhidas para
# cobrir os **quatro** conjuntos de pratos bloqueados que este cardapio produz:
# nenhum, so o macarrao (gluten), so o ovo (ovos) e os dois juntos. Sem o par
# `gluten + ovos` o caso de dois bloqueios de uma vez nunca seria exercitado.
COMBINACOES = (
    [],
    ["ovos"],
    ["leite"],
    ["gluten"],
    ["ovos", "leite"],
    ["gluten", "ovos"],
    ["gluten", "soja", "leite"],
    ["amendoim", "castanhas", "peixes", "crustaceos"],
)


def conformidade(pessoa) -> list[dict]:
    """O veredito que o Python da para cada combinacao, prato a prato.

    O navegador precisa decidir o veredito sozinho: o cadastro e do proprio
    funcionario, e a lista de alergias dele so existe no aparelho. Isso poe a
    regra dos tres estados em dois lugares, que e exatamente o jeito de eles
    discordarem um dia. Esta tabela e a trava: `scripts/conferir_vereditos.py`
    roda o app num navegador de verdade e falha se um unico prato divergir.
    """
    conn = abrir(_CAMINHO)
    try:
        codigos = [
            linha["item_code"]
            for linha in cardapio_de(banco(), pessoa, DIA)
        ]
        declaradas = {c: item_allergens(conn, c) for c in codigos}
    finally:
        conn.close()

    casos = []
    for combinacao in COMBINACOES:
        restricoes = [Restriction(c) for c in combinacao]
        casos.append({
            "restricoes": combinacao,
            "vereditos": {
                # Sem o `MARCA` de proposito: `sem_restricao` fica como esta.
                # Quem nao declarou alergia nao pode ver o cardapio inteiro de
                # verde — "liberado" afirma que alguem conferiu o prato para
                # ela, e ninguem conferiu coisa nenhuma.
                codigo: check_item(restricoes, declaradas[codigo]).verdict.value
                for codigo in codigos
            },
        })
    return casos


def porcoes(pessoa) -> dict:
    """Quanto pegar, com a medida separada do nome do prato."""
    sugestao = sugestao_de(banco(), pessoa, DIA)
    if not sugestao or not sugestao.portions:
        return {}
    alvo = alvo_de(pessoa, conn=banco())
    return {
        "itens": [
            {
                "codigo": p.code,
                "nome": clean_dish_name(p.name),
                "medida": "a vontade" if p.category in FREE_CATEGORIES
                          else measure_label(p.category, p.quantity),
                "categoria": category_label(p.category),
                "kcal": round(p.total_kcal),
                "ptn": round(p.total_ptn),
            }
            for p in sugestao.portions
        ],
        "kcal": round(sugestao.kcal),
        "ptn": round(sugestao.ptn_g),
        "alvo_kcal": alvo["kcal"],
        "alvo_ptn": alvo["ptn"],
        "resumo": sugestao.summary(),
        "avisos": list(sugestao.notes),
    }


def bloqueados_para(pessoa, restricoes: list[str]) -> list[str]:
    """Os pratos que essa lista de alergias bloqueia no cardapio do dia."""
    quem = replace(pessoa, restrictions=[Restriction(c) for c in restricoes])
    return sorted(
        i["item_code"] for i in cardapio_de(banco(), quem, DIA)
        if i["check"].verdict is Verdict.BLOQUEIO
    )


# Um `telegram_id` que nao e o da Mariana. As tabelas de consumo e ponto tem
# chave estrangeira para `employee`, entao a pessoa sem historico precisa
# existir no banco — ela so nunca registrou nada.
NOVATO = 999_000_001


def sem_historico():
    """Alguem que acabou de se cadastrar: existe, e nao fez nada ainda."""
    conn = abrir(_CAMINHO)
    try:
        quem = Employee(
            telegram_id=NOVATO, name="Novato", apetit_unit="SM",
            client_company="Industria Exemplo", sector="Producao",
            goal=next(iter(TARGETS)), consent_accepted=True,
        )
        save_employee(conn, quem)
        conn.commit()
        return load_employee(conn, NOVATO)
    finally:
        conn.close()


def meu_dia(pessoa) -> dict:
    conn = abrir(_CAMINHO)
    try:
        dias = history_by_day(conn, pessoa.telegram_id, days=14)
    finally:
        conn.close()
    return {
        "dias": [
            {
                "data": d.service_date,
                "data_amigavel": friendly_date(d.service_date),
                "kcal": round(d.kcal) if d.known_items else None,
                "ptn": round(d.ptn_g) if d.known_items else None,
                "incompleto": d.incomplete,
                "itens": [
                    {"nome": clean_dish_name(l["name"]), "quantidade": l["quantity"]}
                    for l in d.items
                ],
            }
            for d in dias
        ],
    }


def progresso(pessoa) -> dict:
    conn = abrir(_CAMINHO)
    try:
        pontos = total_points(conn, pessoa.telegram_id)
        extrato = points_breakdown(conn, pessoa.telegram_id)
        dias = {l["service_date"] for l in conn.execute(
            "SELECT service_date FROM consumption WHERE telegram_id = ?", (pessoa.telegram_id,)
        ).fetchall()}
    finally:
        conn.close()
    rotulos = {r.code: r.label for r in RULES}
    inicio = inicio_da_semana(DIA)
    na_semana = sum(1 for d in dias if inicio <= d <= DIA)
    return {
        "pontos": pontos,
        "dias_na_semana": na_semana,
        "resumo_semana": week_summary(na_semana),
        "conquistas": [
            {"nome": rotulos.get(l["rule_code"], l["rule_code"]), "vezes": l["vezes"]}
            for l in extrato
        ],
    }


def semana(pessoa) -> list[dict]:
    """Os cinco ultimos dias uteis, para o grafico do progresso.

    Janela movel, e nao "a semana corrente", porque o cardapio de exemplo
    comeca numa segunda: "esta semana" teria um dia so e o grafico nasceria
    vazio. Cinco dias uteis ate hoje mostra o que existe sem inventar dia
    nenhum — e o dia sem registro aparece como o que e, um dia sem registro.
    """
    conn = abrir(_CAMINHO)
    try:
        registrados = {
            linha["service_date"]: linha
            for linha in conn.execute(
                "SELECT service_date, SUM(kcal * quantity) AS kcal, "
                "SUM(ptn_g * quantity) AS ptn FROM consumption "
                "WHERE telegram_id = ? GROUP BY service_date",
                (pessoa.telegram_id,),
            ).fetchall()
        }
    finally:
        conn.close()

    dias, cursor = [], date.fromisoformat(DIA)
    while len(dias) < 5:
        if cursor.weekday() < 5:            # sabado e domingo nao tem refeitorio
            dias.append(cursor)
        cursor -= timedelta(days=1)
    dias.reverse()

    saida = []
    for d in dias:
        iso = d.isoformat()
        linha = registrados.get(iso)
        saida.append({
            "data": iso,
            "sigla": DIAS[d.weekday()][:3].capitalize(),
            "hoje": iso == DIA,
            "registrado": linha is not None,
            "kcal": round(linha["kcal"]) if linha and linha["kcal"] else None,
            "ptn": round(linha["ptn"]) if linha and linha["ptn"] else None,
        })
    return saida


def perfil(pessoa) -> dict:
    """O cadastro em campos, para a tela de perfil parar de ser um paragrafo.

    Nao ha e-mail: o app fala por Telegram e nunca pediu endereco nenhum.
    Campo que o cadastro nao coleta nao vira linha vazia na tela — inventar
    um lugar para ele seria prometer que existe.
    """
    conn = abrir(_CAMINHO)
    try:
        guardados = [
            {"nome": clean_dish_name(f["name"]), "codigo": f["item_code"]}
            for f in favorites(conn, pessoa.telegram_id)
        ]
    finally:
        conn.close()
    return {
        "nome": pessoa.name,
        "iniciais": "".join(p[0] for p in pessoa.name.split()[:2]).upper(),
        "refeitorio": pessoa.apetit_unit,
        "empresa": pessoa.client_company,
        "setor": pessoa.sector,
        "objetivo": pessoa.goal,
        "alvo": alvo_de(pessoa, conn=banco()),
        "restricoes": [ALLERGENS.get(r.allergen, r.allergen) for r in pessoa.restrictions],
        # O nome e para ler; o codigo e para decidir. Quem abre a demonstracao
        # sem se cadastrar ve o cardapio pelos olhos deste exemplo, e para isso
        # o navegador precisa da mesma chave que as declaracoes do prato usam.
        "restricoes_codigos": [r.allergen for r in pessoa.restrictions],
        "restricoes_livres": list(pessoa.free_restrictions),
        "evitar": list(pessoa.avoid_foods),
        "favoritos": guardados,
        "avisos": [
            {"codigo": RESUMO_SEMANAL, "nome": "Resumo da semana",
             "detalhe": "Sexta, 16h. Só sobre você: não existe ranking.", "ligado": True},
            {"codigo": LEMBRETE_ALMOCO, "nome": "Lembrete do almoço",
             "detalhe": "Dia útil, 11h, só se você ainda não registrou.", "ligado": False},
        ],
    }


def registro_previsto(pessoa, codigos=None) -> dict:
    """O que a pessoa ganha se registrar o prato sugerido hoje.

    Roda o registro de verdade num banco descartavel e le o resultado. A tela
    de "Vou pegar isso" precisa mostrar o ganho antes de ele acontecer, e
    calcular isso no navegador seria uma segunda implementacao das regras de
    pontuacao — que um dia discordaria desta.
    """
    if codigos is None:
        sugestao = sugestao_de(banco(), pessoa, DIA)
        if not sugestao or not sugestao.portions:
            return {}
        codigos = [p.code for p in sugestao.portions for _ in range(p.quantity)]
    if not codigos:
        return {}

    conn = abrir(_CAMINHO)
    try:
        antes = total_points(conn, pessoa.telegram_id)
        log_consumption(conn, pessoa.telegram_id, DIA, codigos)
        regras = score_day(conn, pessoa.telegram_id, DIA,
                           protein_target_g=alvo_de(pessoa, conn=banco())["ptn"])
        depois = total_points(conn, pessoa.telegram_id)
        dia = next(d for d in history_by_day(conn, pessoa.telegram_id, days=36500)
                   if d.service_date == DIA)
        refeicao = {
            "data": DIA, "data_amigavel": friendly_date(DIA),
            "kcal": round(dia.kcal) if dia.known_items else None,
            "ptn": round(dia.ptn_g) if dia.known_items else None,
            "incompleto": dia.incomplete,
            "itens": [{"codigo": l["item_code"], "nome": clean_dish_name(l["name"]),
                       "quantidade": l["quantity"],
                       "medida": "a vontade" if l["category"] in FREE_CATEGORIES
                       else measure_label(l["category"], l["quantity"])} for l in dia.items],
        }
        # O banco e temporario e morre no fim do script, mas desfazer aqui
        # mantem as outras exportacoes lendo o estado de antes do registro.
        conn.execute("DELETE FROM consumption WHERE telegram_id = ? AND service_date = ?",
                     (pessoa.telegram_id, DIA))
        conn.execute("DELETE FROM points_event WHERE telegram_id = ? AND reference_date = ?",
                     (pessoa.telegram_id, DIA))
        conn.commit()
    finally:
        conn.close()

    return {
        "pontos_antes": antes,
        "pontos_depois": depois,
        "ganho": depois - antes,
        "conquistas": [{"nome": r.label, "pontos": r.points} for r in regras],
        "refeicao": refeicao,
    }


def opcoes_montagem(pessoa) -> list[dict]:
    """Medidas e faixas disponíveis no demo, lidas do módulo de porções.

    A faixa offline é uma capacidade da interface, não uma prescrição. Salada
    mantém a indicação à vontade do motor, sem inventar unidades adicionais.
    """
    saida = []
    for item in sorted(cardapio_de(banco(), pessoa, DIA), key=lambda i: i["item_code"]):
        categoria = item["category"]
        maximo = 1 if categoria in FREE_CATEGORIES else MEASURES.get(categoria, MEDIDA_PADRAO)[2]
        saida.append({"codigo": item["item_code"], "maximo": maximo,
                      "medidas": ["a vontade" if categoria in FREE_CATEGORIES
                                  else measure_label(categoria, q) for q in range(1, maximo + 1)]})
    return saida


def com_exemplo(montagens: list[dict], pessoa) -> list[dict]:
    """Acrescenta, a cada montagem, o que **a Mariana** ganharia.

    O servidor nao devolve isso, e nao devolve de proposito: a previsao com
    historico depende de quem esta registrando, e o historico nunca sai do
    aparelho. Aqui e diferente — a Mariana e o exemplo do proprio arquivo, e a
    tela "So olhar" precisa mostrar um numero que faz sentido para ela.
    """
    for item in montagens:
        for alvo in item["objetivos"]:
            previsto = registro_previsto(replace(pessoa, goal=alvo["objetivo"]), list(item["codigos"]))
            previsto.pop("refeicao", None)
            alvo["exemplo"] = previsto
    return montagens


def montagens(pessoa) -> list[dict]:
    """Calcula no Python todas as quantidades suportadas pelo pequeno demo.

    Códigos repetidos são porções adicionais, como em log_consumption. O
    navegador escolhe um resultado já calculado, inclusive a pontuação.
    """
    opcoes = opcoes_montagem(pessoa)
    if prod(o["maximo"] + 1 for o in opcoes) > 4096:
        raise ValueError("O limite de combinações offline foi excedido; consulte o motor no servidor para este cardápio.")
    novato = sem_historico()
    saida = []
    for quantidades in product(*(range(o["maximo"] + 1) for o in opcoes)):
        selecao = [o["codigo"] for o, qtd in zip(opcoes, quantidades) for _ in range(qtd)]
        if not selecao:
            continue
        item = {"codigos": selecao, "objetivos": []}
        for objetivo in TARGETS:
            novo = registro_previsto(replace(novato, goal=objetivo), selecao)
            exemplo = registro_previsto(replace(pessoa, goal=objetivo), selecao)
            item["refeicao"] = novo.pop("refeicao")
            exemplo.pop("refeicao")
            item["objetivos"].append({"objetivo": objetivo, "novo": novo, "exemplo": exemplo})
        saida.append(item)
    return saida


def gestao(pessoa) -> dict:
    """Os quatro relatorios da Apetit, em numero e nao em paragrafo.

    Sai das mesmas funcoes que o bot usa — `pilot_report`, `aggregate_by_sector`,
    `all_unit_reports`, `allergen_coverage` — e por isso o n minimo de cinco
    pessoas, a supressao de recorte pequeno e a ordem "pior refeitorio primeiro"
    vem de graca. Recalcular esses numeros em JavaScript seria escrever uma
    segunda versao da regra de privacidade, que e o pior lugar possivel para ter
    duas versoes.

    **Isto nao entra em `/api/dia`.** Aquele payload e publico porque nao ha nada
    de ninguem nele; relatorio de adesao e de avaliacao e dado da empresa e pede
    rota autenticada. Aqui vai junto do arquivo do demo porque o demo e o
    exemplo fechado da Mariana, e serve para a tela existir antes de a rota
    existir.
    """
    conn = banco()
    desde, ate = "2025-09-01", "2025-09-30"
    rel = pilot_report(conn, desde=desde, ate=ate, convidados=16)
    unidades = all_unit_reports(conn, from_date="2025-08-03", to_date="2025-09-01")
    cob = allergen_coverage(conn)

    return {
        "periodo": {"desde": desde, "ate": ate,
                    "desde_amigavel": friendly_date(desde), "ate_amigavel": friendly_date(ate)},
        "piloto": {
            "convidados": rel.convidados, "cadastrados": rel.cadastrados,
            "ativos": rel.ativos, "voltaram": rel.voltaram,
            "registros": rel.registros, "dias_por_pessoa": rel.dias_por_pessoa,
            "avaliacoes": rel.avaliacoes,
            "comida_boa_pct": rel.comida_boa_pct,
            "atendimento_bom_pct": rel.atendimento_bom_pct,
            "itens_cardapio": rel.itens_cardapio,
            "itens_sem_macro": rel.itens_sem_macro,
            "itens_sem_alergenico": rel.itens_sem_alergenico,
            "pessoas_com_restricao": rel.pessoas_com_restricao,
            # Quando a funcao e suprimida, o numero nao vai no arquivo — nao vai
            # so escondido na tela. "Uma pessoa tem ficha nutricional" num
            # piloto de quinze aponta para alguem tanto quanto o nome dela, e o
            # arquivo e publico: mandar o numero e confiar que nenhuma tela, hoje
            # ou depois, vai imprimi-lo.
            "funcoes": [{"nome": f.nome, "saude": f.saude, "suprimido": f.suprimida,
                         "pessoas": None if f.suprimida else f.pessoas,
                         "usos": None if f.suprimida else f.usos}
                        for f in rel.funcoes],
            # `None` no lugar do numero e o recorte suprimido pelo n minimo. A
            # tela precisa distinguir isso de zero: "ninguem declarou" e
            # "declarou pouca gente para mostrar" nao sao a mesma informacao.
            "objetivos": [{"nome": nome, "pessoas": quantos} for nome, quantos in rel.objetivos],
        },
        "setores": [
            {"empresa": linha["client_company"], "setor": linha["sector"],
             "pessoas": linha["total"], "suprimido": bool(linha.get("suprimido")),
             "motivo": linha.get("motivo", "")}
            for linha in aggregate_by_sector(conn)
        ],
        "refeitorios": [
            {"unidade": u.apetit_unit, "avaliacoes": u.total,
             "comida_boa_pct": u.food_good_pct, "atendimento_bom_pct": u.service_good_pct,
             "faltou_algo": u.missing_count, "faltou_pct": u.missing_pct,
             "suprimido": u.suppressed, "motivo": u.reason,
             "marcacoes": [{"nome": nome, "vezes": vezes} for nome, vezes in u.tags]}
            for u in unidades
        ],
        "cobertura": {
            "total": cob["total"], "completos": cob["completos"], "parciais": cob["parciais"],
            "sem_nenhuma": cob["total"] - cob["completos"] - cob["parciais"],
            "resumo": coverage_summary(cob["total"], cob["completos"], cob["parciais"]),
            # Os que mais aparecem no cardapio primeiro: declarar na ordem de
            # quem mais e servido tira mais aviso de "nao consigo confirmar" por
            # ficha preenchida.
            "faltando": [{"codigo": c, "nome": clean_dish_name(n), "faltam": q}
                         for c, n, q in cob["faltando"][:10]],
        },
    }


def plano_exemplo(ficha: Path | None) -> list[dict]:
    """O almoco de uma ficha de exemplo, para a tela nascer com conteudo."""
    if not ficha or not ficha.exists():
        return []
    almoco = lunch_from_plan(extract_meal_plan(read_pdf(ficha.read_bytes())))
    return [
        {"nome": i.nome, "quantidade": i.quantidade, "medida": i.medida, "peso": i.peso}
        for i in almoco
    ]


def main() -> int:
    saida = Path(sys.argv[1] if len(sys.argv) > 1 else "demo/dados.json")
    receitas = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    ficha = Path(sys.argv[3]) if len(sys.argv) > 3 else None

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    tmp.close()
    try:
        global _CAMINHO
        _CAMINHO = Path(tmp.name)
        preparar_banco(_CAMINHO, receitas)
        conn_demo = banco()
        pessoa = load_employee(conn_demo, USUARIO)
        try:
            dados = {
                # O que e do dia e igual para todo mundo da unidade sai de
                # `apetit/payload.py` — o mesmo modulo que o servidor usa para
                # responder ao app. Duas fontes para a mesma tela e o jeito de o
                # demo e a producao mostrarem coisas diferentes.
                **payload.do_dia(conn_demo, pessoa.apetit_unit, DIA),
                # O que sobra aqui e do **exemplo**: o perfil da Mariana, o
                # historico dela e a conferencia de veredito. Nada disso o
                # servidor devolve, porque nada disso e do dia — e de alguem.
                "pessoa": {
                    "nome": pessoa.name,
                    "refeitorio": pessoa.apetit_unit,
                    "empresa": pessoa.client_company,
                    "setor": pessoa.sector,
                    "objetivo": pessoa.goal,
                    "restricoes": descrever_restricoes(pessoa),
                },
                "montagens": com_exemplo(payload.do_dia(conn_demo, pessoa.apetit_unit, DIA)["montagens"], pessoa),
                "conformidade": conformidade(pessoa),
                "porcoes": porcoes(pessoa),
                "meu_dia": meu_dia(pessoa),
                "progresso": progresso(pessoa),
                "semana": semana(pessoa),
                "perfil": perfil(pessoa),
                "registro_previsto": registro_previsto(pessoa),
                "plano_exemplo": plano_exemplo(ficha),
                "gestao": gestao(pessoa),
            }
        finally:
            conn_demo.close()
    finally:
        global _CONEXAO
        if _CONEXAO is not None:
            _CONEXAO.close()
            _CONEXAO = None
        Path(tmp.name).unlink(missing_ok=True)

    saida.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{saida}: {len(dados['cardapio'])} categorias, "
          f"{len(dados['porcoes'].get('itens', []))} porcoes, "
          f"{len(dados['plano_exemplo'])} itens de plano")
    return 0



if __name__ == "__main__":
    raise SystemExit(main())
