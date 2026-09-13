"""Exporta o conteudo do app em dados, nao em texto pronto.

    python scripts/demo_dados.py demo/dados.json [Receitas.xlsx]

O `demo_telas.py` captura a tela do bot como ela sai no Telegram: um bloco de
texto com os botoes embaixo. Isso serve para mostrar o bot, e e fiel — mas um
bot de Telegram **e** uma conversa com botoes, e uma tela de aplicativo nao e.

Para o app parecer aplicativo, a interface precisa do dado, nao do paragrafo:
o cardapio como lista de pratos com veredito de alergenico, a sugestao como
medidas separadas do nome, o progresso como numero. Entao aqui o conteudo sai
das mesmas funcoes de dominio que o bot usa, ja em JSON.

Fiel continua sendo fiel: o veredito de alergenico, a sugestao de porcao e o
historico saem de `apetit/`, nao de texto reescrito a mao.
"""

import json
import os
import sys
import tempfile
from dataclasses import replace
from datetime import date, timedelta
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "demo")

import bot  # noqa: E402
from apetit.allergens import ALLERGENS, Declaration, Restriction, Verdict, check_item  # noqa: E402
from apetit.catalog import item_allergens  # noqa: E402
from apetit.feedback import MISSING_TAGS, SCALE  # noqa: E402
from apetit.humanize import DIAS  # noqa: E402
from apetit.humanize import (  # noqa: E402
    category_label, clean_dish_name, dish_hint, dish_role, dish_weight,
    friendly_date, order_categories, week_summary,
)
from apetit.nudges import LEMBRETE_ALMOCO, RESUMO_SEMANAL  # noqa: E402
from apetit.profile import Employee, load_employee, save_employee  # noqa: E402
from apetit.portions import FREE_CATEGORIES, measure_label  # noqa: E402
from apetit.prescription import extract_meal_plan, lunch_from_plan, read_pdf  # noqa: E402
from apetit.tracking import (  # noqa: E402
    RULES, favorites, history_by_day, log_consumption, points_breakdown,
    score_day, total_points,
)
from scripts.demo_telas import DIA, USUARIO, preparar_banco  # noqa: E402

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


def cardapio(pessoa) -> list[dict]:
    """O cardapio do dia, por categoria, com o veredito de cada prato."""
    conn = bot.db()
    try:
        grupos: dict[str, list[dict]] = {}
        for item in bot.load_menu(pessoa, DIA):
            check = item["check"]
            grupos.setdefault(item["category"], []).append({
                "codigo": item["item_code"],
                "declaracoes": declaracoes_do_prato(conn, item["item_code"]),
                "nome": clean_dish_name(item["name"]),
                "veredito": MARCA.get(check.verdict.value, "atencao"),
                "etiqueta": etiqueta(check),
                "motivo": check.message() if check.verdict.value != "sem_restricao" else "",
                "peso": dish_weight(item.get("kcal")),
                "papel": dish_role(item["category"]),
                "dica": dish_hint(item.get("kcal"), item.get("ptn_g")),
                "kcal": item.get("kcal"),
                "ptn": item.get("ptn_g"),
            })
    finally:
        conn.close()
    return [
        {"categoria": category_label(nome), "codigo": nome, "itens": grupos[nome]}
        for nome in order_categories(grupos)
    ]


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
    conn = bot.db()
    try:
        codigos = [
            linha["item_code"]
            for linha in bot.load_menu(pessoa, DIA)
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


def contagem(grupos: list[dict]) -> dict:
    """Quantos pratos em cada veredito, para a tela inicial dizer em um numero.

    `atencao` fica no mesmo balde que tudo que nao da para afirmar, e e de
    proposito: separar "pode conter" de "ninguem declarou" num contador daria a
    impressao de que o segundo e mais seguro que o primeiro. Nenhum dos dois e
    seguro — os dois pedem a mesma pergunta no balcao.
    """
    itens = [i for g in grupos for i in g["itens"]]
    return {
        "total": len(itens),
        "bloqueio": sum(1 for i in itens if i["veredito"] == "bloqueio"),
        "atencao": sum(1 for i in itens if i["veredito"] == "atencao"),
        "liberado": sum(1 for i in itens if i["veredito"] == "liberado"),
    }


def porcoes(pessoa) -> dict:
    """Quanto pegar, com a medida separada do nome do prato."""
    sugestao = bot.build_suggestion(pessoa, DIA)
    if not sugestao or not sugestao.portions:
        return {}
    alvo = bot.target_for(pessoa)
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
        i["item_code"] for i in bot.load_menu(quem, DIA)
        if i["check"].verdict is Verdict.BLOQUEIO
    )


