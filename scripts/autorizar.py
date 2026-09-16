"""Quem pode entrar no app: poe, tira e lista os e-mails do refeitorio.

Uso:
    python scripts/autorizar.py --listar
    python scripts/autorizar.py --unidade SM joana@empresa.com.br rafael@empresa.com.br
    python scripts/autorizar.py --unidade SM --arquivo equipe.txt
    python scripts/autorizar.py --tirar joana@empresa.com.br
    python scripts/autorizar.py --papel gestao coordenacao@apetit.com.br

Por linha de comando, e nao por tela: no piloto quem entra sao quinze pessoas
definidas pela empresa, e uma tela de "adicionar funcionario" exposta na
internet seria a porta mais valiosa do sistema para um ataque — quem escreve
nessa lista escolhe quem le o cardapio de alergenico de quem.

Tirar da lista tambem derruba as sessoes abertas. Sem isso, a pessoa continuaria
dentro do app por trinta dias depois de sair da empresa.

O `--papel gestao` da acesso ao painel da Apetit, que le a avaliacao de todas as
empresas clientes. E o privilegio mais alto do sistema depois do token de
publicacao, e por isso mora aqui e nao numa tela: ninguem se promove sozinho.
Sem `--papel`, o papel de quem ja esta na lista **nao muda** — rodar a lista do
piloto de novo nao pode rebaixar a coordenacao em silencio.
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apetit import identidade  # noqa: E402
from apetit.catalog import connect, init_schema  # noqa: E402


def enderecos(args) -> list[str]:
    lista = list(args.emails)
    if args.arquivo:
        texto = Path(args.arquivo).read_text(encoding="utf-8")
        # Um por linha, e `#` comenta: a lista costuma vir colada de um e-mail
        # do RH, com cabecalho e nome de setor no meio.
        lista += [
            linha.strip() for linha in texto.splitlines()
            if linha.strip() and not linha.strip().startswith("#")
        ]
    return lista


def main() -> int:
    parser = argparse.ArgumentParser(description="Lista de quem pode entrar no app da Apetit.")
    parser.add_argument("emails", nargs="*", help="E-mails a autorizar.")
    parser.add_argument("--arquivo", default="", help="Arquivo com um e-mail por linha.")
    parser.add_argument("--unidade", default="", help="Unidade do refeitorio (ex.: SM).")
    parser.add_argument("--tirar", action="append", default=[],
                        help="E-mail a remover da lista (derruba as sessoes).")
    parser.add_argument("--papel", choices=sorted(identidade.PAPEIS), default=None,
                        help="Papel de quem entra. 'gestao' abre o painel da Apetit. "
                             "Omitido, mantem o papel de quem ja esta na lista.")
    parser.add_argument("--listar", action="store_true", help="Apenas mostra a lista e sai.")
    parser.add_argument("--banco", default=os.getenv("APETIT_DB_PATH", "apetit.db"),
                        help="Caminho do banco SQLite.")
    args = parser.parse_args()

    conn = connect(args.banco)
    try:
        init_schema(conn)

        if args.listar:
            linhas = identidade.autorizados(conn)
            if not linhas:
                print("Ninguem autorizado ainda.")
            for linha in linhas:
                print(f"{linha['email']:<45} id={linha['pessoa_id']:<8} "
                      f"unidade={linha['apetit_unit'] or '-':<8} papel={linha['papel']}")
            return 0

        for email in args.tirar:
            try:
                saiu = identidade.revogar(conn, email)
            except ValueError:
                print(f"e-mail invalido, nao mexi: {email}")
                continue
            print(f"{'removido' if saiu else 'nao estava na lista'}: {email.strip().lower()}")

        novos = enderecos(args)
        if not novos and not args.tirar:
            parser.error("informe e-mails, --arquivo, --tirar ou --listar")

        for email in novos:
            try:
                pessoa = identidade.autorizar(conn, email, args.unidade, args.papel)
            except ValueError:
                # Uma linha ruim no meio da lista nao pode impedir as outras de
                # entrar: e mais comum colar um arquivo com lixo do que digitar
                # um e-mail invalido de proposito.
                print(f"e-mail invalido, pulei: {email}")
                continue
            sufixo = f", papel {args.papel}" if args.papel else ""
            print(f"autorizado: {email.strip().lower()} (id {pessoa}{sufixo})")

        if args.papel == identidade.PAPEL_GESTAO and novos:
            print(f"\nAtencao: {len(novos)} pessoa(s) passam a ler a avaliacao de "
                  "todas as empresas clientes no painel da Apetit.")
        if not args.unidade and novos:
            print("\nAviso: sem --unidade, essas pessoas entram sem refeitorio definido "
                  "e o app nao sabe qual cardapio mostrar.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
