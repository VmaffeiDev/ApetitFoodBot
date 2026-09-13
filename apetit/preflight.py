"""Decidir o que publicar, antes e independentemente de gravar.

Ler o arquivo, validar cada item e separar o que publica do que vai para a fila
do nutricionista sao decisoes de dominio. Gravar no SQLite e outra coisa. Elas
viviam juntas em `catalog.import_menu_rows`, e enquanto o unico caminho era o
terminal isso nao incomodava.

Passou a incomodar quando a operacao ganhou uma pagina para **conferir** o
cardapio antes de mandar (`demo/cardapio.html`). Ela roda este mesmo codigo no
navegador, e nao tem banco nenhum: o Pyodide nem traz `sqlite3`. As opcoes eram
carregar um banco para joga-lo fora, ou reescrever estas oito linhas de decisao
do lado da pagina — e a segunda e a forma classica de a pagina dizer "seu
arquivo esta bom" e a publicacao discordar.

Entao a decisao mora aqui, sozinha, e `catalog.import_menu_rows` a chama antes
de escrever. Um so lugar decide; quem grava so grava.
"""

from .csv_import import parse_menu_rows
from .model import Issue, MenuEntry
from .validation import validate_item


class ImportResult:
    """O que sairia desta importacao: o publicado, o barrado e os porques."""

    def __init__(self, batch: str) -> None:
        self.batch = batch
        self.published: list[MenuEntry] = []
        self.blocked: list[MenuEntry] = []
        self.issues: list[Issue] = []

    @property
    def blocking_issues(self) -> list[Issue]:
        return [issue for issue in self.issues if issue.blocking]

    def grouped_issues(self) -> list[tuple[Issue, int, list[str]]]:
        """Agrupa por ficha tecnica.

        Um mesmo item errado reaparece em varios dias do mes. Quem revisa
        corrige a ficha uma vez, entao a fila precisa listar item, nao ocorrencia.
        """
        grupos: dict[tuple[str, str], tuple[Issue, list[str]]] = {}
        for issue in self.issues:
            chave = (issue.code, issue.item_name)
            if chave not in grupos:
                grupos[chave] = (issue, [])
            if issue.service_date:
                grupos[chave][1].append(issue.service_date)
        ordenado = sorted(grupos.values(), key=lambda par: (not par[0].blocking, par[0].item_name))
        return [(issue, len(datas), sorted(datas)) for issue, datas in ordenado]

    def summary(self) -> str:
        agrupados = self.grouped_issues()
        bloqueios = sum(1 for issue, *_ in agrupados if issue.blocking)
        linhas = [
            f"Lote: {self.batch}",
            f"Itens publicados: {len(self.published)}",
            f"Ocorrencias bloqueadas: {len(self.blocked)} (em {bloqueios} ficha(s) tecnica(s))",
            f"Avisos: {len(agrupados) - bloqueios}",
        ]
        if agrupados:
            linhas.append("")
            linhas.append("Para revisao do nutricionista:")
            for issue, vezes, datas in agrupados:
                marca = "BLOQUEIO" if issue.blocking else "aviso   "
                nome = issue.item_name or issue.category or "(cardapio)"
                repete = f" — {vezes}x no periodo (1o em {datas[0]})" if vezes > 1 else ""
                linhas.append(f"  [{marca}] {nome}{repete}")
                linhas.append(f"             {issue.detail}")
        return "\n".join(linhas)


def decidir(
    rows: list[list[str]],
    unit: str = "",
    meal: str = "almoco",
    batch: str = "",
    month: int | None = None,
    year: int | None = None,
) -> ImportResult:
    """Le as linhas e separa o que publica do que fica para revisao.

    Nada e gravado. E a mesma decisao que a importacao toma — `catalog` chama
    esta funcao e depois escreve o que ela aprovou.
    """
    result = ImportResult(batch)
    entries, issues = parse_menu_rows(rows, unit=unit, meal=meal, month=month, year=year)
    result.issues.extend(issues)

    for entry in entries:
        entry_issues = validate_item(
            entry.item,
            unit=entry.unit,
            service_date=entry.service_date,
            category=entry.category,
        )
        result.issues.extend(entry_issues)
        if any(issue.blocking for issue in entry_issues):
            result.blocked.append(entry)
            continue
        result.published.append(entry)

    return result
