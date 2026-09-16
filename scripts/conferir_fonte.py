"""Confere que o app nunca apresenta como de hoje um cardapio que nao e.

    python scripts/conferir_fonte.py

O app tem duas fontes para o cardapio: o servidor da Apetit, que responde o dia
de hoje, e `demo/dados.json`, uma fotografia publicada junto com a pagina. A
fotografia existe porque refeitorio tem sinal ruim e porque o piloto testa sem
servidor nenhum — um app que abre em branco no subsolo da fabrica nao serve.

O risco que vem junto e especifico deste app. Num leitor de noticias, mostrar
conteudo velho e um incomodo. Aqui, alguem planeja o almoco contra a lista de
pratos que a tela mostra, e confere alergenico nessa lista. Cardapio de outro
dia apresentado como o de hoje e conferir o prato errado.

Entao o app pode cair na fotografia — precisa poder — mas nao pode calar sobre
isso. Este script sobe um servidor de mentira e percorre os cinco caminhos:

    1. sem servidor configurado         usa a fotografia  e avisa
    2. servidor com o cardapio de hoje  usa o do servidor e nao avisa
    3. servidor fora do ar              cai na fotografia e avisa
    4. servidor com o cardapio de ontem usa o do servidor e avisa
    5. servidor com cardapio vazio      cai na fotografia

O caso 4 e o que justifica o script: e o unico em que o app tem dado fresco,
vindo da rede, e ainda assim precisa ressalvar. Um teste que so olhasse "veio do
servidor?" daria esse caso por bom.

Sai com codigo 1 se algum caminho falhar, 0 se todos passarem.
"""

import asyncio
import datetime
import functools
import http.server
import json
import socketserver
import sys
import threading
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent / "demo"
CHROMIUM = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
PORTA_APP = int(sys.argv[1]) if len(sys.argv) > 1 else 8731
PORTA_API = PORTA_APP + 1

HOJE = datetime.date.today().isoformat()
ONTEM = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
AVISO = "não é o cardápio de hoje"

FOTOGRAFIA = json.loads((RAIZ / "dados.json").read_text(encoding="utf-8"))

# O que a API de mentira devolve. Cada caso ajusta antes de abrir o app.
api = {"dia": HOJE, "fora_do_ar": False, "vazio": False}


def payload_do_dia() -> dict:
    corpo = dict(FOTOGRAFIA)
    corpo["dia"] = api["dia"]
    corpo["dia_amigavel"] = "cardápio de teste de " + api["dia"]
    if api["vazio"]:
        corpo["cardapio"] = []
    # O servidor de verdade nao manda nada de pessoa; o app junta com a
    # fotografia. Tirar aqui mantem a mentira fiel ao original.
    for chave in ("pessoa", "perfil", "progresso", "meu_dia", "semana", "gestao"):
        corpo.pop(chave, None)
    return corpo


