"""Entidades do cardapio com informacao nutricional."""

from dataclasses import dataclass, field

# Categorias observadas nos cardapios da Apetit. "PRATO PRINCIPAL 2" e
# "SALADA 3" nao sao categorias diferentes: sao a mesma categoria em outro slot.
CATEGORIES = (
    "PRATO PRINCIPAL",
    "OPCAO AO PP",
    "GUARNICAO",
    "SALADA",
    "SOBREMESA",
    "FRUTA",
    "ARROZ",
    "FEIJAO",
    "BEBIDA",
    "LANCHE",
    "ACOMPANHAMENTO",
)

# Campos que existem no export da operacao mas nao podem chegar ao app do
# funcionario: custo e per capita sao dado comercial da Apetit.
DISCARDED_COLUMNS = ("CUSTO", "PER CAPITA", "PERCAPITA", "PRECO", "VALOR")


@dataclass(frozen=True)
class Nutrition:
    """Valores por porcao servida, nao por 100 g."""

    kcal: float | None = None
    cho_g: float | None = None
    lip_g: float | None = None
    ptn_g: float | None = None

    @property
    def complete(self) -> bool:
        return None not in (self.kcal, self.cho_g, self.lip_g, self.ptn_g)

    @property
    def macros_g(self) -> float | None:
        if None in (self.cho_g, self.lip_g, self.ptn_g):
            return None
        return self.cho_g + self.lip_g + self.ptn_g

    def atwater_kcal(self) -> float | None:
        """Energia recalculada a partir dos macros (4/9/4 kcal por grama)."""
        if None in (self.cho_g, self.lip_g, self.ptn_g):
            return None
        return self.cho_g * 4 + self.lip_g * 9 + self.ptn_g * 4


@dataclass
class MenuItem:
    """Um item da ficha tecnica."""

    code: str
    name: str
    nutrition: Nutrition = field(default_factory=Nutrition)
    portion_g: float | None = None
    # Alergenico ausente do dicionario e "nao declarado", nunca "nao contem".
    allergens: dict[str, str] = field(default_factory=dict)


@dataclass
class MenuEntry:
    """Um item publicado num dia, refeicao e categoria."""

    unit: str
    service_date: str
    meal: str
    category: str
    slot: int
    item: MenuItem


@dataclass
class Issue:
    """Problema encontrado na importacao.

    severity "block" impede a publicacao do item; "warn" publica com ressalva.
    """

    severity: str
    code: str
    detail: str
    item_name: str = ""
    unit: str = ""
    service_date: str = ""
    category: str = ""

    @property
    def blocking(self) -> bool:
        return self.severity == "block"