def combinacoes() -> list[dict]:
    """A sugestao de porcoes para **qualquer** cadastro, vinda do Python.

    O cadastro e do proprio funcionario e mora no aparelho dele, entao a
    sugestao precisa responder a alergia e objetivo que so existem no
    navegador. Recalcular la seria uma segunda implementacao de
    `apetit/portions.py` — e essa decide quanto alguem come.

    A saida disso e o que torna desnecessario: a sugestao nao depende da lista
    de alergias, e sim de **quais pratos sobram**, e das 512 combinacoes de
    alergia deste cardapio saem so quatro conjuntos de bloqueados. Quatro
    conjuntos x quatro objetivos = dezesseis respostas, todas calculadas aqui
    pelo motor de verdade. O navegador escolhe uma; nao calcula nenhuma.

    O `registro` sai com historico vazio, que e a situacao de quem acabou de se
    cadastrar. As regras de semana (`variedade`, `sequencia`) dependem do que
    veio antes, e a Mariana tem dois dias registrados: usar a previsao dela
    para uma pessoa nova prometeria ponto que nao vem.
    """
    novato = sem_historico()
    distintos: dict[tuple[str, ...], list[str]] = {}
    for tamanho in range(len(ALLERGENS) + 1):
        for combo in combinations(list(ALLERGENS), tamanho):
            chave = tuple(bloqueados_para(novato, list(combo)))
            distintos.setdefault(chave, list(combo))

    saida = []
    for bloqueados, exemplo in sorted(distintos.items()):
        for objetivo in bot.TARGETS:
            quem = replace(
                novato,
                restrictions=[Restriction(c) for c in exemplo],
                goal=objetivo,
            )
            saida.append({
                "bloqueados": list(bloqueados),
                "objetivo": objetivo,
                "sugestao": porcoes(quem),
                "registro": registro_previsto(quem),
            })
    return saida


# Um `telegram_id` que nao e o da Mariana. As tabelas de consumo e ponto tem
# chave estrangeira para `employee`, entao a pessoa sem historico precisa
# existir no banco — ela so nunca registrou nada.
NOVATO = 999_000_001


def sem_historico():
    """Alguem que acabou de se cadastrar: existe, e nao fez nada ainda."""
    conn = bot.db()
    try:
        quem = Employee(
            telegram_id=NOVATO, name="Novato", apetit_unit="SM",
            client_company="Industria Exemplo", sector="Producao",
            goal=next(iter(bot.TARGETS)), consent_accepted=True,
        )
        save_employee(conn, quem)
        conn.commit()
        return load_employee(conn, NOVATO)
    finally:
        conn.close()


def meu_dia(pessoa) -> dict:
    conn = bot.db()
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
    conn = bot.db()
    try:
        pontos = total_points(conn, pessoa.telegram_id)
        extrato = points_breakdown(conn, pessoa.telegram_id)
        dias = {l["service_date"] for l in conn.execute(
            "SELECT service_date FROM consumption WHERE telegram_id = ?", (pessoa.telegram_id,)
        ).fetchall()}
    finally:
        conn.close()
    rotulos = {r.code: r.label for r in RULES}
    inicio = bot.week_start(DIA)
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
    conn = bot.db()
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
    conn = bot.db()
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
        "alvo": bot.target_for(pessoa),
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


def avaliacao() -> dict:
    """As opcoes da avaliacao, como o dominio ja as define."""
    return {
        "escala": [{"nota": n, "rotulo": r} for n, r in sorted(SCALE.items(), reverse=True)],
        "faltas": [{"codigo": c, "rotulo": r} for c, r in MISSING_TAGS.items()],
    }


