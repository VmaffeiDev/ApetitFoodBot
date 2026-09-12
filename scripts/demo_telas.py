"""Captura as telas reais do bot para o simulador de teste.

    python scripts/demo_telas.py telas.json

O bot vive no Telegram, e por isso nao da para "mandar um link" dele. Este
script resolve o problema por outro lado: ele roda o **codigo de verdade** do
`bot.py` com o mesmo arnes dos testes, clica em cada botao e guarda o que a
tela devolveu. O que sai daqui e o que o funcionario veria no celular — texto,
botoes e avisos de alergenico incluidos —, nao uma maquete desenhada a mao.

A varredura reexecuta o caminho inteiro a cada clique, comecando do zero. E
mais lento e e de proposito: o `context.user_data` guarda estado (o prato que
esta sendo montado, a ficha em conferencia), e reaproveitar sessao faria a tela
capturada depender da ordem em que o crawler passou por ela.
"""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "demo")

import bot  # noqa: E402
from apetit.allergens import Restriction  # noqa: E402
from apetit.catalog import import_menu_csv, init_schema, set_item_allergens  # noqa: E402
from apetit.feedback import Rating, save_rating  # noqa: E402
from apetit.csv_import import slugify  # noqa: E402
from apetit.profile import Employee, save_employee  # noqa: E402
from apetit.recipes import declarations_by_item, read_recipe_rows  # noqa: E402
from apetit.spreadsheet import read_spreadsheet_rows  # noqa: E402
from apetit.tracking import log_consumption  # noqa: E402
from tests.test_bot import FakeContext, FakeUpdate  # noqa: E402

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
DIA = "2025-09-01"          # segunda-feira coberta pelo cardapio de exemplo
USUARIO = 777
PROFUNDIDADE = 6

# Telas que dependem de digitar algo, e por isso entram com o texto ja dado.
TEXTOS = {
    "ficha_manual": ["600", "35"],
}

# Botoes que nao levam a lugar nenhum util no simulador, ou que apagariam o
# cadastro no meio da varredura.
NAO_CLICAR = {"del_sim", "recadastrar", "ficha_remover_sim", "consent_nao"}


def preparar_banco(caminho: Path, receitas: Path | None) -> None:
    bot.DB_PATH = caminho
    conn = bot.db()
    init_schema(conn)
    import_menu_csv(conn, (FIXTURES / "cardapio_largo.csv").read_text(encoding="utf-8"), unit="SM")

    # O alergenico deduzido da planilha de receitas: e o que faz o cardapio do
    # simulador mostrar ⛔ e ⚠️ de verdade, em vez de "nao declarado" em tudo.
    if receitas and receitas.exists():
        pratos = declarations_by_item(read_recipe_rows(read_spreadsheet_rows(receitas, sheet="Receitas")), slugify)
        publicados = {linha["code"] for linha in conn.execute("SELECT code FROM menu_item").fetchall()}
        for match in pratos.values():
            if match.item_code in publicados and match.declarations:
                set_item_allergens(
                    conn, match.item_code,
                    {a: d.value for a, d in match.declarations.items()},
                    source="lista de ingredientes",
                )

    # Ovo e leite de proposito: com o alergenico vindo da receita, OVO COZIDO
    # vira ⛔ ("contem ovos") e PURE DE BATATA vira ⚠️ ("pode conter leite"),
    # enquanto o resto segue ⚠️ de "ninguem declarou". As tres respostas na
    # mesma tela — que e o ponto do modelo de tres estados.
    save_employee(conn, Employee(
        telegram_id=USUARIO, name="Mariana", apetit_unit="SM",
        client_company="Industria Exemplo", sector="Producao",
        goal="Manter o equilibrio", consent_accepted=True,
        restrictions=[Restriction("ovos"), Restriction("leite")],
    ))
    # Um dia ja registrado, para "Meu dia" e "Meu progresso" terem conteudo.
    log_consumption(conn, USUARIO, "2025-08-29", ["file_de_frango_grelhado", "arroz_parboilizado"])

    # Um piloto de 15 pessoas inventado, so para as telas de administracao
    # terem numero em vez de tabela vazia. Nenhuma dessas pessoas aparece nas
    # telas do funcionario — e o relatorio continua sem citar nenhuma delas.
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


