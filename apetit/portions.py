"""Quanto pegar de cada coisa, em medida de refeitorio.

O funcionario nao serve gramas: ele serve concha, colher e pegador. Entao a
sugestao sai assim — "2 colheres de arroz, 1 concha de feijao" — em vez de
"200 g de arroz".

O que torna isso possivel: no cardapio da operacao, **cada linha ja e uma porcao
padrao**. ARROZ PARBOILIZADO 138 kcal e uma colher de servir; FEIJAO PRETO
29 kcal e uma concha. Entao a conta e multiplicar, nao converter.

A sugestao e sugestao: quem prescreve quantidade individual e o nutricionista
responsavel. Por isso ela descreve o caminho para o alvo que a propria pessoa
escolheu, com teto por categoria para nao virar recomendacao absurda.
"""

from dataclasses import dataclass

from .humanize import clean_dish_name

# Categoria -> (singular, plural, maximo de porcoes sugeridas)
MEASURES: dict[str, tuple[str, str, int]] = {
    "PRATO PRINCIPAL": ("porcao", "porcoes", 2),
    "OPCAO AO PP": ("porcao", "porcoes", 1),
    "GUARNICAO": ("colher", "colheres", 2),
    "ARROZ": ("colher", "colheres", 3),
    "FEIJAO": ("concha", "conchas", 2),
    "SALADA": ("pegador", "pegadores", 3),
    "FRUTA": ("unidade", "unidades", 1),
    "SOBREMESA": ("porcao", "porcoes", 1),
    "BEBIDA": ("copo", "copos", 1),
    "LANCHE": ("unidade", "unidades", 1),
    "ACOMPANHAMENTO": ("porcao", "porcoes", 1),
}

MEDIDA_PADRAO = ("porcao", "porcoes", 1)

# Categorias que compoem o prato base. Sobremesa e bebida ficam de fora da
# sugestao: nao e papel do app empurrar sobremesa para bater caloria.
BASE_CATEGORIES = ("PRATO PRINCIPAL", "GUARNICAO", "ARROZ", "FEIJAO", "SALADA")

# Salada quase nao move o total e faz bem: entra como "a vontade".
FREE_CATEGORIES = {"SALADA"}

# Quanto vale fechar 1 g de proteina, em "kcal equivalente" de prioridade.
# Alto de proposito: a proteina e a parte que a pessoa costuma nao alcancar.
PESO_PROTEINA = 15.0

# Ate quanto a sugestao pode passar do alvo de energia antes de parar.
TOLERANCIA_ESTOURO = 1.10


@dataclass
class Portion:
    category: str
    name: str
    quantity: int
    kcal: float
    ptn_g: float

    @property
    def total_kcal(self) -> float:
        return self.kcal * self.quantity

    @property
    def total_ptn(self) -> float:
        return self.ptn_g * self.quantity

    def label(self) -> str:
        singular, plural, _ = MEASURES.get(self.category, MEDIDA_PADRAO)
        nome = clean_dish_name(self.name)
        if self.category in FREE_CATEGORIES:
            return f"{nome} a vontade"
        medida = singular if self.quantity == 1 else plural
        return f"{self.quantity} {medida} de {nome}"


@dataclass
class PlateSuggestion:
    portions: list[Portion]
    target_kcal: int
    target_ptn: int
    notes: list[str]

    @property
    def kcal(self) -> float:
        return sum(p.total_kcal for p in self.portions)

    @property
    def ptn_g(self) -> float:
        return sum(p.total_ptn for p in self.portions)

    def lines(self) -> list[str]:
        return [p.label() for p in self.portions]

    def summary(self) -> str:
        if not self.portions:
            return "Nao consigo sugerir quantidades para o cardapio de hoje."
        falta = self.target_ptn - self.ptn_g
        if falta <= 0:
            fecho = f"Isso fecha {self.kcal:.0f} kcal e {self.ptn_g:.0f} g de proteina — sua meta do dia."
        else:
            fecho = (
                f"Isso da {self.kcal:.0f} kcal e {self.ptn_g:.0f} g de proteina. "
                f"Ainda ficam {falta:.0f} g de proteina abaixo do seu alvo com o que tem hoje."
            )
        return fecho


