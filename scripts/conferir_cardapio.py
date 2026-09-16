"""Confere que a pagina do cardapio diz o mesmo que o importador de verdade.

    python scripts/conferir_cardapio.py

`demo/cardapio.html` existe para a operacao saber, antes de publicar, o que o
app entendeu do arquivo dela. O valor inteiro dela e ser confiavel: se ela
disser "18 itens, nenhum problema" e o bot publicar 16 com dois bloqueios,
ela e pior que nao existir.

Por isso ela nao reimplementa nada — roda o proprio `apetit/` no navegador, via
Pyodide. Este script prova que isso continua verdade: abre a pagina num
Chromium, solta cada cardapio de exemplo nela, e compara o que aparece na tela
com o que `import_menu_rows` devolve aqui em Python, para o mesmo arquivo.

Uma divergencia aqui significa que a pagina e o bot discordam sobre o cardapio
da semana, e o script falha.

Precisa do Pyodide em disco, porque a pagina o busca de um CDN e o teste nao
deve depender de rede:

    npm install --prefix scripts/pyodide pyodide@0.26.4

Sai com codigo 1 se algo divergir, 0 se tudo bater.
"""

import asyncio
import functools
import http.server
import json
import socketserver
import sqlite3
import sys
import threading
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DEMO = RAIZ / "demo"
FIXTURES = RAIZ / "tests" / "fixtures"
CHROMIUM = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
PYODIDE_LOCAL = RAIZ / "scripts" / "pyodide" / "node_modules" / "pyodide"
PYODIDE_CDN = "https://cdn.jsdelivr.net/pyodide/v0.26.4/full/"

MODULOS = ("model", "allergens", "allergy_text", "validation", "csv_import", "preflight")

# Os cardapios de exemplo, com o contexto que cada layout precisa. O de
# planejamento nao traz mes nem ano — e exatamente o caso em que a pagina tem
# de mostrar as datas para alguem conferir.
CASOS = (
    ("cardapio_largo.csv", {"unidade": "SM", "refeicao": "almoco", "mes": "", "ano": ""}),
    ("cardapio_longo.csv", {"unidade": "SM", "refeicao": "almoco", "mes": "", "ano": ""}),
    ("cardapio_alergenicos.csv", {"unidade": "SM", "refeicao": "almoco", "mes": "", "ano": ""}),
    ("cardapio_planejamento.csv", {"unidade": "SM", "refeicao": "almoco", "mes": "9", "ano": "2025"}),
)


class Silencioso(http.server.SimpleHTTPRequestHandler):
    """Uma linha de log por arquivo esconderia a unica saida que importa."""

    def log_message(self, *args):
        pass


def montar_site(destino: Path) -> None:
    """O site como o workflow do Pages monta: `demo/` mais o `apetit/` ao lado."""
    destino.mkdir(parents=True, exist_ok=True)
    for arquivo in DEMO.rglob("*"):
        if arquivo.is_file():
            alvo = destino / arquivo.relative_to(DEMO)
            alvo.parent.mkdir(parents=True, exist_ok=True)
            alvo.write_bytes(arquivo.read_bytes())
    pacote = destino / "apetit"
    pacote.mkdir(exist_ok=True)
    for modulo in MODULOS:
        (pacote / f"{modulo}.py").write_bytes((RAIZ / "apetit" / f"{modulo}.py").read_bytes())
    (pacote / "__init__.py").write_text("", encoding="utf-8")


def servir(pasta: Path):
    manipulador = functools.partial(Silencioso, directory=str(pasta))
    socketserver.TCPServer.allow_reuse_address = True
    servidor = socketserver.TCPServer(("127.0.0.1", 0), manipulador)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    return servidor, servidor.server_address[1]


def esperado(arquivo: Path, contexto: dict) -> dict:
    """O que o importador de verdade faz com este arquivo, aqui em Python."""
    sys.path.insert(0, str(RAIZ))
    from apetit.catalog import import_menu_rows, init_schema
    from apetit.csv_import import read_rows

    try:
        texto = arquivo.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        texto = arquivo.read_text(encoding="latin-1")

    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    try:
        resultado = import_menu_rows(
            conn, read_rows(texto),
            unit=contexto["unidade"], meal=contexto["refeicao"],
            month=int(contexto["mes"]) if contexto["mes"] else None,
            year=int(contexto["ano"]) if contexto["ano"] else None,
        )
    finally:
        conn.close()

    return {
        "publicados": len(resultado.published),
        "bloqueados": len(resultado.blocked),
        "dias": sorted({e.service_date for e in resultado.published}),
        "achados": sorted(
            (issue.code, issue.item_name, issue.blocking)
            for issue, _, _ in resultado.grouped_issues()
        ),
    }


