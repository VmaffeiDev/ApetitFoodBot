"""O que o app do funcionario precisa saber do dia — e nada sobre ninguem.

O app web nasceu lendo um `dados.json` gerado aqui e publicado junto com o
site: um retrato de um dia, de uma unidade, para uma pessoa de exemplo. Isso
serve para demonstrar e nao serve para operar — publicar um cardapio novo nao
chegava a ninguem sem alguem rodar um script e republicar o site.

Este modulo e a virada. Ele monta o mesmo conteudo a partir do banco, para
**uma unidade e um dia**, e o servidor o serve. O exportador do demo passa a
chamar a mesma funcao, entao as duas saidas nao tem como divergir.

O ponto que decide o desenho: **nada aqui e dado pessoal.** O cardapio do dia e
igual para todo mundo da unidade; as alergias, o objetivo e o historico
continuam no aparelho de quem usa o app. Por isso a leitura nao exige login, e a
promessa de privacidade que o termo faz continua valendo sem nenhuma migracao:
o servidor nunca soube, e continua sem saber, o que voce nao pode comer.

O veredito de alergenico tambem nao sai daqui. Ele depende da lista de quem esta
lendo, e e calculado no navegador, contra `dados.conformidade` — a trava que o
`scripts/conferir_vereditos.py` confere prato a prato num navegador de verdade.
"""

import sqlite3
from itertools import combinations, product
from math import prod

from .allergens import ALLERGENS, Declaration
from .catalog import init_schema, item_allergens, menu_for_date
from .feedback import MISSING_TAGS, SCALE
from .humanize import (
    category_label, clean_dish_name, dish_hint, dish_role, dish_weight,
    friendly_date, order_categories,
)
from .portions import FREE_CATEGORIES, MEASURES, MEDIDA_PADRAO, measure_label, suggest_plate
from .profile import TARGETS, Employee, save_employee
from .tracking import history_by_day, log_consumption, score_day, total_points

# Quantas combinacoes de quantidade a exportacao offline aceita montar. Acima
# disso o navegador teria de escolher entre respostas que nao cabem no arquivo,
# e a montagem precisa voltar a ser uma pergunta ao servidor.
LIMITE_MONTAGENS = 4096

# Um `pessoa_id` so para calcular pontuacao. E negativo porque o Telegram
# nunca emite id negativo para pessoa — ninguem colide com ele.
CALCULADORA = -1


def _banco_de_calculo(conn: sqlite3.Connection, unidade: str, dia: str, refeicao: str):
    """Um banco descartavel com o cardapio do dia, e mais nada.

    A pontuacao so sai rodando o registro de verdade, e registrar exige gravar.
    Gravar no banco de producao para desfazer em seguida seria pedir para um
    erro no meio deixar lixo no historico de alguem — e a pessoa de calculo
    apareceria no relatorio do piloto, inflando a adesao com quem nao existe.

    Entao o calculo acontece numa copia em memoria com as linhas do dia. O banco
    de producao nao e tocado nem uma vez.
    """
    calculo = sqlite3.connect(":memory:")
    calculo.row_factory = sqlite3.Row
    init_schema(calculo)
    for tabela, colunas in (
        ("menu_item", "code, name, portion_g, kcal, cho_g, lip_g, ptn_g, updated_at"),
        ("menu_entry", "unit, service_date, meal, category, slot, item_code, created_at"),
    ):
        for linha in conn.execute(f"SELECT {colunas} FROM {tabela}").fetchall():
            valores = tuple(linha)
            marcas = ", ".join("?" * len(valores))
            calculo.execute(f"INSERT OR REPLACE INTO {tabela} ({colunas}) VALUES ({marcas})", valores)
    save_employee(calculo, Employee(
        pessoa_id=CALCULADORA, name="calculo", apetit_unit=unidade,
        client_company="", sector="", goal=next(iter(TARGETS)), consent_accepted=True,
    ))
    calculo.commit()
    return calculo


def _limpar(conn: sqlite3.Connection, dia: str) -> None:
    conn.execute("DELETE FROM consumption WHERE pessoa_id = ? AND service_date = ?",
                 (CALCULADORA, dia))
    conn.execute("DELETE FROM points_event WHERE pessoa_id = ? AND reference_date = ?",
                 (CALCULADORA, dia))
    conn.commit()


def declaracoes(conn: sqlite3.Connection, codigo: str) -> dict[str, str]:
    """O que a ficha tecnica declara, alergenico por alergenico.

    Ausencia vira `nao_declarado`, nunca `nao_contem`: ninguem ter escrito nao e
    o mesmo que alguem ter conferido.
    """
    declarado = item_allergens(conn, codigo)
    return {c: declarado.get(c, Declaration.NAO_DECLARADO).value for c in ALLERGENS}


