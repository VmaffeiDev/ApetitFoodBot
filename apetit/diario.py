"""O dia de **uma pessoa**: o cardapio conferido contra ela, e o alvo dela.

Isto e regra de negocio, e morava no `bot.py` — o arquivo do Telegram — por
acidente de historia: o bot foi a primeira porta, entao tudo nasceu la.

Enquanto so existia o bot, a diferenca era de arrumacao. Deixou de ser quando o
app web virou o produto: precisar do `bot.py` para saber o alvo de alguem
significaria arrastar a biblioteca do Telegram para dentro de um servidor que
nao fala com o Telegram.

O que fica no `bot.py` e o que e mesmo do Telegram: teclado, `update`, `context`.
O que decide alguma coisa sobre comida mora aqui.

A diferenca para `apetit/payload.py`: la e o que e **igual para todo mundo da
unidade**, e por isso pode ser servido sem login. Aqui e o que depende de quem
esta perguntando — restricao, ficha do nutricionista, alvo — e por isso exige
saber quem e.
"""

import sqlite3
from datetime import UTC, date, datetime, timedelta

from .allergens import ALLERGENS
from .catalog import check_menu_for_employee
from .portions import suggest_plate
from .prescription import Prescription, load_prescription
from .profile import TARGET_PADRAO, TARGETS, Employee


def hoje() -> str:
    return datetime.now(UTC).date().isoformat()


def inicio_da_semana(dia: str) -> str:
    d = date.fromisoformat(dia)
    return (d - timedelta(days=d.weekday())).isoformat()


def descrever_restricoes(pessoa: Employee) -> str:
    """A lista de restricoes em uma linha, dizendo o que cada uma significa.

    "Sem conferencia automatica" nao e detalhe: o app so consegue conferir o que
    a ficha tecnica do prato declara, e uma alergia escrita a mao fica fora
    disso. Dizer isso onde a pessoa le a propria lista evita que ela confie numa
    conferencia que nao acontece.
    """
    partes = [ALLERGENS[r.allergen] for r in pessoa.restrictions]
    partes += [f"{termo} (alergia, sem conferencia automatica)" for termo in pessoa.free_restrictions]
    partes += [f"{termo} (prefiro evitar)" for termo in pessoa.avoid_foods]
    return ", ".join(partes) or "nenhuma"


def alvo_de(pessoa: Employee, ficha: Prescription | None = None,
            conn: sqlite3.Connection | None = None) -> dict:
    """Alvo do almoco: o do nutricionista quando existe, o do objetivo quando nao.

    A ficha confirmada ganha do objetivo generico — ela e o numero de um
    profissional, o outro e ilustrativo. Campo que a ficha nao traz continua
    vindo do objetivo: sem proteina nao ha como pontuar o dia, e deixar o alvo
    pela metade quebraria o resto do app.

    Ficha implausivel (numero do dia inteiro entrando como refeicao) e ignorada
    aqui de proposito: melhor cair no alvo generico que sugerir 1.800 kcal num
    prato so.

    Sem `ficha`, busca a da pessoa: quem tem ficha tem ficha em toda tela, e
    esquecer de passar em uma delas faria o mesmo prato ser lido contra dois
    alvos diferentes.
    """
    if ficha is None and conn is not None:
        ficha = load_prescription(conn, pessoa.telegram_id)
    alvo = dict(TARGETS.get(pessoa.goal, TARGET_PADRAO))
    if ficha and ficha.tem_alvo and not ficha.implausivel():
        alvo.update(ficha.alvo_almoco())
    return alvo


def origem_do_alvo(pessoa: Employee, ficha: Prescription | None) -> dict[str, str]:
    """De onde veio cada numero do alvo: "ficha" ou "objetivo".

    A tela precisa disso para nao apresentar palpite como prescricao.
    """
    origem = {"kcal": "objetivo", "ptn": "objetivo"}
    if ficha and ficha.tem_alvo and not ficha.implausivel():
        for campo in ficha.alvo_almoco():
            origem[campo] = "ficha"
    return origem


def cardapio_de(conn: sqlite3.Connection, pessoa: Employee, dia: str) -> list[dict]:
    """O cardapio do dia ja conferido contra o que a pessoa nao pode comer.

    O que o nutricionista mandou evitar entra pelo mesmo caminho do "prefiro
    evitar": bloqueia quando o termo aparece no nome do prato, sem virar aviso
    de alergenico. E o tratamento correto — e uma orientacao profissional, nao
    um risco de reacao alergica, e tratar como alergia encheria o cardapio de
    aviso onde nao ha perigo nenhum.
    """
    ficha = load_prescription(conn, pessoa.telegram_id)
    evitar = list(dict.fromkeys(pessoa.avoid_foods + (ficha.proibidos if ficha else [])))
    return check_menu_for_employee(
        conn, dia, pessoa.restrictions,
        unit=pessoa.apetit_unit,
        unverifiable=pessoa.free_restrictions,
        avoid_terms=evitar,
    )


def sugestao_de(conn: sqlite3.Connection, pessoa: Employee, dia: str):
    """A sugestao de porcoes do dia, ou None quando nao ha cardapio para ela.

    Fica fora do handler porque a tela mostra a sugestao e o botao de salvar
    precisa recalcular a mesma coisa. Recalcular e de proposito: guardar a
    sugestao na sessao deixaria um botao de ontem gravando o prato de ontem.
    """
    from .allergens import Verdict

    alvo = alvo_de(pessoa, conn=conn)
    # So entra na sugestao o que a pessoa pode comer: sugerir quantidade de um
    # prato bloqueado seria pior que nao sugerir nada.
    liberados = [
        {
            "code": i["item_code"], "category": i["category"], "name": i["name"],
            "kcal": i["kcal"], "ptn_g": i["ptn_g"],
        }
        for i in cardapio_de(conn, pessoa, dia)
        if i["check"].verdict is not Verdict.BLOQUEIO
    ]
    if not liberados:
        return None
    return suggest_plate(liberados, alvo["kcal"], alvo["ptn"])
