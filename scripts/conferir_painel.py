"""O painel da Apetit conferido pelo clique, contra a API de verdade.

Nao e teste de funcao: e o navegador entrando pelo e-mail, digitando o codigo e
lendo o que aparece na tela. O que este script existe para pegar e o defeito que
teste de Python nao ve — a rota recusar direitinho e a tela mostrar o numero
assim mesmo, ou o painel abrir para quem nao e da gestao porque a tela esqueceu
de olhar o status.

Tres caminhos:

    1. sem servidor        a pagina diz que nao ha painel, em vez de tela vazia
    2. sessao de funcionario  entra no app, e o painel recusa com recado claro
    3. sessao de gestao    as tres empresas aparecem, a pior primeiro, e a
                           empresa com poucas avaliacoes aparece **sem numero**

Uma coisa descoberta quebrando este script de proposito: entre "recorte pequeno"
e "numero na tela da gestao" ha tres camadas — `_report` zera os valores, a API
omite as chaves, e a tela nem desenha o bloco. Quebrar as duas de cima nao faz
numero nenhum aparecer, porque a essa altura ele ja nao existe. Quem segura de
verdade e o `_report`, e e quebrando ele que este script acusa.

    python scripts/conferir_painel.py
"""

import asyncio
import datetime
import http.server
import json
import socket
import sqlite3
import sys
import tempfile
import threading
import functools
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apetit import identidade  # noqa: E402
from apetit.api import criar_app  # noqa: E402
from apetit.catalog import init_schema  # noqa: E402
from apetit.feedback import MIN_RATINGS, Rating, save_rating  # noqa: E402
from apetit.profile import Employee, save_employee  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent
CHROME = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
SEGREDO = "segredo-de-conferencia-bem-comprido"
GESTOR = "coordenacao@apetit-exemplo.com.br"
FUNCIONARIO = "joana@metalurgica-exemplo.com.br"

# Copel bem atendida, Coca-Cola mal atendida, Sanepar pequena demais para contar.
# E o cenario da pergunta que originou o painel.
EMPRESAS = (
    ("Copel", 3, 3, MIN_RATINGS, ""),
    ("Coca-Cola", 1, 1, MIN_RATINGS, "Faltou tempero e o atendimento foi ruim"),
    ("Sanepar", 3, 3, MIN_RATINGS - 1, "Precisa melhorar o tempero"),
)


def porta_livre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Caixa:
    """Guarda o codigo que teria ido por e-mail, para o navegador digitar."""

    def __init__(self):
        self.por_email = {}

    def __call__(self, email, codigo):
        self.por_email[email] = codigo


def dias_atras(n: int) -> str:
    """Datas recentes de proposito: o painel abre nos ultimos 30 dias, e semear
    no ano passado testaria so a tela de 'periodo sem avaliacao'."""
    return (datetime.date.today() - datetime.timedelta(days=n)).isoformat()


def semear(caminho: Path) -> None:
    conn = sqlite3.connect(caminho)
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    identidade.autorizar(conn, GESTOR, "SM", papel=identidade.PAPEL_GESTAO)
    identidade.autorizar(conn, FUNCIONARIO, "SM")
    for empresa, comida, atendimento, quantos, comentario in EMPRESAS:
        for i in range(quantos):
            pessoa = abs(hash((empresa, i))) % 90000 + 2000
            save_employee(conn, Employee(
                pessoa_id=pessoa, name=f"P{i}", apetit_unit="SM",
                client_company=empresa, sector="Operacao", goal="manter",
            ))
            save_rating(conn, pessoa, Rating(
                apetit_unit="SM", service_date=dias_atras(3 + i),
                food=comida, service=atendimento,
                missing=(comida == 1), tags=["acabou"] if comida == 1 else [],
                comment=comentario,
            ))
    conn.close()


def subir_api(caminho: Path, porta: int, caixa: Caixa) -> threading.Thread:
    def rodar():
        asyncio.set_event_loop(asyncio.new_event_loop())

        def abrir():
            conn = sqlite3.connect(caminho)
            conn.row_factory = sqlite3.Row
            return conn

        criar_app(abrir, "token", segredo=SEGREDO, enviar=caixa).listen(porta)
        asyncio.get_event_loop().run_forever()

    t = threading.Thread(target=rodar, daemon=True)
    t.start()
    return t


def subir_site(pasta: Path, porta: int) -> threading.Thread:
    manipulador = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(pasta))
    servidor = http.server.ThreadingHTTPServer(("127.0.0.1", porta), manipulador)
    t = threading.Thread(target=servidor.serve_forever, daemon=True)
    t.start()
    return t


def preparar_pagina(pasta: Path, api: str) -> None:
    """Copia o painel apontando para a API, sem editar o arquivo do repositorio."""
    html = (RAIZ / "demo" / "painel.html").read_text(encoding="utf-8")
    html = html.replace('<meta name="apetit-api" content="">',
                        f'<meta name="apetit-api" content="{api}">')
    (pasta / "painel.html").write_text(html, encoding="utf-8")
    # A mesma pagina, sem servidor, para o primeiro caminho.
    (pasta / "painel-sem-api.html").write_text(
        (RAIZ / "demo" / "painel.html").read_text(encoding="utf-8"), encoding="utf-8")


