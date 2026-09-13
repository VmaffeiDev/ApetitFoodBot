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


async def conferir(dados: dict, porta: int) -> list[str]:
    from playwright.async_api import async_playwright

    casos = dados["conformidade"]
    objetivos = [o["nome"] for o in dados["objetivos"]]
    alvos = {o["nome"]: o["alvo"] for o in dados["objetivos"]}
    problemas: list[str] = []

    async with async_playwright() as p:
        navegador = await p.chromium.launch(executable_path=CHROMIUM)
        contexto = await navegador.new_context(viewport={"width": 393, "height": 852})
        # Fonte e leitor de PDF vem de CDN e nao entram em nada que se confere
        # aqui. Cortar a ida ate eles tira dez segundos de cada uma das dezenas
        # de cargas, e faz a conferencia rodar em minutos em vez de meia hora.
        await contexto.route(
            "**/*",
            lambda rota: rota.abort()
            if "127.0.0.1" not in rota.request.url
            else rota.continue_(),
        )
        pagina = await contexto.new_page()
        pagina.on("pageerror", lambda e: problemas.append(f"erro de pagina: {e}"))

        endereco = f"http://127.0.0.1:{porta}/index.html"
        # Uma primeira visita so para o `localStorage` do endereco existir antes
        # de escrever nele.
        await pagina.goto(endereco, wait_until="domcontentloaded")

        for caso in casos:
            for objetivo in objetivos:
                quem = (", ".join(caso["restricoes"]) or "sem restricao") + f" / {objetivo}"
                cadastro = dict(BASE, restricoes=list(caso["restricoes"]), objetivo=objetivo)
                await pagina.evaluate(
                    "d => localStorage.setItem('apetit-cadastro', JSON.stringify(d))",
                    cadastro,
                )
                await pagina.goto(endereco, wait_until="domcontentloaded")

                # 1. o veredito de cada prato, como ele chega na tela
                await pagina.click('.aba:has-text("Cardápio")')
                # Espera folgada de proposito: `dados.json` passa de um mega, e
                # o `JSON.parse` dele na carga chega a mais de um segundo num
                # aparelho modesto. Com o limite padrao, esta conferencia
                # falhava de vez em quando por lentidao — e teste que falha
                # sozinho ensina a ignorar teste que falha.
                await pagina.wait_for_selector(".prato[data-codigo]", timeout=60000)
                na_tela = await pagina.evaluate(
                    """() => {
                      var saida = {};
                      document.querySelectorAll('.prato[data-codigo]').forEach(function(n){
                        saida[n.dataset.codigo] = n.dataset.veredito;
                      });
                      return saida;
                    }"""
                )
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

                # 2. a sugestao de porcoes, que e a parte perigosa
                bloqueados = {c for c, v in esperado.items() if v == "bloqueio"}
                sugerido = await abrir_sugestao(pagina)
                if sugerido is None:
                    problemas.append(f"[{quem}] a tela de porcoes nao montou")
                    continue
                dentro = sorted(bloqueados & set(sugerido["itens"]))
                if dentro:
                    problemas.append(
                        f"[{quem}] A SUGESTAO INCLUI PRATO BLOQUEADO: {dentro}"
                    )
                # 3. e ela tem de ser a do objetivo escolhido, nao a de outro
                alvo = alvos[objetivo]
                if sugerido["alvo_kcal"] != alvo["kcal"] or sugerido["alvo_ptn"] != alvo["ptn"]:
                    problemas.append(
                        f"[{quem}] a sugestao mira {sugerido['alvo_kcal']} kcal / "
                        f"{sugerido['alvo_ptn']} g, mas o objetivo pede "
                        f"{alvo['kcal']} kcal / {alvo['ptn']} g"
                    )

        await navegador.close()
    return problemas


async def abrir_sugestao(pagina):
    """Os codigos dos pratos que "Quanto pegar hoje" esta sugerindo.

    Le a tela, e nao o JSON: o que importa e o que a pessoa vai pegar no
    balcao depois de ler aquilo.
    """
    await pagina.click('.aba:has-text("Início")')
    await pagina.click('.cta:has-text("Quanto pegar hoje")')
    try:
        await pagina.wait_for_selector("[data-sugestao]", timeout=30000)
    except Exception:
        return None
    return await pagina.evaluate(
        """() => {
          var n = document.querySelector('[data-sugestao]');
          return {
            itens: [...n.querySelectorAll('[data-codigo]')].map(x => x.dataset.codigo),
            alvo_kcal: Number(n.dataset.alvoKcal),
            alvo_ptn: Number(n.dataset.alvoPtn)
          };
        }"""
    )


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
        problemas = asyncio.run(conferir(dados, porta))
    finally:
        servidor.shutdown()

    if problemas:
        print("\n".join(problemas), file=sys.stderr)
        print(f"\n{len(problemas)} divergencia(s).", file=sys.stderr)
        return 1

    pratos = len(casos[0]["vereditos"])
    objetivos = len(dados.get("objetivos", []))
    print(f"ok: {len(casos)} combinacoes de alergia x {objetivos} objetivos "
          f"x {pratos} pratos. Nenhum veredito diverge do Python, e nenhuma "
          f"sugestao inclui prato bloqueado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
