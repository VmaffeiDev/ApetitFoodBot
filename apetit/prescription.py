"""A ficha de controle nutricional que o proprio funcionario traz.

Ate aqui o alvo do app saia de um objetivo generico ("manter o equilibrio") com
numero ilustrativo. Quem tem acompanhamento de nutricionista tem numero de
verdade — e o app passa a seguir esse numero em vez do palpite dele.

Isso inverte quem prescreve, e para melhor: o nutricionista prescreveu, o app
so aplica no cardapio do dia. O app continua sem prescrever nada (Lei
8.234/1991); ele vira ferramenta de **seguir** uma prescricao existente.

Tres regras seguram o resto:

1. **Nada e aplicado sem a pessoa confirmar.** Leitura automatica de documento
   erra, e aqui errar significa dar orientacao errada a partir de um documento
   clinico. O app mostra o que entendeu, linha por linha, e so vale o que for
   confirmado.
2. **Total do dia nunca vira alvo de refeicao.** E o erro que machuca: uma
   ficha de 1.800 kcal/dia aplicada ao almoco mandaria a pessoa comer o dia
   inteiro num prato so. Valor diario fica marcado como diario e exige uma
   segunda pergunta.
Duas formas de ficha chegam, e elas nao se parecem:

- **ficha de meta**: "VET: 1800 kcal/dia, PTN: 90 g". Da um alvo numerico.
- **plano alimentar**: "Almoco — arroz 4 colheres de sopa, feijao 2 conchas,
  frango 150 g, salada a vontade". Nao traz caloria nenhuma.

A segunda e a que apareceu no mundo real, e para este app ela e melhor: ja fala
em concha e colher, que e a lingua da tela "quanto pegar". `extract_meal_plan`
le esse formato; `extract_prescription` le o outro.

3. **O arquivo nao fica guardado.** Ficha de nutricionista costuma carregar
   peso, diagnostico e historico — dado de saude bem mais sensivel que "objetivo:
   emagrecer". O app extrai os numeros, confirma, guarda os numeros e descarta
   o documento.
"""

import re
import unicodedata
from dataclasses import dataclass, field

# Fracao do dia que o almoco costuma representar. Existe para ser **oferecida**
# e confirmada, nunca aplicada em silencio: repartir a prescricao do dia entre
# refeicoes e decisao de nutricionista, e o app nao vai fingir que e dele.
FRACAO_ALMOCO_SUGERIDA = 0.35

# Limites de sanidade para uma refeicao de refeitorio. Fora disso, quase certo
# que o numero lido e do dia, ou veio errado.
ALMOCO_KCAL_MAX = 1500
ALMOCO_PTN_MAX = 150

# Piso, tao necessario quanto o teto. Numa ficha real, "Italac Whey Protein -
# 1 Caixinha" (nome de produto numa lista de substituicao do lanche) foi lido
# como "proteina = 1", e 1 g viraria a meta do almoco. Numero absurdamente
# baixo nao e meta: e ruido que casou com um rotulo.
ALMOCO_KCAL_MIN = 100
ALMOCO_PTN_MIN = 5