async def entrar(pagina, email, caixa, api_porta):
    await pagina.fill("#email", email)
    await pagina.click("#botao-entrar")
    await pagina.wait_for_selector("#passo-codigo:not([hidden])", timeout=5000)
    for _ in range(50):
        if email in caixa.por_email:
            break
        await pagina.wait_for_timeout(100)
    await pagina.fill("#codigo", caixa.por_email[email])
    await pagina.click("#botao-entrar")


async def principal() -> int:
    from playwright.async_api import async_playwright

    problemas = []
    with tempfile.TemporaryDirectory() as tmp:
        pasta = Path(tmp)
        banco = pasta / "painel.db"
        semear(banco)

        api_porta, site_porta = porta_livre(), porta_livre()
        api = f"http://127.0.0.1:{api_porta}"
        caixa = Caixa()
        subir_api(banco, api_porta, caixa)
        preparar_pagina(pasta, api)
        subir_site(pasta, site_porta)
        site = f"http://127.0.0.1:{site_porta}"
        await asyncio.sleep(0.6)

        async with async_playwright() as p:
            navegador = await p.chromium.launch(executable_path=CHROME)
            ctx = await navegador.new_context(viewport={"width": 430, "height": 900})
            pagina = await ctx.new_page()
            erros = []
            pagina.on("pageerror", lambda e: erros.append(str(e)))

            # 1. sem servidor
            await pagina.goto(f"{site}/painel-sem-api.html")
            await pagina.wait_for_timeout(400)
            if await pagina.is_hidden("#sem-servidor"):
                problemas.append("1. sem servidor: a pagina nao avisa que nao ha painel")
            if await pagina.is_visible("#entrada"):
                problemas.append("1. sem servidor: oferece entrar num painel que nao existe")

            # 2. sessao de funcionario
            await pagina.goto(f"{site}/painel.html")
            await entrar(pagina, FUNCIONARIO, caixa, api_porta)
            await pagina.wait_for_selector("#recado-painel:not([hidden])", timeout=8000)
            recado = (await pagina.text_content("#recado-painel")) or ""
            if "papel de gest" not in recado:
                problemas.append(f"2. funcionario: recado nao explica o motivo — {recado!r}")
            corpo = (await pagina.text_content("body")) or ""
            for empresa in ("Copel", "Coca-Cola", "Sanepar"):
                if empresa in corpo:
                    problemas.append(f"2. funcionario: viu {empresa} na tela")

            # 3. sessao de gestao
            await pagina.evaluate("localStorage.clear()")
            await pagina.goto(f"{site}/painel.html")
            await entrar(pagina, GESTOR, caixa, api_porta)
            await pagina.wait_for_selector(".empresa", timeout=8000)
            nomes = await pagina.eval_on_selector_all(
                ".empresa h2", "ns => ns.map(n => n.textContent.trim())")
            if nomes[:1] != ["Coca-Cola"]:
                problemas.append(f"3. gestao: a pior empresa nao vem primeiro — {nomes}")
            if set(nomes) != {"Copel", "Coca-Cola", "Sanepar"}:
                problemas.append(f"3. gestao: faltou empresa na tela — {nomes}")

            texto_coca = await pagina.eval_on_selector_all(
                ".empresa",
                "ns => ns.filter(n => n.querySelector('h2').textContent.includes('Coca'))"
                "      .map(n => n.textContent)[0] || ''")
            if "0%" not in texto_coca:
                problemas.append("3. gestao: a Coca-Cola nao mostra o quanto caiu")
            if "Faltou tempero" not in texto_coca:
                problemas.append("3. gestao: o comentario da empresa com volume nao aparece")

            texto_sanepar = await pagina.eval_on_selector_all(
                ".empresa",
                "ns => ns.filter(n => n.querySelector('h2').textContent.includes('Sanepar'))"
                "      .map(n => n.textContent)[0] || ''")
            if "%" in texto_sanepar:
                problemas.append(
                    "3. gestao: a empresa suprimida mostrou porcentagem — "
                    f"{texto_sanepar.strip()[:120]!r}")
            if "Precisa melhorar o tempero" in texto_sanepar:
                problemas.append("3. gestao: comentario de recorte pequeno vazou para a tela")

            # O corpo da resposta tambem nao pode carregar pessoa.
            cru = await pagina.evaluate(
                """async (api) => {
                     const periodo = 'desde=' + document.getElementById('desde').value +
                                     '&ate=' + document.getElementById('ate').value;
                     const r = await fetch(api + '/api/gestao/feedback?' + periodo,
                       { headers: { Authorization: 'Bearer ' + localStorage.getItem('apetit-painel-token') } });
                     return JSON.stringify(await r.json());
                   }""", api)
            for proibido in ("pessoa_id", "email", "sector"):
                if proibido in cru:
                    problemas.append(f"3. gestao: o corpo da API carrega {proibido}")

            # nada pode rolar para o lado num celular
            largura = await pagina.evaluate(
                "document.documentElement.scrollWidth - document.documentElement.clientWidth")
            if largura > 1:
                problemas.append(f"3. gestao: a tela rola {largura}px para o lado num celular")

            import os
            if os.getenv("APETIT_RETRATO"):
                await pagina.screenshot(path=os.getenv("APETIT_RETRATO"), full_page=True)
            if erros:
                problemas.append(f"erro de JavaScript: {erros}")

            await navegador.close()

    if problemas:
        print("PROBLEMAS:")
        for x in problemas:
            print(" -", x)
        return 1
    print("Os tres caminhos passaram.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(principal()))