def registro_previsto(pessoa, codigos=None) -> dict:
    """O que a pessoa ganha se registrar o prato sugerido hoje.

    Roda o registro de verdade num banco descartavel e le o resultado. A tela
    de "Vou pegar isso" precisa mostrar o ganho antes de ele acontecer, e
    calcular isso no navegador seria uma segunda implementacao das regras de
    pontuacao — que um dia discordaria desta.
    """
    if codigos is None:
        sugestao = bot.build_suggestion(pessoa, DIA)
        if not sugestao or not sugestao.portions:
            return {}
        codigos = [p.code for p in sugestao.portions for _ in range(p.quantity)]
    if not codigos:
        return {}

    conn = bot.db()
    try:
        antes = total_points(conn, pessoa.telegram_id)
        log_consumption(conn, pessoa.telegram_id, DIA, codigos)
        regras = score_day(conn, pessoa.telegram_id, DIA,
                           protein_target_g=bot.target_for(pessoa)["ptn"])
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


def montagens(pessoa) -> list[dict]:
    """Resultados do motor para as seleções manuais do pequeno cardápio demo.

    Cada alimento pode ser escolhido uma vez, como no montador do bot. A
    interface consulta estes resultados; não replica macros nem pontuação.
    O limite impede uma exportação exponencial para um cardápio de produção.
    """
    codigos = sorted(i["item_code"] for i in bot.load_menu(pessoa, DIA))
    if len(codigos) > 10:
        raise ValueError("A montagem offline suporta até 10 alimentos; use o motor no servidor para cardápios maiores.")
    novato = sem_historico()
    saida = []
    for tamanho in range(1, len(codigos) + 1):
        for selecao in combinations(codigos, tamanho):
            item = {"codigos": list(selecao), "objetivos": []}
            for objetivo in bot.TARGETS:
                novo = registro_previsto(replace(novato, goal=objetivo), list(selecao))
                exemplo = registro_previsto(replace(pessoa, goal=objetivo), list(selecao))
                item["refeicao"] = novo.pop("refeicao")
                exemplo.pop("refeicao")
                item["objetivos"].append({"objetivo": objetivo, "novo": novo, "exemplo": exemplo})
            saida.append(item)
    return saida


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
        preparar_banco(Path(tmp.name), receitas)
        bot.today = lambda: DIA
        pessoa = bot.current_employee(_FakeUpdate(USUARIO))
        dados = {
            "dia": DIA,
            "dia_amigavel": friendly_date(DIA),
            "pessoa": {
                "nome": pessoa.name,
                "refeitorio": pessoa.apetit_unit,
                "empresa": pessoa.client_company,
                "setor": pessoa.sector,
                "objetivo": pessoa.goal,
                "restricoes": bot.describe_restrictions(pessoa),
            },
            "cardapio": (pratos := cardapio(pessoa)),
            "contagem": contagem(pratos),
            "conformidade": conformidade(pessoa),
            "alergenicos": [{"codigo": c, "nome": n} for c, n in ALLERGENS.items()],
            "objetivos": [
                {"nome": nome, "alvo": alvo} for nome, alvo in bot.TARGETS.items()
            ],
            "porcoes": porcoes(pessoa),
            "combinacoes": combinacoes(),
            "montagens": montagens(pessoa),
            "meu_dia": meu_dia(pessoa),
            "progresso": progresso(pessoa),
            "semana": semana(pessoa),
            "perfil": perfil(pessoa),
            "avaliacao": avaliacao(),
            "registro_previsto": registro_previsto(pessoa),
            "plano_exemplo": plano_exemplo(ficha),
        }
    finally:
        Path(tmp.name).unlink(missing_ok=True)

    saida.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{saida}: {len(dados['cardapio'])} categorias, "
          f"{len(dados['porcoes'].get('itens', []))} porcoes, "
          f"{len(dados['plano_exemplo'])} itens de plano")
    return 0


class _FakeUpdate:
    """O minimo que `current_employee` precisa."""

    def __init__(self, user_id: int):
        from types import SimpleNamespace
        self.effective_user = SimpleNamespace(id=user_id)


if __name__ == "__main__":
    raise SystemExit(main())