def _best_in_category(category: str, items: list[dict]) -> dict | None:
    """Escolhe o item que representa a categoria na sugestao.

    No prato principal vale o mais proteico, que e a funcao dele no prato. Nas
    outras, vale a primeira opcao do cardapio, que e a principal da operacao.
    """
    com_macro = [i for i in items if i.get("kcal") is not None and i.get("ptn_g") is not None]
    if not com_macro:
        return None
    if category in ("PRATO PRINCIPAL", "OPCAO AO PP"):
        return max(com_macro, key=lambda i: i["ptn_g"])
    return com_macro[0]


def suggest_plate(menu_items: list[dict], target_kcal: int, target_ptn: int) -> PlateSuggestion:
    """Monta a sugestao de quanto pegar de cada categoria.

    `menu_items` sao os itens publicados do dia, com category, name, kcal e
    ptn_g. Itens sem macro ficam de fora e viram aviso: sugerir quantidade sem
    saber o que tem dentro seria chute.
    """
    notes: list[str] = []
    por_categoria: dict[str, list[dict]] = {}
    sem_macro = 0
    for item in menu_items:
        if item.get("kcal") is None or item.get("ptn_g") is None:
            sem_macro += 1
            continue
        por_categoria.setdefault(item["category"], []).append(item)

    if sem_macro:
        notes.append(f"{sem_macro} item(ns) do cardapio ainda estao sem informacao nutricional.")

    escolhidos: dict[str, dict] = {}
    for categoria in BASE_CATEGORIES:
        item = _best_in_category(categoria, por_categoria.get(categoria, []))
        if item:
            escolhidos[categoria] = item

    if not escolhidos:
        notes.append("Sem itens com informacao nutricional suficiente para sugerir quantidade.")
        return PlateSuggestion([], target_kcal, target_ptn, notes)

    # Comeca com uma porcao de cada categoria disponivel.
    quantidades = {categoria: 1 for categoria in escolhidos}

    def totais() -> tuple[float, float]:
        kcal = sum(escolhidos[c]["kcal"] * q for c, q in quantidades.items())
        ptn = sum(escolhidos[c]["ptn_g"] * q for c, q in quantidades.items())
        return kcal, ptn

    # Acrescenta porcoes enquanto isso aproximar do alvo. A cada rodada escolhe
    # a adicao que mais reduz o que falta, com a proteina pesando mais.
    while True:
        kcal_atual, ptn_atual = totais()
        falta_kcal = max(0.0, target_kcal - kcal_atual)
        falta_ptn = max(0.0, target_ptn - ptn_atual)
        if falta_kcal <= 0 and falta_ptn <= 0:
            break

        custo_atual = falta_kcal + falta_ptn * PESO_PROTEINA
        melhor_categoria = None
        melhor_custo = custo_atual

        for categoria, item in escolhidos.items():
            if categoria in FREE_CATEGORIES:
                continue
            _, _, maximo = MEASURES.get(categoria, MEDIDA_PADRAO)
            if quantidades[categoria] >= maximo:
                continue
            novo_kcal = kcal_atual + item["kcal"]
            if novo_kcal > target_kcal * TOLERANCIA_ESTOURO:
                continue
            novo_custo = (
                max(0.0, target_kcal - novo_kcal)
                + max(0.0, target_ptn - (ptn_atual + item["ptn_g"])) * PESO_PROTEINA
            )
            if novo_custo < melhor_custo:
                melhor_custo = novo_custo
                melhor_categoria = categoria

        if melhor_categoria is None:
            break
        quantidades[melhor_categoria] += 1

    portions = [
        Portion(
            category=categoria,
            name=escolhidos[categoria]["name"],
            quantity=quantidade,
            kcal=escolhidos[categoria]["kcal"],
            ptn_g=escolhidos[categoria]["ptn_g"],
        )
        for categoria, quantidade in quantidades.items()
    ]
    ordem = {c: i for i, c in enumerate(BASE_CATEGORIES)}
    portions.sort(key=lambda p: ordem.get(p.category, 99))

    return PlateSuggestion(portions, target_kcal, target_ptn, notes)
