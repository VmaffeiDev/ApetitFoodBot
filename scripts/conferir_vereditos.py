"""Confere, num navegador de verdade, se o app concorda com o Python.

    python scripts/conferir_vereditos.py

O cadastro do funcionario mora no aparelho dele: o app e um PWA sem servidor, e
a lista de alergias que ele marca nunca sai do celular. Por isso o navegador tem
de decidir o veredito de cada prato sozinho, o que poe a regra dos tres estados
em dois lugares — `apetit/allergens.py` e o JavaScript de `demo/index.html`.

Duas implementacoes da mesma regra discordam um dia. Quando isso acontecer aqui,
a divergencia nao vai aparecer como tela feia: vai aparecer como um prato com
leite pintado de verde para quem tem alergia a leite. Este script existe para a
discordancia falhar num teste em vez de num almoco.

Ele abre o app no Chromium, entra com cada combinacao de alergia de
`dados.conformidade` como se fosse o cadastro da pessoa, e compara o veredito de
cada prato **como ele chega na tela** (`data-veredito` no botao do prato) com o
que `check_item` calculou em Python. Um unico prato diferente derruba o script.

Sai com codigo 1 se algo divergir, 0 se tudo bater.
"""

import asyncio
import functools
import http.server
import json
import socketserver
import sys
import threading
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DEMO = RAIZ / "demo"
CHROMIUM = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"

# O cadastro precisa estar completo para o app aceitar e ir para a home; so a
# lista de restricoes varia entre os casos.
BASE = {
    "nome": "Conferencia",
    "refeitorio": "SM",
    "empresa": "Industria Exemplo",
    "setor": "Producao",
    "objetivo": "Manter o equilibrio",
    "consentimento": True,
}


class Silencioso(http.server.SimpleHTTPRequestHandler):
    """Uma linha de log por arquivo esconderia a unica saida que importa."""

    def log_message(self, *args):
        pass


def servir(pasta: Path):
    """Um endereco http de verdade: `localStorage` nao funciona em `file://`."""
    manipulador = functools.partial(Silencioso, directory=str(pasta))
    socketserver.TCPServer.allow_reuse_address = True
    servidor = socketserver.TCPServer(("127.0.0.1", 0), manipulador)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    return servidor, servidor.server_address[1]


async def conferir(casos: list[dict], porta: int) -> list[str]:
    from playwright.async_api import async_playwright

    problemas: list[str] = []
    async with async_playwright() as p:
        navegador = await p.chromium.launch(executable_path=CHROMIUM)
        contexto = await navegador.new_context(viewport={"width": 393, "height": 852})
        pagina = await contexto.new_page()
        pagina.on("pageerror", lambda e: problemas.append(f"erro de pagina: {e}"))

        endereco = f"http://127.0.0.1:{porta}/index.html"
        # Uma primeira visita so para o `localStorage` do endereco existir antes
        # de escrever nele.
        await pagina.goto(endereco, wait_until="domcontentloaded")

        for caso in casos:
            cadastro = dict(BASE, restricoes=list(caso["restricoes"]))
            await pagina.evaluate(
                "d => localStorage.setItem('apetit-cadastro', JSON.stringify(d))",
                cadastro,
            )
            await pagina.goto(endereco, wait_until="domcontentloaded")
            await pagina.wait_for_selector('.aba:has-text("Cardápio")')
            await pagina.click('.aba:has-text("Cardápio")')
            await pagina.wait_for_selector(".prato[data-codigo]")
            na_tela = await pagina.evaluate(
                """() => {
                  var saida = {};
                  document.querySelectorAll('.prato[data-codigo]').forEach(function(n){
                    saida[n.dataset.codigo] = n.dataset.veredito;
                  });
                  return saida;
                }"""
            )

            quem = ", ".join(caso["restricoes"]) or "sem restricao"
            esperado = caso["vereditos"]
            faltando = sorted(set(esperado) - set(na_tela))
            if faltando:
                problemas.append(f"[{quem}] pratos que nao apareceram na tela: {faltando}")
            for codigo in sorted(set(esperado) & set(na_tela)):
                if na_tela[codigo] != esperado[codigo]:
                    problemas.append(
                        f"[{quem}] {codigo}: a tela diz {na_tela[codigo]!r}, "
                        f"o Python diz {esperado[codigo]!r}"
                    )

        await navegador.close()
    return problemas


def main() -> int:
    dados = json.loads((DEMO / "dados.json").read_text(encoding="utf-8"))
    casos = dados.get("conformidade") or []
    if not casos:
        print(
            "dados.json nao tem a tabela `conformidade`. "
            "Rode `python scripts/demo_dados.py` antes.",
            file=sys.stderr,
        )
        return 1

    servidor, porta = servir(DEMO)
    try:
        problemas = asyncio.run(conferir(casos, porta))
    finally:
        servidor.shutdown()

    if problemas:
        print("\n".join(problemas), file=sys.stderr)
        print(f"\n{len(problemas)} divergencia(s).", file=sys.stderr)
        return 1

    pratos = len(casos[0]["vereditos"])
    print(f"ok: {len(casos)} combinacoes x {pratos} pratos conferem com o Python.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
