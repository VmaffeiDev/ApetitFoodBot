"""Empacota a demonstracao numa pagina unica, para virar um link.

    python scripts/demo_pagina.py saida.html
    python scripts/demo_pagina.py --documento preview/Apetit-previa-visual.html

O `demo/` e um PWA de varios arquivos: instala na tela inicial, guarda o
shell em cache e busca `telas.json` e `dados.json` por HTTP. Isso exige um
endereco com HTTPS proprio, e enquanto ele nao existe ninguem consegue abrir
o app para testar.

Este script resolve a espera pelo outro lado: junta tudo — telas, dados e as
regras de leitura da ficha — dentro de um HTML so, que abre de qualquer
lugar. O que sai daqui **nao instala** na tela inicial, e por isso o convite
de instalar e a parte de service worker ficam de fora em vez de ficarem ali
sem funcionar: botao que promete e nao entrega e pior que botao ausente.

A fonte continua sendo `demo/index.html`. Nada e reescrito a mao aqui, para
as duas versoes nao divergirem: o que muda e a forma de entregar os dados,
que o proprio app ja sabe receber por `window.__APETIT__`.
"""

import argparse
import json
import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DEMO = RAIZ / "demo"

# Tags que so fazem sentido num documento proprio. Numa pagina embutida elas
# nao tem efeito, e a de instalar teria efeito errado: dentro de um quadro, o
# "Adicionar a Tela de Inicio" salvaria a pagina de fora, nao o app.
FORA = (
    r'<meta charset[^>]*>',
    r'<meta name="viewport"[^>]*>',
    r'<link rel="manifest"[^>]*>',
    r'<meta name="theme-color"[^>]*>',
    r'<link rel="icon"[^>]*>',
    r'<link rel="apple-touch-icon"[^>]*>',
    r'<meta name="apple-mobile-web-app-[^>]*>',
    r'<meta name="mobile-web-app-capable"[^>]*>',
)

AVISO = (
    '<div class="instalar on" id="faixa-instalar">'
    '<p id="instalar-texto">Prévia para testar. Para instalar na tela inicial, '
    'o app precisa de um endereço próprio com HTTPS.</p>'
    '</div>'
)


def escapar_json(valor) -> str:
    """`</script>` dentro do JSON encerraria o bloco antes da hora."""
    return json.dumps(valor, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")


def empacotar(documento: bool = False) -> str:
    html = (DEMO / "index.html").read_text(encoding="utf-8")

    titulo = re.search(r"<title>(.*?)</title>", html, re.S)
    estilo = re.search(r"<style>(.*?)</style>", html, re.S)
    corpo = re.search(r"<body>(.*?)</body>", html, re.S)
    if not (titulo and estilo and corpo):
        raise SystemExit("demo/index.html mudou de forma: title, style ou body nao achados.")

    cabeca = html[html.index("<head>"):html.index("</head>")]
    externos = "\n".join(
        linha.strip() for linha in cabeca.splitlines()
        if ("cdnjs.cloudflare.com" in linha or "fonts.googleapis.com" in linha
            or "rel=\"preconnect\"" in linha)
    )

    miolo = corpo.group(1)
    for padrao in FORA:
        miolo = re.sub(padrao, "", miolo)
    # O convite de instalar sai inteiro e da lugar a um aviso honesto.
    miolo = re.sub(
        r'<div class="instalar" id="faixa-instalar">.*?</div>', AVISO, miolo, flags=re.S
    )
    # Sem service worker: nao ha o que cachear numa pagina unica, e o registro
    # falharia em silencio dentro de um quadro.
    miolo = re.sub(
        r'  if \("serviceWorker" in navigator\)\{.*?\n  \}\n', "", miolo, flags=re.S
    )
    # O mesmo motivo do aviso acima: sem instalacao possivel, a logica de
    # convite viraria promessa falsa.
    miolo = re.sub(
        r'  // ---------- instalar na tela inicial ----------.*?'
        r'  // ---------- ler a ficha',
        "  // ---------- ler a ficha", miolo, flags=re.S
    )

    dados = {
        "telas": json.loads((DEMO / "telas.json").read_text(encoding="utf-8")),
        "dados": json.loads((DEMO / "dados.json").read_text(encoding="utf-8")),
        "regras": json.loads((DEMO / "regras.json").read_text(encoding="utf-8")),
    }

    cabeca_final = (
        f"<title>{titulo.group(1)}</title>\n"
        f"{externos}\n"
        f"<style>{estilo.group(1)}</style>\n"
    )
    corpo_final = (
        f"<script>window.__APETIT__ = {escapar_json(dados)};</script>\n"
        f"{miolo}\n"
    )
    if documento:
        corpo_final = corpo_final.replace(AVISO, (
            '<div class="instalar on" id="faixa-instalar">'
            '<p id="instalar-texto">Prévia interativa · Cardápio e perfil de exemplo</p>'
            '</div>'
        ))
        return (
            '<!doctype html>\n<html lang="pt-BR">\n<head>\n'
            '<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
            f'{cabeca_final}</head>\n<body>\n{corpo_final}</body>\n</html>\n'
        )
    return cabeca_final + corpo_final


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("saida", nargs="?", default="apetit.html")
    parser.add_argument("--documento", action="store_true",
                        help="Gera um HTML completo para abrir diretamente no navegador.")
    args = parser.parse_args()
    saida = Path(args.saida)
    pagina = empacotar(documento=args.documento)
    saida.write_text(pagina, encoding="utf-8")
    print(f"{saida}: {len(pagina) / 1024:.0f} KB numa pagina so")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