def _normaliza(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFD", texto or "").encode("ascii", "ignore").decode()
    return re.sub(r"[ \t]+", " ", sem_acento).lower()


def parse_number(bruto: str) -> float | None:
    """Numero no formato brasileiro: "1.800" e mil e oitocentos, "1,8" e um virgula oito."""
    try:
        return float(bruto.replace(".", "").replace(",", ".") if "," in bruto else bruto)
    except (ValueError, AttributeError):
        return None


# Cada campo traz os rotulos que aparecem em ficha brasileira. "vet" e valor
# energetico total; "get", gasto energetico total.
ROTULOS = {
    "kcal": r"(?:vet|get|valor energetico(?: total)?|energia|calorias?|kcal|kilocalorias?)",
    # `prot` so vale como palavra inteira: sem isso casa dentro de "Protein",
    # que aparece em nome de produto, e de "protocolo".
    "ptn_g": r"(?:ptn|proteinas?|prot\b\.?)",
    "cho_g": r"(?:cho|carboidratos?|carbo\.?|gliciacids?)",
    "lip_g": r"(?:lip|lipideos?|lipidios?|gorduras?)",
}

# Marcas de que o numero e do dia inteiro, nao daquela refeicao.
POR_DIA = re.compile(r"(?:/\s*dia|por dia|ao dia|diari[oa]|total diari[oa]|/d\b|24\s*h)")
# Marcas de que o numero e do almoco especificamente.
DO_ALMOCO = re.compile(r"(?:almoco|refeicao principal|jantar/almoco)")

# Onde a ficha lista o que nao pode.
INICIO_PROIBIDO = re.compile(
    r"(?:evitar|nao consumir|nao ingerir|restric(?:ao|oes)|proibid[oa]s?|suspender|excluir|abolir)\s*:?",
)
INICIO_LIVRE = re.compile(r"(?:permitid[oa]s?|liberad[oa]s?|recomendad[oa]s?|consumir|preferir)\s*:?")
SEPARADOR = re.compile(r"[,;/•]|\se\s|\bou\b|\n")

# Linhas que nao carregam prescricao e so gerariam ruido na leitura.
IGNORAR = re.compile(r"(?:^\s*$|assinatura|carimbo|pagina \d|www\.|@|cpf|rg\b)")


@dataclass
class Campo:
    """Um numero lido da ficha, com o trecho de onde saiu.

    O trecho viaja junto porque a pessoa precisa conferir a leitura contra o
    proprio documento — "1800 kcal" sozinho nao diz se veio da linha certa.
    """

    valor: float
    trecho: str
    por_dia: bool = False

    @property
    def precisa_repartir(self) -> bool:
        return self.por_dia


@dataclass
class Leitura:
    """O que o app entendeu da ficha. Nada disso vale antes de confirmado."""

    campos: dict[str, Campo] = field(default_factory=dict)
    proibidos: list[str] = field(default_factory=list)
    recomendados: list[str] = field(default_factory=list)
    profissional: str = ""
    linhas_lidas: int = 0

    @property
    def vazia(self) -> bool:
        return not (self.campos or self.proibidos or self.recomendados)

    @property
    def algum_valor_diario(self) -> bool:
        return any(c.por_dia for c in self.campos.values())


@dataclass
class Prescription:
    """A ficha ja confirmada pela pessoa. E isto que vira alvo no app."""

    kcal: float | None = None
    ptn_g: float | None = None
    cho_g: float | None = None
    lip_g: float | None = None
    proibidos: list[str] = field(default_factory=list)
    recomendados: list[str] = field(default_factory=list)
    profissional: str = ""
    fonte: str = ""
    escopo: str = "almoco"  # "almoco" ou "dia"; guardado para nao se perder
    fracao_almoco: float | None = None
    confirmada_em: str = ""

    @property
    def tem_alvo(self) -> bool:
        return self.kcal is not None or self.ptn_g is not None

    def alvo_almoco(self) -> dict:
        """Alvo da refeicao, ja repartido quando a ficha era do dia inteiro."""
        fator = self.fracao_almoco if self.escopo == "dia" and self.fracao_almoco else 1.0
        alvo = {}
        if self.kcal is not None:
            alvo["kcal"] = round(self.kcal * fator)
        if self.ptn_g is not None:
            alvo["ptn"] = round(self.ptn_g * fator)
        return alvo

    def implausivel(self) -> str:
        """Motivo pelo qual o alvo da refeicao nao faz sentido, se for o caso.

        Existe porque o jeito mais provavel de errar e um total do dia entrar
        como se fosse do almoco. Melhor o app dizer que o numero parece do dia
        do que sugerir um prato de 1.800 kcal.
        """
        alvo = self.alvo_almoco()
        if alvo.get("kcal", 0) > ALMOCO_KCAL_MAX:
            return f"{alvo['kcal']:.0f} kcal num almoco so — esse numero parece ser do dia inteiro."
        if alvo.get("ptn", 0) > ALMOCO_PTN_MAX:
            return f"{alvo['ptn']:.0f} g de proteina num almoco so — esse numero parece ser do dia inteiro."
        if "kcal" in alvo and alvo["kcal"] < ALMOCO_KCAL_MIN:
            return f"{alvo['kcal']:.0f} kcal e pouco demais para um almoco — devo ter lido o numero errado."
        if "ptn" in alvo and alvo["ptn"] < ALMOCO_PTN_MIN:
            return f"{alvo['ptn']:.0f} g de proteina e pouco demais para um almoco — devo ter lido o numero errado."
        return ""


def _procura_campo(linha_norm: str, linha_original: str, padrao: str) -> Campo | None:
    """Acha o numero do campo na linha, nas duas ordens que a ficha usa.

    Ficha escreve dos dois jeitos: "PTN: 90 g" poe o rotulo antes, "Almoco: 600
    kcal" poe depois. Procurar so uma das ordens perde metade dos documentos.

    Entre rotulo e numero cabe pouca coisa ("PTN almoco: 35 g"), e de proposito:
    janela larga faria o rotulo de uma linha capturar o numero de outro assunto.
    """
    candidatos = (
        # rotulo antes: "PTN: 90", "Proteinas almoco: 35"
        rf"{padrao}\s*(?:total)?[^\d\n]{{0,20}}?(\d+(?:[.,]\d+)?)",
        # rotulo depois: "600 kcal", "90 g de proteina"
        rf"(\d+(?:[.,]\d+)?)\s*(?:g|gramas?)?\s*(?:de\s+)?{padrao}",
    )
    for regex in candidatos:
        busca = re.search(regex, linha_norm)
        if not busca:
            continue
        valor = parse_number(busca.group(1))
        if valor is None or valor <= 0:
            continue
        return Campo(
            valor=valor,
            trecho=linha_original.strip()[:120],
            por_dia=bool(POR_DIA.search(linha_norm)) and not DO_ALMOCO.search(linha_norm),
        )
    return None


def _lista_apos(linha_norm: str, linha_original: str, marcador: re.Pattern) -> list[str]:
    busca = marcador.search(linha_norm)
    if not busca:
        return []
    resto = linha_original[busca.end():]
    itens = []
    for pedaco in SEPARADOR.split(resto):
        termo = pedaco.strip(" .:-–—\t").lower()
        if 2 < len(termo) <= 40 and not termo.isdigit():
            itens.append(termo)
    return itens


def extract_prescription(texto: str) -> Leitura:
    """Le a ficha e devolve o que entendeu, para a pessoa confirmar.

    Nunca decide nada: o retorno e uma proposta de leitura. Campo que nao
    aparecer fica de fora em vez de receber valor padrao — a mesma regra que
    vale para macro de cardapio vale para prescricao.
    """
    leitura = Leitura()
    if not texto or not texto.strip():
        return leitura

    for linha_original in texto.splitlines():
        linha_norm = _normaliza(linha_original)
        if not linha_norm.strip() or IGNORAR.search(linha_norm):
            continue
        leitura.linhas_lidas += 1

        for campo, padrao in ROTULOS.items():
            if campo in leitura.campos:
                continue
            achado = _procura_campo(linha_norm, linha_original, padrao)
            if achado:
                leitura.campos[campo] = achado

        for termo in _lista_apos(linha_norm, linha_original, INICIO_PROIBIDO):
            if termo not in leitura.proibidos:
                leitura.proibidos.append(termo)
        for termo in _lista_apos(linha_norm, linha_original, INICIO_LIVRE):
            if termo not in leitura.recomendados and termo not in leitura.proibidos:
                leitura.recomendados.append(termo)

        if not leitura.profissional:
            crn = re.search(r"crn[\s\-/]*\d*[\s\-/]*(\d{3,})", linha_norm)
            if crn:
                leitura.profissional = linha_original.strip()[:80]

    return leitura


# ---------------------------------------------------------------------------
# Plano alimentar: a ficha que lista alimento e medida caseira por refeicao
# ---------------------------------------------------------------------------

REFEICOES = r"(Caf[eé] da manh[ãa]|Cola[çc][ãa]o|Almo[çc]o|Lanche(?:\s*-\s*Op[çc][ãa]o\s*\d+)?|Jantar|Ceia)"

# O rodape de assinatura digital se repete em toda pagina e cairia no meio dos
# alimentos.
RODAPE = re.compile(r"Digitally signed by.*?NUTRI[ÇC][ÃA]O", re.S)

# No PDF os itens vem colados: "Arroz branco cozido4 Colher(es) de sopa cheia(s)
# (100g)Feijao cozido2 Colher servir cheia(70g)". Nao ha separador — o que
# marca o fim de um item e o peso entre parenteses, o "a vontade" ou um volume
# solto. Entao a leitura acha essas ancoras e corta entre elas.
ANCORA = re.compile(
    r"\((\d+(?:[.,]\d+)?)\s*(g|ml)\)"      # (100g) / (250ml)
    r"|(À\s*vontade|A\s*vontade)"           # salada a vontade
    r"|(\d+(?:[.,]\d+)?)\s*ml",             # 150ml solto
    re.I,
)
QUANTIDADE = re.compile(r"^(?P<nome>.*?)\s*(?P<qtd>\d+(?:[.,]\d+)?)\s*(?P<medida>.*)$", re.S)


@dataclass
class ItemPlano:
    """Um alimento do plano, na medida que o nutricionista escreveu."""

    nome: str
    quantidade: float | None = None
    medida: str = ""
    peso: str = ""

    @property
    def a_vontade(self) -> bool:
        return self.medida == "a vontade"

    def descreve(self) -> str:
        if self.a_vontade:
            return f"{self.nome} — a vontade"
        partes = []
        if self.quantidade is not None:
            numero = f"{self.quantidade:g}"
            partes.append(f"{numero} {self.medida}".strip())
        elif self.medida:
            partes.append(self.medida)
        if self.peso:
            partes.append(f"({self.peso})")
        return f"{self.nome} — {' '.join(partes)}" if partes else self.nome


def extract_meal_plan(texto: str) -> dict[str, list[ItemPlano]]:
    """Le um plano alimentar e devolve os alimentos de cada refeicao.

    So o que o nutricionista pediu entra: as listas de substituicao ("no lugar
    do arroz, pure ou batata") ficam de fora, porque elas multiplicariam o
    almoco por cinco e o app nao saberia qual foi para o prato.
    """
    if not texto:
        return {}
    texto = RODAPE.sub("\n", texto)
    cabecalhos = list(re.finditer(rf"^\s*{REFEICOES}\s*$", texto, re.M | re.I))

    plano: dict[str, list[ItemPlano]] = {}
    for i, cabecalho in enumerate(cabecalhos):
        fim = cabecalhos[i + 1].start() if i + 1 < len(cabecalhos) else len(texto)
        corpo = texto[cabecalho.end():fim]
        corte = corpo.find("•")          # dali para baixo sao as substituicoes
        itens = _itens_do_bloco(corpo[:corte] if corte > 0 else corpo)
        if itens:
            plano[cabecalho.group(1).strip()] = itens
    return plano


def _itens_do_bloco(bloco: str) -> list[ItemPlano]:
    bloco = re.sub(r"\s*\n\s*", " ", bloco).strip()
    itens: list[ItemPlano] = []
    pos = 0
    for ancora in ANCORA.finditer(bloco):
        trecho = bloco[pos:ancora.start()].strip()
        pos = ancora.end()
        if not trecho:
            continue

        partido = QUANTIDADE.match(trecho)
        if partido and partido.group("nome").strip():
            nome = partido.group("nome").strip()
            quantidade = parse_number(partido.group("qtd"))
            medida = partido.group("medida").strip()
        else:
            nome, quantidade, medida = trecho, None, ""

        if ancora.group(3):              # "a vontade" nao tem quantidade
            medida, quantidade = "a vontade", None
            peso = ""
        elif ancora.group(1):
            peso = f"{ancora.group(1)} {ancora.group(2).lower()}"
        else:
            peso = f"{ancora.group(4)} ml"

        nome = nome.strip(" -•\t")
        if nome:
            itens.append(ItemPlano(nome=nome, quantidade=quantidade, medida=medida, peso=peso))
    return itens


def lunch_from_plan(plano: dict[str, list[ItemPlano]]) -> list[ItemPlano]:
    """O almoco do plano, que e a refeicao que este app acompanha."""
    for nome, itens in plano.items():
        if re.search(r"almo[çc]o", nome, re.I):
            return itens
    return []


def save_plan_lunch(conn, telegram_id: int, itens: list[ItemPlano]) -> None:
    """Guarda o almoco prescrito. Como sempre, o documento nao entra."""
    conn.execute("DELETE FROM employee_plan_item WHERE telegram_id = ?", (telegram_id,))
    for posicao, item in enumerate(itens):
        conn.execute(
            "INSERT INTO employee_plan_item (telegram_id, posicao, nome, quantidade, medida, peso) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (telegram_id, posicao, item.nome, item.quantidade, item.medida, item.peso),
        )
    conn.commit()


def load_plan_lunch(conn, telegram_id: int) -> list[ItemPlano]:
    linhas = conn.execute(
        "SELECT nome, quantidade, medida, peso FROM employee_plan_item "
        "WHERE telegram_id = ? ORDER BY posicao",
        (telegram_id,),
    ).fetchall()
    return [
        ItemPlano(nome=l["nome"], quantidade=l["quantidade"], medida=l["medida"], peso=l["peso"])
        for l in linhas
    ]


def delete_plan_lunch(conn, telegram_id: int) -> None:
    conn.execute("DELETE FROM employee_plan_item WHERE telegram_id = ?", (telegram_id,))
    conn.commit()


def save_prescription(conn, telegram_id: int, ficha: Prescription) -> None:
    """Guarda os numeros confirmados. O documento nao entra no banco."""
    from datetime import UTC, datetime

    agora = datetime.now(UTC).isoformat(timespec="seconds")
    conn.execute(
        """
        INSERT INTO employee_prescription (
            telegram_id, kcal, ptn_g, cho_g, lip_g, escopo, fracao_almoco,
            profissional, fonte, confirmada_em, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(telegram_id) DO UPDATE SET
            kcal = excluded.kcal, ptn_g = excluded.ptn_g,
            cho_g = excluded.cho_g, lip_g = excluded.lip_g,
            escopo = excluded.escopo, fracao_almoco = excluded.fracao_almoco,
            profissional = excluded.profissional, fonte = excluded.fonte,
            confirmada_em = excluded.confirmada_em, updated_at = excluded.updated_at
        """,
        (
            telegram_id, ficha.kcal, ficha.ptn_g, ficha.cho_g, ficha.lip_g,
            ficha.escopo, ficha.fracao_almoco, ficha.profissional, ficha.fonte,
            ficha.confirmada_em or agora, agora,
        ),
    )
    conn.execute("DELETE FROM employee_prescription_term WHERE telegram_id = ?", (telegram_id,))
    for termo in dict.fromkeys(ficha.proibidos):
        conn.execute(
            "INSERT INTO employee_prescription_term (telegram_id, term, kind) VALUES (?, ?, 'proibido')",
            (telegram_id, termo),
        )
    for termo in dict.fromkeys(ficha.recomendados):
        conn.execute(
            "INSERT INTO employee_prescription_term (telegram_id, term, kind) VALUES (?, ?, 'recomendado')",
            (telegram_id, termo),
        )
    conn.commit()


def load_prescription(conn, telegram_id: int) -> Prescription | None:
    linha = conn.execute(
        "SELECT * FROM employee_prescription WHERE telegram_id = ?", (telegram_id,)
    ).fetchone()
    if not linha:
        return None
    termos = conn.execute(
        "SELECT term, kind FROM employee_prescription_term WHERE telegram_id = ? ORDER BY term",
        (telegram_id,),
    ).fetchall()
    return Prescription(
        kcal=linha["kcal"],
        ptn_g=linha["ptn_g"],
        cho_g=linha["cho_g"],
        lip_g=linha["lip_g"],
        proibidos=[t["term"] for t in termos if t["kind"] == "proibido"],
        recomendados=[t["term"] for t in termos if t["kind"] == "recomendado"],
        profissional=linha["profissional"],
        fonte=linha["fonte"],
        escopo=linha["escopo"],
        fracao_almoco=linha["fracao_almoco"],
        confirmada_em=linha["confirmada_em"],
    )


def delete_prescription(conn, telegram_id: int) -> None:
    conn.execute("DELETE FROM employee_prescription_term WHERE telegram_id = ?", (telegram_id,))
    conn.execute("DELETE FROM employee_prescription WHERE telegram_id = ?", (telegram_id,))
    conn.commit()


def read_pdf(conteudo: bytes) -> str:
    """Texto de um PDF. PDF escaneado volta vazio, e isso e dito a pessoa.

    Nao ha OCR de proposito: reconhecer texto de foto erra bastante, e um erro
    aqui vira orientacao errada a partir de documento clinico. Melhor o app
    admitir que nao leu e pedir os numeros do que chutar.
    """
    try:
        from pypdf import PdfReader
    except ImportError as erro:  # pragma: no cover - depende do ambiente
        raise RuntimeError("Ler PDF precisa do pypdf. Instale com: pip install pypdf") from erro

    import io

    leitor = PdfReader(io.BytesIO(conteudo))
    partes = []
    for pagina in leitor.pages:
        try:
            partes.append(pagina.extract_text() or "")
        except Exception:  # pagina corrompida nao derruba o resto
            continue
    return "\n".join(partes).strip()
