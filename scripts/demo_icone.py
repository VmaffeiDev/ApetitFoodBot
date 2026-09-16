"""Gera os icones do app de demonstracao.

    python scripts/demo_icone.py demo/

Um PWA precisa de icone em PNG para entrar na tela inicial do celular — e o
icone e a primeira coisa que a pessoa ve do "app instalado". Aqui ele e
desenhado em codigo em vez de virar binario solto no repositorio: assim da para
mudar a cor numa linha e refazer, e ninguem precisa abrir editor de imagem.

O `maskable` sai com margem folgada de proposito: o Android recorta o icone na
forma do lancador (circulo, quadrado arredondado, gota), e desenho colado na
borda perde pedaco.
"""

import sys
from pathlib import Path

# As cores da marca Apetit, tiradas do site: vermelho da faixa e amarelo dos
# botoes. O talher sai branco, como o logo.
VERMELHO = (236, 0, 63)      # #EC003F
AMARELO = (245, 217, 78)     # #F5D94E
BRANCO = (255, 255, 255)


def desenha(tamanho: int, margem: float = 0.0):
    from PIL import Image, ImageDraw

    # Desenha grande e reduz: e o antialias do pobre, e resolve a borda
    # serrilhada do prato sem depender de filtro de desenho.
    escala = 4
    lado = tamanho * escala
    img = Image.new("RGBA", (lado, lado), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    raio = int(lado * 0.22)
    d.rounded_rectangle([0, 0, lado - 1, lado - 1], radius=raio, fill=VERMELHO)

    # Area util: o maskable reserva margem para o recorte do lancador.
    centro = lado / 2
    util = lado * (1 - 2 * margem)

    # Prato: aro externo e circulo interno, que e o que se reconhece em 48px.
    r_prato = util * 0.30
    largura = max(1, int(util * 0.035))
    d.ellipse(
        [centro - r_prato, centro - r_prato, centro + r_prato, centro + r_prato],
        outline=BRANCO, width=largura,
    )
    r_fundo = util * 0.175
    d.ellipse(
        [centro - r_fundo, centro - r_fundo, centro + r_fundo, centro + r_fundo],
        fill=AMARELO,
    )

    # Garfo a esquerda: tres dentes, cabo. Some no tamanho pequeno, mas da a
    # silhueta de refeitorio que um circulo sozinho nao da.
    x = centro - util * 0.40
    topo = centro - util * 0.26
    dente_h = util * 0.15
    esp = max(1, int(util * 0.028))
    for i in (-1, 0, 1):
        dx = x + i * util * 0.055
        d.line([(dx, topo), (dx, topo + dente_h)], fill=BRANCO, width=esp)
    d.line([(x, topo + dente_h), (x, centro + util * 0.28)], fill=BRANCO, width=int(esp * 1.6))

    # Faca a direita: lamina e cabo.
    xf = centro + util * 0.40
    d.line([(xf, topo), (xf, topo + dente_h * 1.5)], fill=BRANCO, width=int(esp * 2.2))
    d.line([(xf, topo + dente_h * 1.5), (xf, centro + util * 0.28)], fill=BRANCO, width=int(esp * 1.6))

    return img.resize((tamanho, tamanho), 1)  # LANCZOS


def main() -> int:
    destino = Path(sys.argv[1] if len(sys.argv) > 1 else "demo")
    destino.mkdir(parents=True, exist_ok=True)

    for nome, tamanho, margem in (
        ("icon-192.png", 192, 0.0),
        ("icon-512.png", 512, 0.0),
        ("icon-maskable-512.png", 512, 0.14),
        ("apple-touch-icon.png", 180, 0.0),
    ):
        desenha(tamanho, margem).save(destino / nome)
        print(f"  {destino / nome}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
