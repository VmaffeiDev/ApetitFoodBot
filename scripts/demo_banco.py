"""O banco de exemplo da demonstracao: o cardapio, a Mariana e o piloto inventado.

Isto morava em `scripts/demo_telas.py`, o script que capturava as telas do bot.
Nada aqui e do Telegram — e cardapio, cadastro e consumo —, mas ficava la porque
era de la que o exemplo nascia. Quando o bot saiu, o exemplo teria saido junto,
e `demo/dados.json` nao teria mais de onde vir.

O que este banco monta e escolhido para as telas mostrarem as tres respostas de
alergenico ao mesmo tempo: OVO COZIDO bloqueia, SAL. MIX DE ALFACE libera, e o
resto fica em "ninguem declarou". Uma demonstracao em que tudo aparece liberado
ensinaria a coisa errada sobre o app.
"""

import csv
import sqlite3
from pathlib import Path

from apetit.allergens import Restriction
from apetit.catalog import import_menu_csv, init_schema, set_item_allergens
from apetit.csv_import import slugify
from apetit.feedback import Rating, save_rating
from apetit.profile import TARGETS, Employee, save_employee
from apetit.recipes import declarations_by_item, read_recipe_rows
from apetit.spreadsheet import read_spreadsheet_rows
from apetit.tracking import log_consumption, score_day

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
DIA = "2025-09-01"          # segunda-feira coberta pelo cardapio de exemplo
USUARIO = 777


def abrir(caminho: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(caminho)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def preparar_banco(caminho: Path, receitas: Path | None = None) -> None:
    conn = abrir(caminho)
    init_schema(conn)
    import_menu_csv(conn, (FIXTURES / "cardapio_largo.csv").read_text(encoding="utf-8"), unit="SM")

    # O alergenico deduzido da lista de ingredientes: e o que faz o cardapio da
    # demonstracao mostrar bloqueio e atencao de verdade, em vez de "nao
    # declarado" em tudo.
    #
    # Sem a planilha da empresa cai na ficha de exemplo, e nao em "nenhum
    # alergenico". Ela e pequena de proposito — cobre so os pratos do cardapio
    # de exemplo —, mas passa pelo mesmo `allergens_for_recipe`: quem decide o
    # que cada prato declara continua sendo o codigo, nao esta tabela.
    linhas = (
        read_spreadsheet_rows(receitas, sheet="Receitas")
        if receitas and receitas.exists()
        else list(csv.reader(
            (FIXTURES / "receitas.csv").read_text(encoding="utf-8").splitlines(), delimiter=";"))
    )
    pratos = declarations_by_item(read_recipe_rows(linhas), slugify)
    publicados = {linha["code"] for linha in conn.execute("SELECT code FROM menu_item").fetchall()}
    for match in pratos.values():
        if match.item_code in publicados and match.declarations:
            set_item_allergens(
                conn, match.item_code,
                {a: d.value for a, d in match.declarations.items()},
                source="lista de ingredientes", deduzida=True,
            )

    # O "liberado" nao sai de ingrediente: ingrediente prova presenca, nunca
    # ausencia. Ele so existe quando alguem conferiu o prato e assinou embaixo —
    # e e essa a unica origem dele aqui, com a fonte dizendo isso em voz alta.
    set_item_allergens(
        conn, "sal_mix_de_alface",
        {"ovos": "nao_contem", "leite": "nao_contem"},
        source="declaracao da cozinha",
    )

    save_employee(conn, Employee(
        telegram_id=USUARIO, name="Mariana", apetit_unit="SM",
        client_company="Industria Exemplo", sector="Producao",
        goal="Manter o equilibrio", consent_accepted=True,
        restrictions=[Restriction("ovos"), Restriction("leite")],
    ))

    # Um dia ja registrado, para "Meu dia" e "Meu progresso" terem conteudo.
    #
    # O `score_day` entra junto porque e o que acontece quando alguem registra:
    # sem ele a pessoa apareceria com zero ponto tendo almocado. Os 25 pontos
    # saem daqui — das regras de `apetit/tracking.py` somando 10 + 5 + 10 —, nao
    # de um numero escrito na interface.
    log_consumption(conn, USUARIO, "2025-08-29",
                    ["file_de_frango_grelhado", "arroz_parboilizado", "sal_mix_de_alface"])
    score_day(conn, USUARIO, "2025-08-29",
              protein_target_g=TARGETS["Manter o equilibrio"]["ptn"])

    # Um piloto de 15 pessoas inventado, so para as telas de gestao terem numero
    # em vez de tabela vazia. Nenhuma dessas pessoas aparece nas telas do
    # funcionario, e o relatorio continua sem citar nenhuma delas.
    for i in range(15):
        save_employee(conn, Employee(
            telegram_id=900_001 + i, name=f"Teste {i}", apetit_unit="SM",
            client_company="Industria Exemplo", sector="Producao" if i % 2 else "Logistica",
            goal="Comer mais leve" if i % 3 else "Manter o equilibrio", consent_accepted=True,
        ))
    for i in range(11):
        log_consumption(conn, 900_001 + i, "2025-09-01", ["arroz_parboilizado"])
        if i < 8:
            log_consumption(conn, 900_001 + i, "2025-09-02", ["sal_mix_de_alface"])
    for i in range(9):
        save_rating(conn, 900_001 + i, Rating(
            apetit_unit="SM", service_date="2025-09-01",
            food=3 if i % 4 else 2, service=3 if i % 5 else 2,
        ))
    conn.close()