async def na_pagina(porta: int, casos: list[tuple[Path, dict]]) -> list[dict]:
    from playwright.async_api import async_playwright

    vistos = []
    async with async_playwright() as p:
        navegador = await p.chromium.launch(executable_path=CHROMIUM)
        contexto = await navegador.new_context(viewport={"width": 1100, "height": 900})

        # A pagina busca o Pyodide num CDN. O teste serve a copia de disco no
        # lugar: assim ele nao depende de rede, e o que roda e exatamente o
        # arquivo que a pagina pediu.
        async def do_disco(rota):
            nome = rota.request.url.split(PYODIDE_CDN)[-1].split("?")[0]
            arquivo = PYODIDE_LOCAL / nome
            if not arquivo.exists():
                await rota.fulfill(status=404, body="")
                return
            tipos = {".js": "text/javascript", ".mjs": "text/javascript",
                     ".wasm": "application/wasm", ".json": "application/json",
                     ".zip": "application/zip", ".ts": "text/plain"}
            await rota.fulfill(
                status=200, body=arquivo.read_bytes(),
                content_type=tipos.get(arquivo.suffix, "application/octet-stream"),
            )

        await contexto.route(PYODIDE_CDN + "**", do_disco)
        pagina = await contexto.new_page()
        erros: list[str] = []
        pagina.on("pageerror", lambda e: erros.append(f"erro de pagina: {e}"))

        await pagina.goto(f"http://127.0.0.1:{porta}/cardapio.html", wait_until="domcontentloaded")

        for arquivo, dados in casos:
            for campo in ("unidade", "mes", "ano"):
                await pagina.fill(f"#{campo}", dados[campo])
            await pagina.select_option("#refeicao", dados["refeicao"])
            await pagina.set_input_files("#arquivo", str(arquivo))
            # A primeira conferencia inclui abrir o Pyodide, que nao e rapido.
            await pagina.wait_for_selector("#resultado:not([hidden])", timeout=180000)
            vistos.append(await pagina.evaluate(
                """() => {
                  var r = document.getElementById('resultado');
                  var linhas = [...r.querySelectorAll('table tr')].slice(1);
                  return {
                    publicados: Number(r.dataset.publicados),
                    bloqueados: Number(r.dataset.bloqueados),
                    dias_na_tela: linhas.length,
                    achados: [...r.querySelectorAll('.achado')].map(a => ({
                      tag: a.querySelector('.tag').textContent,
                      texto: a.textContent.replace(/\\s+/g, ' ').trim()
                    }))
                  };
                }"""
            ))
            vistos[-1]["erros"] = list(erros)
            erros.clear()

        await navegador.close()
    return vistos


def main() -> int:
    if not PYODIDE_LOCAL.exists():
        print(
            f"Pyodide nao encontrado em {PYODIDE_LOCAL}.\n"
            "Rode: npm install --prefix scripts/pyodide pyodide@0.26.4",
            file=sys.stderr,
        )
        return 1

    casos = [(FIXTURES / nome, ctx) for nome, ctx in CASOS]
    faltando = [c[0].name for c in casos if not c[0].exists()]
    if faltando:
        print(f"Cardapios de exemplo ausentes: {faltando}", file=sys.stderr)
        return 1

    import tempfile

    problemas: list[str] = []
    with tempfile.TemporaryDirectory() as pasta:
        site = Path(pasta) / "site"
        montar_site(site)
        servidor, porta = servir(site)
        try:
            vistos = asyncio.run(na_pagina(porta, casos))
        finally:
            servidor.shutdown()

    for (arquivo, _), visto in zip(casos, vistos):
        alvo = esperado(arquivo, dict(CASOS)[arquivo.name])
        nome = arquivo.name
        if visto["erros"]:
            problemas.append(f"[{nome}] {'; '.join(visto['erros'])}")
        if visto["publicados"] != alvo["publicados"]:
            problemas.append(
                f"[{nome}] a pagina diz {visto['publicados']} itens publicados, "
                f"o importador diz {alvo['publicados']}"
            )
        if visto["bloqueados"] != alvo["bloqueados"]:
            problemas.append(
                f"[{nome}] a pagina diz {visto['bloqueados']} bloqueados, "
                f"o importador diz {alvo['bloqueados']}"
            )
        if visto["dias_na_tela"] != len(alvo["dias"]):
            problemas.append(
                f"[{nome}] a pagina mostra {visto['dias_na_tela']} dias, "
                f"o importador achou {len(alvo['dias'])}"
            )
        na_tela = sum(1 for a in visto["achados"] if a["tag"] in ("bloqueio", "aviso"))
        if na_tela != len(alvo["achados"]):
            problemas.append(
                f"[{nome}] a pagina lista {na_tela} achados, "
                f"o importador agrupou {len(alvo['achados'])}"
            )
        for codigo, item, bloqueia in alvo["achados"]:
            if not item:
                continue
            if not any(item in a["texto"] for a in visto["achados"]):
                problemas.append(f"[{nome}] a pagina nao mostra o achado de {item!r} ({codigo})")

    if problemas:
        print("\n".join(problemas), file=sys.stderr)
        print(f"\n{len(problemas)} divergencia(s).", file=sys.stderr)
        return 1

    total = sum(e["publicados"] for e in (esperado(a, dict(CASOS)[a.name]) for a, _ in casos))
    print(f"ok: {len(casos)} cardapios, {total} itens. A pagina diz o mesmo que o importador.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
