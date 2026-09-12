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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "demo")

import bot  # noqa: E402
from apetit.allergens import ALLERGENS  # noqa: E402
from apetit.humanize import (  # noqa: E402
    category_label, clean_dish_name, dish_hint, dish_weight, friendly_date,
    order_categories, week_summary,
)
from apetit.portions import FREE_CATEGORIES, measure_label  # noqa: E402
from apetit.prescription import extract_meal_plan, lunch_from_plan, read_pdf  # noqa: E402
from apetit.tracking import RULES, history_by_day, points_breakdown, total_points  # noqa: E402
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


def cardapio(pessoa) -> list[dict]:
    """O cardapio do dia, por categoria, com o veredito de cada prato."""
    grupos: dict[str, list[dict]] = {}
    for item in bot.load_menu(pessoa, DIA):
        check = item["check"]
        grupos.setdefault(item["category"], []).append({
            "nome": clean_dish_name(item["name"]),
            "veredito": MARCA.get(check.verdict.value, "atencao"),
            "etiqueta": etiqueta(check),
            "motivo": check.message() if check.verdict.value != "sem_restricao" else "",
            "peso": dish_weight(item.get("kcal")),
            "dica": dish_hint(item.get("kcal"), item.get("ptn_g")),
            "kcal": item.get("kcal"),
            "ptn": item.get("ptn_g"),
        })
    return [
        {"categoria": category_label(nome), "codigo": nome, "itens": grupos[nome]}
        for nome in order_categories(grupos)
    ]


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
            "porcoes": porcoes(pessoa),
            "meu_dia": meu_dia(pessoa),
            "progresso": progresso(pessoa),
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
