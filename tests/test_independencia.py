"""O pacote `apetit/` nao depende do Telegram — verificado, e nao prometido.

Esta e a unica garantia que sustenta apagar o `bot.py`. Enquanto uma regra de
comida morar no arquivo do Telegram, o app web precisa do Telegram para saber o
alvo de alguem, e apagar o bot deixa de ser arrumacao e passa a ser uma quebra.

O teste falha no dia em que alguem importar `bot` (ou `telegram`) de dentro de
`apetit/` — inclusive por acidente, dentro de uma funcao. E dessa vez o aviso
chega no CI, e nao no dia da migracao.
"""

import ast
import subprocess
import sys
import unittest
from pathlib import Path

PACOTE = Path(__file__).resolve().parent.parent / "apetit"
PROIBIDOS = {"telegram", "bot"}


def importados(arquivo: Path) -> set[str]:
    """Todo modulo que o arquivo importa, inclusive dentro de funcoes.

    `ast.walk` e nao leitura do topo de proposito: o import tardio — aquele
    escrito dentro da funcao para quebrar um ciclo — e exatamente o que passaria
    despercebido numa revisao e continuaria arrastando a dependencia.
    """
    arvore = ast.parse(arquivo.read_text(encoding="utf-8"), filename=str(arquivo))
    nomes: set[str] = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.Import):
            nomes.update(a.name for a in no.names)
        elif isinstance(no, ast.ImportFrom) and no.level == 0 and no.module:
            nomes.add(no.module)
    return nomes


class IndependenciaTest(unittest.TestCase):
    def test_nenhum_modulo_do_apetit_importa_telegram(self):
        for arquivo in sorted(PACOTE.glob("*.py")):
            for nome in importados(arquivo):
                raiz = nome.split(".")[0]
                self.assertNotIn(
                    raiz, PROIBIDOS,
                    f"{arquivo.name} importa {nome}: regra de comida nao pode "
                    f"depender do Telegram",
                )

    def test_importar_o_dominio_nao_carrega_a_biblioteca_do_telegram(self):
        """O teste de verdade: o que o `import` faz, e nao o que o codigo diz.

        Um import transitivo — `apetit.x` importa `apetit.y`, que importa
        `telegram` — nao apareceria no teste de cima se `y` fizesse isso por um
        caminho que a leitura de AST nao alcanca (um `importlib` com o nome
        montado em tempo de execucao, por exemplo). Aqui a prova e o resultado.

        Num interpretador novo, e nao neste: rodando a bateria inteira, o
        `test_bot` ja importou `telegram` antes daqui, e um teste que olhasse o
        `sys.modules` deste processo passaria sozinho, sem provar nada.
        """
        modulos = [p.stem for p in sorted(PACOTE.glob("*.py")) if p.stem != "__init__"]
        programa = (
            "import sys\n"
            f"for nome in {modulos!r}:\n"
            "    __import__('apetit.' + nome)\n"
            f"achados = sorted(n for n in sys.modules if n.split('.')[0] in {sorted(PROIBIDOS)!r})\n"
            "print(','.join(achados))\n"
        )
        saida = subprocess.run(
            [sys.executable, "-c", programa],
            cwd=PACOTE.parent, capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(saida.returncode, 0, saida.stderr)
        self.assertEqual(
            saida.stdout.strip(), "",
            f"importar apetit/ carregou {saida.stdout.strip()}",
        )


if __name__ == "__main__":
    unittest.main()