def cardapio(conn: sqlite3.Connection, unidade: str, dia: str, refeicao: str = "almoco") -> list[dict]:
    """Os pratos do dia por categoria, sem veredito: ele e de quem le."""
    grupos: dict[str, dict] = {}
    for linha in menu_for_date(conn, dia, unit=unidade, meal=refeicao):
        categoria = linha["category"]
        grupo = grupos.setdefault(categoria, {
            "codigo": categoria, "categoria": category_label(categoria), "itens": [],
        })
        kcal = linha["kcal"]
        ptn = linha["ptn_g"]
        grupo["itens"].append({
            "codigo": linha["item_code"],
            "nome": clean_dish_name(linha["name"]),
            "declaracoes": declaracoes(conn, linha["item_code"]),
            "papel": dish_role(categoria),
            "peso": dish_weight(kcal),
            "dica": dish_hint(kcal, ptn),
            "kcal": round(kcal) if kcal else None,
            "ptn": round(ptn) if ptn else None,
        })
    return [grupos[c] for c in order_categories(list(grupos)) if c in grupos]


def _porcoes(liberados: list[dict], alvo: dict) -> tuple[dict, list[str]]:
    """A sugestao pronta para a tela, e os codigos para registrar.

    Os dois vem juntos de proposito. A sugestao diz "2 colheres de arroz", e
    registrar isso e logar o arroz **duas vezes** — e o que `log_consumption`
    espera. Montar a lista de codigos a partir dos itens da tela, um por item,
    perdia a quantidade e derrubava a meta de proteina do dia sem nada na tela
    denunciar.
    """
    sugestao = suggest_plate(liberados, alvo["kcal"], alvo["ptn"])
    if not sugestao or not sugestao.portions:
        return {}, []
    codigos = [p.code for p in sugestao.portions for _ in range(p.quantity)]
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
    }, codigos


def _previsao(conn: sqlite3.Connection, dia: str, codigos: list[str], alvo: dict) -> dict:
    """O que alguem sem historico ganha ao registrar estes pratos hoje.

    Roda o registro de verdade contra `apetit/tracking.py` e desfaz. Calcular a
    pontuacao no navegador seria uma segunda implementacao das regras — e as
    regras de semana (`variedade`, `sequencia`) dependem do que veio antes, o
    que um navegador sem historico nao tem como saber.
    """
    if not codigos:
        return {}
    antes = total_points(conn, CALCULADORA)
    log_consumption(conn, CALCULADORA, dia, codigos)
    regras = score_day(conn, CALCULADORA, dia, protein_target_g=alvo["ptn"])
    depois = total_points(conn, CALCULADORA)
    registrado = next(
        (d for d in history_by_day(conn, CALCULADORA, days=36500) if d.service_date == dia),
        None,
    )
    refeicao = {
        "data": dia, "data_amigavel": friendly_date(dia),
        "kcal": round(registrado.kcal) if registrado and registrado.known_items else None,
        "ptn": round(registrado.ptn_g) if registrado and registrado.known_items else None,
        "incompleto": bool(registrado and registrado.incomplete),
        "itens": [
            {
                "codigo": l["item_code"], "nome": clean_dish_name(l["name"]),
                "quantidade": l["quantity"],
                "medida": "a vontade" if l["category"] in FREE_CATEGORIES
                          else measure_label(l["category"], l["quantity"]),
            }
            for l in (registrado.items if registrado else [])
        ],
    }
    _limpar(conn, dia)
    return {
        "pontos_antes": antes, "pontos_depois": depois, "ganho": depois - antes,
        "conquistas": [{"nome": r.label, "pontos": r.points} for r in regras],
        "refeicao": refeicao,
    }