class ApiDeMentira(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if api["fora_do_ar"]:
            self.send_response(503)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(b'{"erro":"fora do ar"}')
            return
        corpo = json.dumps(payload_do_dia()).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def log_message(self, *a):
        pass


LEITURA = "((document.querySelector('.tela')||{}).innerText||'')"


async def sem_internet(pagina):
    """Corta tudo que nao e local.

    A pagina pede fonte do Google e o leitor de PDF de um CDN. Sem cortar, uma
    conexao pendurada faz `networkidle` nunca chegar e o script trava por motivo
    que nao tem nada a ver com o que ele testa. E deixa o teste mais fiel: o
    refeitorio tem sinal ruim, e o app precisa abrir assim.
    """
    async def filtrar(rota):
        if "127.0.0.1" in rota.request.url:
            await rota.continue_()
        else:
            await rota.abort()
    await pagina.route("**/*", filtrar)


async def abrir(pagina, endereco_api: str) -> str:
    await pagina.goto(f"http://127.0.0.1:{PORTA_APP}/", wait_until="networkidle")
    if endereco_api:
        await pagina.evaluate("([u]) => localStorage.setItem('apetit-api', u)", [endereco_api])
    else:
        await pagina.evaluate("localStorage.removeItem('apetit-api')")
    await pagina.reload(wait_until="networkidle")
    # Sem cadastro o app abre nas boas-vindas; entra pelo exemplo, que e o
    # caminho de quem so quer ver.
    await pagina.click('button:has-text("Só olhar")')
    await pagina.wait_for_selector(".aba:not([disabled])", timeout=20000)
    await pagina.click('.aba:has-text("Cardápio")')
    await pagina.wait_for_timeout(250)
    return await pagina.evaluate(LEITURA)


async def main() -> int:
    from playwright.async_api import async_playwright

    app_srv = socketserver.TCPServer(
        ("127.0.0.1", PORTA_APP),
        functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(RAIZ)))
    app_srv.allow_reuse_address = True
    api_srv = socketserver.TCPServer(("127.0.0.1", PORTA_API), ApiDeMentira)
    api_srv.allow_reuse_address = True
    for servidor in (app_srv, api_srv):
        threading.Thread(target=servidor.serve_forever, daemon=True).start()

    endereco = f"http://127.0.0.1:{PORTA_API}"
    problemas: list[str] = []

    async with async_playwright() as p:
        navegador = await p.chromium.launch(executable_path=CHROMIUM)
        pagina = await navegador.new_page(viewport={"width": 390, "height": 844})
        await sem_internet(pagina)
        erros: list[str] = []
        pagina.on("pageerror", lambda e: erros.append(str(e)))

        api.update(dia=HOJE, fora_do_ar=False, vazio=False)
        texto = await abrir(pagina, "")
        print(f"1. sem servidor           avisa={AVISO in texto}")
        if AVISO not in texto:
            problemas.append("sem servidor: a fotografia passou como cardapio de hoje")

        api.update(dia=HOJE, fora_do_ar=False, vazio=False)
        texto = await abrir(pagina, endereco)
        veio = ("cardápio de teste de " + HOJE) in texto
        print(f"2. servidor com hoje      avisa={AVISO in texto}  do servidor={veio}")
        if AVISO in texto:
            problemas.append("cardapio de hoje do servidor nao devia gerar ressalva")
        if not veio:
            problemas.append("o cardapio do servidor nao foi usado")

        api.update(fora_do_ar=True)
        texto = await abrir(pagina, endereco)
        print(f"3. servidor fora do ar    avisa={AVISO in texto}")
        if AVISO not in texto:
            problemas.append("servidor fora do ar: caiu na fotografia sem avisar")
        if "Não foi possível carregar" in texto:
            problemas.append("servidor fora do ar derrubou o app em vez de usar a fotografia")

        api.update(dia=ONTEM, fora_do_ar=False)
        texto = await abrir(pagina, endereco)
        print(f"4. servidor com ontem     avisa={AVISO in texto}")
        if AVISO not in texto:
            problemas.append("servidor devolveu o cardapio de ontem e o app nao ressalvou")

        # `/api/dia` responde 404 quando nao ha cardapio, mas um proxy mal
        # configurado, ou uma versao futura do servidor, pode devolver 200 com
        # lista vazia — e "nenhum prato hoje" e pior que a fotografia com aviso.
        api.update(dia=HOJE, vazio=True)
        texto = await abrir(pagina, endereco)
        tem_pratos = "Arroz" in texto or "Carne" in texto
        print(f"5. servidor com vazio     usou a fotografia={tem_pratos}")
        if not tem_pratos:
            problemas.append("servidor com cardapio vazio deixou a pessoa sem cardapio nenhum")

        if erros:
            problemas.append("erro de JavaScript: " + " | ".join(dict.fromkeys(erros))[:300])
        await navegador.close()

    app_srv.shutdown()
    api_srv.shutdown()

    if problemas:
        print("\nPROBLEMAS:")
        for item in problemas:
            print(" -", item)
        return 1
    print("\nok: le do servidor, cai na fotografia quando precisa, "
          "e nunca chama de hoje um cardapio que nao e.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