# Telas que sao comando, nao botao: o crawler nao chega nelas clicando.
COMANDOS = [
    ("piloto", "show_pilot_report", ["16", "2025-09-01", "2025-09-30"], "administracao"),
    ("cobertura", "show_coverage", [], "administracao"),
    ("relatorio", "show_report", [], "administracao"),
    ("atendimento", "show_service_report", [], "administracao"),
]


async def capturar_comandos() -> dict:
    """Roda os comandos de administracao e guarda o que eles imprimem."""
    telas = {}
    for ident, funcao, args, grupo in COMANDOS:
        context = FakeContext()
        context.args = list(args)
        update = FakeUpdate(user_id=USUARIO)
        bot.ADMIN_IDS = {USUARIO}
        try:
            await getattr(bot, funcao)(update, context)
        except Exception as erro:
            print(f"  pulei /{ident}: {type(erro).__name__}: {erro}", file=sys.stderr)
            continue
        texto = "\n".join(update.message.replies)
        if texto:
            telas[ident] = {
                "texto": texto, "botoes": [], "caminho": [ident],
                "comando": f"/{ident} {' '.join(args)}".strip(), "grupo": grupo,
            }
    bot.ADMIN_IDS = set()
    return telas


async def rodar_caminho(caminho: list[str]) -> tuple[str, list]:
    """Refaz o caminho do zero e devolve a ultima tela."""
    context = FakeContext()
    update = FakeUpdate(user_id=USUARIO)
    await bot.start(update, context)

    pendentes = []
    for passo in caminho:
        if pendentes:
            update = FakeUpdate(user_id=USUARIO, text=pendentes.pop(0))
            await bot.handle_message(update, context)
            if pendentes:
                continue
        update = FakeUpdate(user_id=USUARIO, callback=passo)
        await bot.handle_callback(update, context)
        pendentes = list(TEXTOS.get(passo, []))
        for texto in pendentes:
            update = FakeUpdate(user_id=USUARIO, text=texto)
            await bot.handle_message(update, context)
        pendentes = []
    return update.last, update.buttons


async def varrer() -> dict:
    """Varre o bot como grafo, nao como arvore.

    A deduplicacao e pelo **conteudo da tela**, nao pelo caminho: "Voltar" leva
    ao menu venha de onde vier, e explorar o menu de novo a cada caminho faria
    a varredura explodir em combinacoes que mostram sempre a mesma coisa. Cada
    tela distinta e visitada uma vez e ganha um id estavel.
    """
    telas: dict[str, dict] = {}
    por_conteudo: dict[str, str] = {}
    caminhos_vistos: set[tuple[str, ...]] = set()
    fila: list[list[str]] = [[]]

    while fila:
        caminho = fila.pop(0)
        chave = tuple(caminho)
        if chave in caminhos_vistos or len(caminho) > PROFUNDIDADE:
            continue
        caminhos_vistos.add(chave)

        try:
            texto, botoes = await rodar_caminho(caminho)
        except Exception as erro:  # tela que depende de estado que o crawler nao monta
            print(f"  pulei {caminho}: {type(erro).__name__}: {erro}", file=sys.stderr)
            continue
        if not texto or texto in por_conteudo:
            continue

        ident = "inicio" if not caminho else caminho[-1]
        while ident in telas:
            ident += "_"
        por_conteudo[texto] = ident
        telas[ident] = {
            "texto": texto,
            "botoes": [{"rotulo": r, "acao": d} for r, d in botoes],
            "caminho": caminho,
        }
        for _, destino in botoes:
            if destino not in NAO_CLICAR:
                fila.append(caminho + [destino])

    # Liga cada botao a tela que ele abre, para o simulador navegar sozinho.
    destino_de = {tela["caminho"][-1]: ident for ident, tela in telas.items() if tela["caminho"]}
    for tela in telas.values():
        for botao in tela["botoes"]:
            botao["destino"] = destino_de.get(botao["acao"], "")
    return telas


def main() -> int:
    saida = Path(sys.argv[1] if len(sys.argv) > 1 else "telas.json")
    receitas = Path(sys.argv[2]) if len(sys.argv) > 2 else None

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    tmp.close()
    try:
        preparar_banco(Path(tmp.name), receitas)
        bot.today = lambda: DIA
        telas = asyncio.run(varrer())
        telas.update(asyncio.run(capturar_comandos()))
    finally:
        Path(tmp.name).unlink(missing_ok=True)

    saida.write_text(json.dumps(telas, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(telas)} telas capturadas em {saida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