def combinacoes(conn: sqlite3.Connection, unidade: str, dia: str, refeicao: str = "almoco") -> list[dict]:
    """A sugestao de porcoes para **qualquer** cadastro, vinda do motor.

    A sugestao nao depende da lista de alergias, e sim de quais pratos sobram, e
    do alvo do objetivo. Das centenas de combinacoes de alergia saem poucos
    conjuntos de bloqueados, e cada um vezes cada objetivo e uma resposta
    calculada aqui. O navegador escolhe uma; nao calcula nenhuma.
    """
    linhas = list(menu_for_date(conn, dia, unit=unidade, meal=refeicao))
    if not linhas:
        return []
    pratos = [
        {"code": l["item_code"], "category": l["category"], "name": l["name"],
         "kcal": l["kcal"], "ptn_g": l["ptn_g"]}
        for l in linhas
    ]
    declarado = {p["code"]: declaracoes(conn, p["code"]) for p in pratos}

    distintos: dict[tuple[str, ...], None] = {}
    for tamanho in range(len(ALLERGENS) + 1):
        for combo in combinations(list(ALLERGENS), tamanho):
            bloqueados = tuple(sorted(
                codigo for codigo, d in declarado.items()
                if any(d[c] == Declaration.CONTEM.value for c in combo)
            ))
            distintos.setdefault(bloqueados, None)

    calculo = _banco_de_calculo(conn, unidade, dia, refeicao)
    try:
        saida = []
        for bloqueados in sorted(distintos):
            liberados = [p for p in pratos if p["code"] not in bloqueados]
            for objetivo, alvo in TARGETS.items():
                sugestao, codigos = _porcoes(liberados, alvo)
                saida.append({
                    "bloqueados": list(bloqueados),
                    "objetivo": objetivo,
                    "sugestao": sugestao,
                    "registro": _previsao(calculo, dia, codigos, alvo),
                })
    finally:
        calculo.close()
    return saida


def opcoes_montagem(conn: sqlite3.Connection, unidade: str, dia: str, refeicao: str = "almoco") -> list[dict]:
    """Medidas e faixas de quantidade, lidas do modulo de porcoes.

    A faixa e capacidade da interface, nao prescricao. Salada mantem o "a
    vontade" do motor, sem inventar unidade nenhuma.
    """
    saida = []
    for linha in sorted(menu_for_date(conn, dia, unit=unidade, meal=refeicao),
                        key=lambda l: l["item_code"]):
        categoria = linha["category"]
        maximo = 1 if categoria in FREE_CATEGORIES else MEASURES.get(categoria, MEDIDA_PADRAO)[2]
        saida.append({
            "codigo": linha["item_code"],
            "maximo": maximo,
            "medidas": ["a vontade" if categoria in FREE_CATEGORIES
                        else measure_label(categoria, q) for q in range(1, maximo + 1)],
        })
    return saida


def montagens(conn: sqlite3.Connection, unidade: str, dia: str, refeicao: str = "almoco") -> list[dict]:
    """Cada selecao possivel do montador, com macros e pontos ja calculados.

    Codigo repetido e porcao adicional, como em `log_consumption`. O navegador
    escolhe um resultado pronto — nao replica macro nem pontuacao.
    """
    opcoes = opcoes_montagem(conn, unidade, dia, refeicao)
    if not opcoes:
        return []
    if prod(o["maximo"] + 1 for o in opcoes) > LIMITE_MONTAGENS:
        raise ValueError(
            "O limite de combinacoes offline foi excedido; para este cardapio a "
            "montagem precisa consultar o motor no servidor."
        )
    calculo = _banco_de_calculo(conn, unidade, dia, refeicao)
    try:
        saida = []
        faixas = [range(o["maximo"] + 1) for o in opcoes]
        for quantidades in product(*faixas):
            codigos = [o["codigo"] for o, q in zip(opcoes, quantidades) for _ in range(q)]
            if not codigos:
                continue
            item = {"codigos": codigos, "objetivos": []}
            for objetivo, alvo in TARGETS.items():
                previsto = _previsao(calculo, dia, codigos, alvo)
                item["refeicao"] = previsto.pop("refeicao")
                item["objetivos"].append({"objetivo": objetivo, "novo": previsto})
            saida.append(item)
    finally:
        calculo.close()
    return saida


def avaliacao() -> dict:
    """As opcoes da avaliacao, como o dominio ja as define."""
    return {
        "escala": [{"nota": n, "rotulo": r} for n, r in sorted(SCALE.items(), reverse=True)],
        "faltas": [{"codigo": c, "rotulo": r} for c, r in MISSING_TAGS.items()],
    }


def do_dia(conn: sqlite3.Connection, unidade: str, dia: str, refeicao: str = "almoco") -> dict:
    """Tudo o que o app precisa do dia, e nada sobre ninguem."""
    return {
        "dia": dia,
        "dia_amigavel": friendly_date(dia),
        "unidade": unidade,
        "refeicao": refeicao,
        "cardapio": cardapio(conn, unidade, dia, refeicao),
        "alergenicos": [{"codigo": c, "nome": n} for c, n in ALLERGENS.items()],
        "objetivos": [{"nome": nome, "alvo": alvo} for nome, alvo in TARGETS.items()],
        "combinacoes": combinacoes(conn, unidade, dia, refeicao),
        "montagens": montagens(conn, unidade, dia, refeicao),
        "opcoes_montagem": opcoes_montagem(conn, unidade, dia, refeicao),
        "avaliacao": avaliacao(),
    }
