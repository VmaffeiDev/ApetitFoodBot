# Apetit

Acompanhamento nutricional para funcionarios atendidos pela **Apetit Servicos de
Alimentacao**.

A empresa serve o refeitorio; o funcionario acompanha o que come. **Nao ha venda,
preco, carrinho nem pedido.** O cardapio da operacao e importado, validado e
publicado, e o funcionario monta o prato e registra o consumo.

> Este projeto comecou como um bot de Telegram, e o nome do repositorio guarda
> isso. O Telegram foi removido: hoje sao um app web, um servidor pequeno e o
> pacote `apetit/`, onde moram todas as regras.

## As tres pecas

| Peca | O que e |
|---|---|
| `apetit/` | Todas as regras: alergenico, porcao, ficha, pontos, relatorios, identidade. Nao depende de nenhuma camada de entrega — ha teste que verifica isso. |
| `apetit/api.py` | Um servidor Tornado com seis rotas. Publica cardapio, serve o dia, e cuida da entrada por e-mail. |
| `demo/` | O app que o funcionario abre: um PWA, instalavel, que funciona sem servidor. |

## O que o funcionario faz

- cadastra unidade da Apetit, empresa, setor, objetivo e restricoes alimentares
- ve o cardapio do dia **ja conferido contra as proprias alergias**
- monta o prato e ve kcal e macros somarem contra o alvo do objetivo
- registra o almoco e acumula pontos
- guarda pratos favoritos
- guarda a ficha do proprio nutricionista, so no aparelho dele
- **avalia o refeitorio** — comida, atendimento e o que faltou — sem se
  identificar para a empresa
- consulta e apaga os proprios dados quando quiser

## As rotas

| Rota | O que faz | Quem pode |
|---|---|---|
| `GET /api/dia` | O cardapio do dia da unidade | qualquer um |
| `POST /api/cardapio` | Publica o cardapio da semana (CSV) | quem tem o token |
| `POST /api/entrar` | Manda o codigo de seis digitos para o e-mail | qualquer um |
| `POST /api/codigo` | Confere o codigo e abre a sessao | qualquer um |
| `GET /api/eu` | De quem e esta sessao | quem tem a sessao |
| `POST /api/sair` | Encerra a sessao deste aparelho | quem tem a sessao |
| `GET /api/saude` | Se o servidor esta de pe | qualquer um |

`GET /api/dia` ser publico nao e descuido: o cardapio e igual para todo mundo da
unidade, e exigir login para ler o cardapio faria o servidor saber quem quis ver
o que — sem ganhar nada em troca. O que e de pessoa (alergia, objetivo,
historico) fica no aparelho.

## Rodar localmente

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env

python -m apetit.api                  # o servidor, na porta 8000
python -m http.server 8080 -d demo    # o app, noutra janela
```

O servidor cria as tabelas ao subir, entao o primeiro `GET /api/dia` responde
**404 "ainda nao ha cardapio publicado"**, e nao um erro.

O app abre e funciona **sem o servidor**: ele le `demo/dados.json`, publicado ao
lado da pagina. Para apontar para um servidor, preencha a `<meta name="apetit-api">`
do `demo/index.html` — ou, so para testar, rode no console do navegador:

```js
localStorage.setItem("apetit-api", "https://seu-servidor")
```

Quem entra no app e definido pela lista fechada do piloto:

```bash
python scripts/autorizar.py --unidade SM --arquivo equipe.txt
python scripts/autorizar.py --listar
python scripts/autorizar.py --tirar alguem@empresa.com.br
```

Testes:

```bash
python -m unittest discover -s tests        # 401 testes
node scripts/conferir_fluxos.cjs            # 67 cenarios em jsdom
python scripts/conferir_vereditos.py        # 8 alergias x 4 objetivos, num Chromium
python scripts/conferir_fonte.py            # servidor x fotografia, e a data do cardapio
python scripts/conferir_cardapio.py         # a pagina de conferencia x o importador
```

## Decisoes de interface

O app e de acompanhamento **pessoal**. Quem usa quer comer melhor, nao ler
macronutriente. Tres decisoes seguem disso:

**Montar o prato segue a ordem da fila do refeitorio** — prato principal,
guarnicao, arroz, feijao, salada, sobremesa — uma categoria por vez, com "Passo
3 de 6". O app acompanha a bandeja em vez de mostrar uma lista unica com tudo.

**Numero vem com leitura em palavras.** "Prato leve para o seu objetivo",
"Faltam 12 g de proteina", "Sem salada nem fruta — vale somar uma". O numero fica,
mas em segundo plano. A leitura descreve onde o prato esta; nunca manda comer
menos, porque quem prescreve e o nutricionista.

**O cadastro oferece, nao pergunta codigo.** Refeitorio, empresa e setor viram
botoes com o que ja existe no banco, com saida para digitar quando for novo. O
funcionario nao sabe que a unidade dele se chama `SM` no sistema da operacao.

## Quanto pegar, em concha e colher

O funcionario nao serve gramas: ele serve concha, colher e pegador. Entao a
sugestao sai na medida do refeitorio.

```
Quanto pegar hoje
segunda-feira, 1 de setembro · objetivo: Manter o equilibrio

• 1 porcao de File de frango grelhado
• 2 colheres de Macarrao alho e oleo
• 2 colheres de Arroz parboilizado
• 1 concha de Feijao preto
• Mix de alface a vontade

Isso da 711 kcal e 44 g de proteina. E a sua meta do dia.
```

O que torna a conta simples: no cardapio da operacao **cada linha ja e uma porcao
padrao**. ARROZ PARBOILIZADO com 138 kcal e uma colher de servir; FEIJAO PRETO
com 29 kcal e uma concha. Entao a sugestao multiplica, nao converte.

Cinco regras seguram o resultado:

- **prato bloqueado nao entra** — sugerir quantidade de algo que a pessoa nao
  pode comer seria pior que nao sugerir nada
- **teto por categoria** — no maximo 3 colheres de arroz, 2 conchas de feijao;
  sem isso a conta viraria recomendacao absurda
- **o prato principal so se repete enquanto falta proteina** — depois da meta
  fechada ele e apenas a opcao mais calorica da bandeja, e repetir viraria
  "pegue duas porcoes de carne" so para fechar energia. Energia que falta se
  fecha com arroz e guarnicao
- **sobremesa e bebida ficam de fora** — nao e papel do app empurrar pudim para
  fechar caloria
- **quando o cardapio nao alcanca o alvo, ele diz** em vez de inventar porcao

O fecho cobra proteina e energia separadamente: bater a proteina e ficar 200
kcal abaixo do alvo nao e "a meta do dia", e anunciar assim faria o app declarar
cumprido o que nao cumpriu.

Salada entra como "a vontade": quase nao move o total e faz bem.

E sugestao, nao prescricao. Quem define quantidade individual e o nutricionista
responsavel.

## Historico do funcionario

Da tela de sugestao sai um botao so: **Vou pegar isso**. Um toque grava a refeicao
do dia com as quantidades sugeridas. Quem prefere ajustar usa **Montar meu prato**
e marca item por item. Nos dois casos, registrar de novo no mesmo dia substitui o
registro anterior — nao empilha.

O `/meu_dia` devolve o prato do jeito que foi pego, com o total do dia e os dias
anteriores:

```
Meu dia

Prato equilibrado para o seu objetivo.
Tem salada no prato.

• Carne assada ao molho — 2 porcoes
• Arroz parboilizado — 2 colheres
• Feijao preto — 1 concha
• Mix de alface — 1 pegador

703 kcal · 33 g de proteina

Seu historico
sexta-feira, 29 de agosto — 612 kcal · 28 g ptn
• ...
```

### O historico e fotografia, nao ponteiro

A tabela `consumption` guarda **nome, categoria, quantidade e macros congelados no
momento do registro**, e nao uma referencia viva para a ficha tecnica.

O motivo e concreto: a operacao reimporta cardapio e corrige ficha tecnica o tempo
todo. Se o historico apontasse para `menu_item`, corrigir o macro de um prato em
novembro mudaria retroativamente o que a pessoa comeu em setembro. Histórico que
muda sozinho nao serve para acompanhar nada.

Consequencias praticas:

- corrigir a ficha tecnica afeta o **proximo** registro, nunca os anteriores
- item que sai do cardapio nao apaga o registro de quem comeu; o que fica nulo e o
  macro, e o total do dia se declara incompleto em vez de fingir zero
- quantidade e coluna: "2 conchas" e uma linha com `quantity = 2`, entao o total do
  dia multiplica em vez de contar item repetido

Bancos criados antes dessa mudanca sao migrados no `init_schema`: as colunas novas
sao criadas e preenchidas com o que a ficha tecnica diz no momento da migracao — a
melhor aproximacao disponivel para um registro feito antes de existir fotografia.
A partir dali o valor para de mudar sozinho.

## Publicar o cardapio da semana

Toda semana quem tem o cardapio manda o `.csv` para o servidor, do jeito que a
operacao exporta:

```bash
curl -X POST "https://servidor/api/cardapio?unidade=SM&refeicao=almoco&mes=8&ano=2025" \
     -H "Authorization: Bearer $APETIT_PUBLICAR_TOKEN" \
     -H "Content-Type: text/csv" \
     --data-binary @Cardapio_17_a_2108.csv
```

A resposta diz o que aconteceu: quantos itens foram publicados, em que dias, o
que ficou bloqueado por macro inconsistente e o que vai sem informacao
nutricional.

Pelo terminal, com o banco na mao, o mesmo caminho:

```bash
python scripts/import_cardapio.py Cardapio_17_a_2108.xlsx --unidade SM --mes 8 --ano 2025
```

**A planilha de planejamento traz so o numero do dia**, sem mes nem ano — por
isso `--mes` e `--ano` sao obrigatorios com ela. Chutar o mes publicaria a
semana no dia errado, e e o tipo de erro que ninguem ve ate alguem almocar.

Tres protecoes:

- **Sem refeitorio nao publica.** O funcionario ve o cardapio filtrado pela
  unidade dele; publicar sem unidade e publicar para ninguem, e some sem erro
  nenhum — a pior forma de falhar.
- **Fim de semana vira aviso.** Se as datas montadas caem no sabado ou domingo,
  o mes ou o ano quase certamente esta errado: os dias vem da planilha, entao o
  que nao bate e o mes/ano. O proprio calendario denuncia o palpite. Fica como
  aviso, porque existe refeitorio que serve no fim de semana.
- **Reenviar o mesmo periodo substitui.** E como a operacao corrige uma semana
  ja publicada: manda o arquivo corrigido de novo.

**Publicar e a acao de maior privilegio do sistema** — quem publica define o que
quinze pessoas leem sobre alergenico. A rota exige `APETIT_PUBLICAR_TOKEN`, e
sem ele no ambiente ela recusa tudo: melhor uma porta ausente que destrancada.

### Conferir antes de mandar (`demo/cardapio.html`)

Quem na operacao exporta a planilha nem sempre quer descobrir um erro **depois**
de publicar. A pagina de conferencia fica no mesmo endereco do app:

> **https://vmaffeidev.github.io/ApetitFoodBot/cardapio.html**

A pessoa arrasta o `.csv` e ve, antes de qualquer coisa ir para o ar: as datas
que o importador montou, os itens e categorias de cada dia, o que ficou
bloqueado por macro inconsistente e o que vai sem informacao nutricional.
Depois ela manda o mesmo arquivo para o servidor, que publica.

**A pagina nao publica** — ela e so leitura, e o arquivo nao sai do computador
de quem abriu. Publicar exige o token, e continua sendo acao de quem tem ele:
quem publica define o que as pessoas leem sobre alergenico.

**E ela nao reimplementa nada.** `apetit/csv_import.py` tem 524 linhas de
leitura em tres layouts; uma segunda implementacao em JavaScript discordaria da
primeira um dia, e a divergencia apareceria como "seu arquivo esta bom" seguido
de uma publicacao errada. Entao a pagina roda o **proprio `apetit/`** no
navegador, via [Pyodide](https://pyodide.org): os mesmos `read_rows` e
`preflight.decidir` que a importacao chama antes de gravar. O Pyodide so e
baixado quando alguem solta um arquivo — quem abre a pagina para ler as
instrucoes nao paga por isso.

Foi o que motivou `apetit/preflight.py`: decidir o que publicar e uma coisa,
gravar e outra, e elas viviam juntas em `import_menu_rows`. A pagina nao tem
banco — o Pyodide nem traz `sqlite3` —, e as opcoes eram carregar um SQLite para
joga-lo fora ou reescrever a decisao do lado do navegador. Separar foi melhor
que as duas: um so lugar decide, e quem grava so grava.

No workflow do Pages, os modulos sao copiados para o lado da pagina na hora de
publicar. Commitar copias dentro de `demo/` criaria a segunda versao das regras
que o desenho inteiro existe para evitar.

Para conferir que a pagina continua concordando com o importador:

```bash
npm install --prefix scripts/pyodide pyodide@0.26.4
python scripts/conferir_cardapio.py
```

Ele abre a pagina num Chromium, solta cada cardapio de exemplo nela e compara o
que aparece na tela com o que `import_menu_rows` devolve em Python para o mesmo
arquivo. O Pyodide vem do disco, para o teste nao depender de rede.

## Importacao pelo terminal

O importador aceita os tres layouts observados, com separador `;` ou `,` e
decimal com virgula:

| Layout | Como e | Traz macro? |
|---|---|---|
| **largo** | uma linha por dia, cinco colunas por categoria (NOME, KCAL, CHO, LIP, PTN) | sim |
| **longo** | uma linha por item, colunas nomeadas | sim |
| **planejamento** | uma linha por dia, uma coluna por categoria, tudo grudado na celula | **nao** |

```powershell
python scripts/import_cardapio.py cardapio.csv --unidade SM --refeicao almoco
python scripts/import_cardapio.py Cardapio_17_a_2108.xlsx --unidade SM --mes 8 --ano 2025
python scripts/import_cardapio.py --pendencias
```

Categoria com numero, como `PRATO PRINCIPAL 2`, e a mesma categoria em outro slot.

### A planilha de planejamento

E o formato que a operacao manda toda semana. Cada celula junta quatro coisas:

```
BIFE ACEBOLADO (80g) - C51 - 3.11
└─ nome         └ porcao └ ficha └ custo per capita
```

O importador separa os quatro. **O custo morre na leitura** — e dado comercial
da Apetit e nao existe caminho por onde ele chegue ao app do funcionario; ha
teste garantindo isso. A porcao entre parenteses vira `portion_g`, e o codigo
da ficha entra no identificador do item, que e o gancho para casar esta
planilha com a que tiver os macros.

Tres detalhes que o formato exige:

- **O nome pode conter o proprio separador** (`KIT - QUIMICO - A. YOSHII`).
  Por isso o que sobra depois de tirar as pontas conhecidas e remontado
  inteiro, em vez de o parser chutar qual pedaco e o nome.
- **Colunas de insumo sao descartadas** — descartaveis, produto de limpeza,
  kit de tempero e de galeteiro nao sao comida que alguem se serve.
- **A coluna do dia traz so o numero** (`17`), sem mes nem ano. Sem `--mes` e
  `--ano` a importacao **para**, em vez de chutar: um mes errado publicaria o
  cardapio da semana no dia errado.

Importar planejamento por cima de uma ficha tecnica ja carregada **nao apaga os
macros** — todo campo nutricional entra por `COALESCE`, entao valor novo
nao-nulo vence e ausencia preserva o que estava la.

### Ausencia de macro nunca vira zero

A mesma invariante que vale para alergenico vale para valor nutricional: o que
o app nao sabe, ele nao afirma.

Somar item sem macro como zero produzia isto, para quem pegou carne, arroz,
feijao e salada de um cardapio sem ficha tecnica:

```
Prato leve para o seu objetivo de hoje.
Faltam 30 g de proteina para o seu alvo.
0 kcal · 0 g proteina
```

Nada ali era verdade. Hoje a leitura do prato recebe quantos itens sao
legiveis, e:

- **nenhum legivel** — o app nao classifica o prato: "ainda nao consigo ler
  este prato"
- **parte legivel** — o total vira piso explicito ("no minimo 280 kcal") e o
  prato tambem nao e classificado, porque classificar soma incompleta e
  afirmar o que nao se sabe
- **tudo legivel** — leitura normal

Item so conta como legivel tendo **kcal e proteina**: e o par que o app usa para
dizer qualquer coisa, e so kcal deixava a proteina entrar como zero silencioso.
A composicao do prato ("sem salada nem fruta") continua sendo lida sem macro
nenhum, porque para isso a categoria basta.

### O que essa planilha sozinha nao resolve

Ela nao tem valor nutricional nenhum. Com so ela no banco, a semana de 17 a 21
importa 80 itens em 5 dias e o funcionario ve o cardapio inteiro, organizado na
ordem da fila — mas:

- **`/quanto_pegar` nao responde.** Sem kcal e proteina nao ha o que sugerir, e
  o app diz isso em vez de inventar porcao.
- **`/meu_dia` registra o prato, com total zerado.** A fotografia guarda o que
  foi pego; os macros ficam nulos e o dia se declara incompleto.
- **Todo prato aparece como ⚠️** para quem tem alergia declarada, porque sem
  ficha tecnica nao da para afirmar que e seguro.

Falta a ficha tecnica com os macros. Como o codigo dela (`C51`,
`06.03.01.258`) ja vem nesta planilha, os dois arquivos casam pelo codigo assim
que o segundo existir.

Colunas de **custo e per capita sao descartadas**: sao dado comercial da operacao
e nao podem chegar ao app do funcionario.

### Validacao antes de publicar

| Regra | Acao |
|---|---|
| Energia declarada x Atwater (4/9/4) divergindo mais de 25% **e** de 30 kcal | bloqueia |
| Macros somando mais que a porcao | bloqueia |
| Valor negativo | bloqueia |
| Macro incompleto | publica marcado como sem informacao |

O limite duplo na primeira regra e o que faz ela funcionar: so o relativo barraria
salada de 4 kcal por 2 kcal de arredondamento.

Item bloqueado **nao chega ao cardapio** — vai para a fila de revisao com o motivo,
agrupada por ficha tecnica, ja que a mesma ficha errada reaparece em varios dias
do mes e a correcao e uma so.

## Alergenicos

A lista segue os alergenicos de declaracao obrigatoria da RDC 26/2015 da ANVISA.
A conferencia tem **tres estados, nao dois**:

| Ficha tecnica diz | Resposta ao funcionario |
|---|---|
| Contem | Bloqueio, com o alergenico nomeado |
| Pode conter / tracos | Atencao — confirmar no balcao |
| **Nada** | Atencao — o app nao afirma que e seguro |
| Todos os alergenicos da pessoa como nao contem | Liberado |

**Falta de informacao nunca vira liberacao.** Deduzir alergenico do nome do prato
e o erro que machuca: "STROGONOFF DE CARNE" nao avisa que leva creme de leite,
"FILE DE FRANGO A MILANESA" nao avisa que leva ovo e trigo.

### O funcionario escreve, o app reconhece

No cadastro a pessoa escreve do jeito dela — "alergia a frutos do mar", "nao
posso leite nem ovo", "sou celiaco" — e o app traduz para os codigos da lista.
Antes de salvar, ele mostra o que entendeu para a pessoa confirmar.

| A pessoa escreve | O app entende |
|---|---|
| alergia a frutos do mar | Crustaceos + Peixes |
| intolerante a lactose | Leite e derivados |
| sou celiaco | Gluten |
| nao posso leite nem ovo | Leite + Ovos |

Duas regras seguram a honestidade:

**Nada e adivinhado por semelhanca.** So casa com sinonimo conhecido. Errar para
o lado do "reconheci" e pior que pedir para a pessoa confirmar.

**O que nao for reconhecido nao e descartado.** "Alergia a legumes" nao existe
como campo em ficha tecnica nenhuma. O termo fica guardado, aparece no cadastro
marcado como *nao conferido pelo app*, e — o ponto importante — **enquanto a
pessoa tiver um termo desses, nenhum prato aparece como liberado.** Mostrar visto
verde a quem tem restricao que o app nao checa e pior que nao mostrar nada.

Quem preferir marcar numa lista em vez de escrever tem essa saida no proprio passo.

### O nome do prato tambem conta

O cardapio ja diz em voz alta o que o prato e: "FEIJAO PRETO" tem feijao,
"SALADA DE CAMARAO" tem camarao. Entao quem escreve "nao posso feijao" tem o
prato bloqueado sem precisar de ficha tecnica nenhuma.

Isso vale **numa direcao so**: o nome prova presenca, nunca ausencia.
"Strogonoff de carne" nao ter "leite" no nome nao prova que nao leva creme de
leite. Por isso o casamento por nome bloqueia, mas nunca libera.

Casa variacao de palavra — "feijao" pega "feijoada", "carne" pega "carnes" — e
na duvida casa: um bloqueio a mais a pessoa percebe e contorna; um bloqueio a
menos ela come.

### Alergia ou so prefiro evitar

Quando o termo nao e um alergenico conhecido, o app pergunta o quanto ser
rigoroso, porque as duas coisas pedem tratamento diferente:

| A pessoa responde | O que o app faz |
|---|---|
| **E alergia** | Bloqueia pelo nome **e** avisa em todo prato que nao consegue confirmar |
| **So prefiro evitar** | Bloqueia so quando aparece no nome; fica quieto no resto |

A diferenca e grande na pratica. Num cardapio real de 13 itens, "nao posso
feijao" como alergia gera **12 avisos**; como preferencia, gera **1 bloqueio e
nenhum aviso**. Tratar preferencia com rigor de alergia enche a tela de alerta
ate a pessoa parar de ler o que importa.

### Os dois lados do alerta

O aviso so funciona cruzando duas informacoes, que vem de fontes diferentes:

| Lado | Quem informa |
|---|---|
| **A que a pessoa e alergica** | O proprio funcionario, no cadastro |
| **O que cada prato contem** | So a cozinha sabe |

O funcionario declarar a alergia dele nao resolve o segundo lado: ele sabe que
nao pode leite, mas nao tem como saber se o strogonoff de hoje leva creme de
leite. Por isso, sem o dado do prato, a resposta e "nao consigo confirmar".

### Como declarar o que cada prato contem

**1. No proprio CSV do cardapio.** Colunas como `alerg_leite` ou `contem_gluten`
sao importadas automaticamente, aceitando `sim`/`nao`/`pode conter`/`tracos`.
Celula vazia continua como nao declarado.

**2. Planilha do nutricionista**, enquanto o export nao tiver o campo:

```powershell
python scripts/alergenicos.py --exportar alergenicos.csv
# nutricionista preenche no Excel: sim / nao / pode conter
python scripts/alergenicos.py --importar alergenicos.csv
python scripts/alergenicos.py --cobertura
```

O trabalho e finito e se paga: num mes real, **110 fichas cobriram 312
ocorrencias** do cardapio. A planilha sai ordenada pelos pratos que mais
aparecem, entao declarar arroz, feijao e refresco ja resolve boa parte do
cardapio.

**3. Prato a prato**, por `set_item_allergens` — o caminho que a pagina de
conferencia e os scripts usam.

**4. Pela planilha de receitas da operacao** — o caminho que cobre mais de uma vez:

```powershell
python scripts/receitas.py --importar Receitas-Cardapio-Nutri.xlsx --so-conferir
python scripts/receitas.py --importar Receitas-Cardapio-Nutri.xlsx
python scripts/receitas.py --revisar Receitas.xlsx --prato strogonoff_de_carne
```

A planilha de receitas traz **a lista de ingredientes de cada prato** — 4.952
receitas, 27.298 linhas de ingrediente. E dela que sai a declaracao que a ficha
tecnica nunca trouxe. O `apetit/allergens.py` abre dizendo qual era o problema:
"STROGONOFF DE CARNE nao avisa que leva creme de leite". A receita avisa — ela
lista `COMPOSTO LACTEO`.

Tres regras seguram a deducao (detalhe em `apetit/recipes.py`):

- **Ingrediente prova presenca, nunca ausencia.** O script **nunca grava
  `nao_contem`**: a receita pode omitir o molho pronto, o ingrediente composto
  pode esconder alergenico na formula e existe contaminacao cruzada na cozinha,
  que nenhuma lista enxerga. Liberar um prato continua sendo decisao da
  nutricionista.
- **Casamento por palavra inteira, com excecao explicita.** "COUVE MANTEIGA"
  nao tem manteiga, "PAO DE QUEIJO" e de polvilho e nao tem gluten, "PAO SIRIO"
  nao tem siri, "LEITE DE COCO" nao e leite e "NOZ MOSCADA" nao e noz. Cada uma
  dessas e um ⚠️ falso que nao aparece — e ⚠️ falso ensina a pessoa a ignorar o
  ⚠️ verdadeiro.
- **Prato com variantes que discordam fica sem declaracao.** "CARNE ASSADA AO
  MOLHO" existe com champignon e ao molho madeira, com ingredientes diferentes.
  Onde as variantes concordam, a resposta e a mesma qualquer que seja a que foi
  para a panela e da para declarar; onde discordam, nao.

Oleo de soja aparece em 1.811 das 27.298 linhas. A RDC 26/2015 dispensa os
oleos vegetais totalmente refinados, e marcar "contem soja" em todo prato
apagaria o aviso para quem tem alergia de verdade. O app marca `pode_conter` e
deixa visivel; `OLEO_VEGETAL_REFINADO`, no topo de `apetit/recipes.py`, e a
linha unica para a nutricionista mudar se o caso for outro.

O resultado entra com `source = "lista de ingredientes"`: e deducao para a
nutricionista revisar, nao declaracao da cozinha.

Acompanhe com `/cobertura` ou `--cobertura`.

## Progresso

E acompanhamento pessoal: **nao existe ranking e ninguem compara o funcionario
com colega nenhum.** O que aparece e a propria sequencia ("voce registrou 3 de 5
dias desta semana") e as proprias conquistas.

Pontuam **constancia** (registrar), **composicao** (incluir salada ou fruta),
**variedade** na semana e **meta de proteina**.

Nenhuma regra premia deficit calorico ou perda de peso, e **nao ha ranking entre
colegas**. Num app corporativo, premiar comer menos sob o olhar do empregador
empurra para uma relacao ruim com comida. Cada regra carrega o campo `basis` e ha
teste garantindo que nenhuma se apoie em deficit ou peso.

Os alvos de kcal e proteina por objetivo sao **ilustrativos**. Quem define faixa
individual e o nutricionista responsavel: o app informa e acompanha, nao prescreve.

## A ficha do nutricionista

Quem faz acompanhamento tem numero de verdade, nao o alvo ilustrativo de um
objetivo generico. O funcionario manda a ficha em PDF (ou digita os numeros, que
e o caso comum: ficha de papel), confere o que o app entendeu e confirma — so
entao o app passa a seguir **a ficha dela** no lugar do objetivo do cadastro.

Isso nao faz o app prescrever nada (Lei 8.234/1991). Ao contrario: quem
prescreveu foi o nutricionista, e o app virou a ferramenta de **seguir** a
prescricao no cardapio do dia.

Tres regras seguram o resto:

**1. Nada vale sem a pessoa confirmar.** A tela mostra cada numero com o trecho
da ficha de onde ele saiu — `li de: "VET: 1800 kcal/dia"`. Sem o trecho, a pessoa
confirmaria as cegas uma leitura automatica de documento clinico. Numero lido
errado sai com um toque.

**2. Total do dia nunca vira alvo de refeicao.** E o erro que machuca: uma ficha
de 1.800 kcal/dia aplicada ao almoco mandaria a pessoa comer o dia inteiro num
prato so. O app **sempre** pergunta se os numeros sao do dia ou do almoco, mesmo
quando a ficha parece dizer, e reparte usando a proporcao usual do almoco (35%)
deixando claro que repartir o dia e decisao do nutricionista. Se ainda assim
sobrar um alvo implausivel para uma refeicao (acima de 1.500 kcal ou 150 g de
proteina), o app **nao usa** esse numero e avisa.

**3. O documento nao fica guardado.** Ficha de nutricionista costuma trazer peso,
diagnostico e historico — dado de saude bem mais sensivel que "objetivo:
emagrecer". O app extrai os numeros, a pessoa confirma, e o documento e
descartado. Ha teste que vasculha o banco inteiro atras do conteudo da ficha.

O que a ficha manda evitar entra pelo mesmo caminho do "prefiro evitar": bloqueia
quando o termo aparece no nome do prato, sem virar alerta de alergenico — e
orientacao profissional, nao risco de reacao alergica.

Quando a ficha traz so metade do alvo (calorias mas nao proteina), a outra metade
continua vindo do objetivo, e a tela **diz qual e qual**. Sem proteina nao ha como
pontuar o dia, e apresentar palpite do app como numero do profissional seria pior
que o palpite.

PDF escaneado ou foto volta vazio de proposito: **nao ha OCR**. Reconhecer letra de
imagem erra, e errar aqui vira orientacao errada a partir do documento clinico de
alguem. O app admite que nao leu e oferece digitar os numeros.

## Avisos de progresso

Ate aqui o app so respondia. Uma mensagem que ele manda sozinho e outra coisa:
ela chega no celular da pessoa, num app da empresa, sobre o que ela come. Isso
pode ser util ou pode ser pressao, e a diferenca esta em poucas regras.

**Fala do proprio progresso, nunca do prato.** "Voce registrou 4 de 5 dias" e
sobre constancia. "Voce passou das calorias" seria o empregador comentando o
almoco de alguem, e nao existe aqui — ha teste varrendo o texto atras de
`caloria`, `peso` e `emagrec`.

**Nunca compara com colega.** Vale a mesma regra do resto do app: sem ranking,
sem media do setor.

**Quem nao esta usando para de receber.** Duas semanas sem nenhum registro e o
resumo cala. Sem isso o aviso vira cobranca semanal de um app corporativo para
quem ja decidiu nao usar — o jeito mais rapido de a pessoa passar a ignorar
tudo, inclusive o alerta de alergenico. A primeira semana em branco convida
("sem pressa"); a segunda nao chega.

Dois avisos:

| Aviso | Quando | Padrao |
|---|---|---|
| Resumo da semana | sexta, 16h (Brasilia) | ligado |
| Lembrete do almoco | dia util, 11h, so se ainda nao registrou e ha cardapio | **desligado** |

O resumo ja vem ligado porque e o proprio progresso da pessoa, uma vez por
semana, desligavel em dois toques. O lembrete nao: ele chega todo dia util, e
ninguem pediu para um app da empresa lembrar da hora do almoco. Toda mensagem
termina com como parar de recebe-la.

O horario e fixo no fuso de Brasilia (UTC-3, sem horario de verao desde 2019),
nao em UTC: um lembrete de almoco precisa cair na hora do almoco de quem recebe.

Nada e enviado duas vezes — o envio fica marcado por periodo, entao reiniciar o
servidor no meio do dia nao reenvia. E a marca so e gravada **depois** que a
entrega e aceita: marcar antes faria uma queda de rede virar um resumo que a
pessoa nunca recebe e que nunca seria reenviado.

> **Hoje nao sai aviso nenhum.** As regras acima (`apetit/nudges.py`) continuam
> testadas e valendo, mas quem entregava era o Telegram. Num app web, entregar
> exige permissao de notificacao no aparelho e um servidor que empurre, e nenhum
> dos dois existe ainda. A tela de avisos guarda a escolha da pessoa e **diz que
> nada esta saindo** — prometer um toque no ombro que nunca vem seria pior que
> nao ter a tela.

## Avaliacao do refeitorio

Depois de registrar a refeicao, o app pergunta como foi. Sao tres toques —
quem avalia esta na fila, de bandeja na mao:

```
A comida estava boa?     😋 Boa  · 😐 Regular · 😞 Ruim
E o atendimento?         😋 Bom  · 😐 Regular · 😞 Ruim
Faltou alguma coisa?     👍 Nao  · 👎 Sim → o que faltou
```

Comentario escrito e opcional. O convite so aparece se a pessoa ainda nao
avaliou naquele dia: pedir de novo o que ela ja respondeu e o caminho mais
rapido para ela parar de responder.

A escala e de tres niveis de proposito. Cinco estrelas viram indecisao na fila,
e o que a operacao precisa saber e se da para servir na segunda-feira, nao a
diferenca entre 3,4 e 3,6.

### A parte delicada: isto e o unico dado que a empresa le

Todo o resto do app e privado do funcionario — o que ele come, seu objetivo,
suas restricoes, a empresa nunca ve. Aqui o fluxo se inverte, e isso cria um
risco que precisa ser resolvido no desenho, nao na politica de uso:

> quem reclama do refeitorio esta reclamando do servico contratado pela propria
> empresa onde trabalha. Se a avaliacao chegasse identificada, o funcionario que
> disse "faltou comida" ficaria exposto a retaliacao — e o proximo aprenderia a
> mentir na avaliacao.

Quatro decisoes saem dai, todas com teste:

- **A linha de avaliacao nao tem coluna de empresa nem de setor.** Ela e sobre o
  refeitorio. Guardar o setor criaria exatamente o cruzamento que reidentifica
  ("a unica pessoa da manutencao que almocou terca"). Nao existe a coluna, entao
  nao ha como consultar por ali depois.
- **Nenhuma leitura para a gestao seleciona `pessoa_id`.** Ele existe na tabela
  so para tres coisas: uma avaliacao por dia, a pessoa poder rever e trocar a
  propria, e a exclusao total quando ela pedir.
- **Abaixo de 5 avaliacoes no periodo, o recorte e suprimido** — media de tres
  pessoas nao e media, e opiniao identificavel.
- **Comentario escrito so sai com volume**, e em ordem alfabetica, nunca
  cronologica: a ordem de chegada cruzada com quem almocou no dia tambem aponta
  para uma pessoa.

A primeira tela diz isso ao funcionario antes da primeira pergunta. Quem nao
sabe que esta protegido responde como se nao estivesse.

Nao existe nota para funcionario do balcao por nome. Avaliacao individual de
trabalhador por trabalhador nao e problema de app, e viraria outra fonte de
retaliacao — do outro lado do balcao.

### O historico de atendimento

`/atendimento` lista os refeitorios do ultimo mes, **o pior primeiro**: o
relatorio existe para achar o refeitorio com problema, nao para exibir o que vai
bem.

```
Refeitorio Fabrica II: 24 avaliacoes · comida boa 62% · atendimento bom 46% · faltou algo em 42%
Refeitorio Administrativo: 24 avaliacoes · comida boa 92% · atendimento bom 92%
Refeitorio Central: 24 avaliacoes · comida boa 96% · atendimento bom 83%
```

`/atendimento <refeitorio>` abre o detalhe, com a serie semanal:

```
segunda-feira, 1 de setembro: 100% comida boa (12 avaliacoes)
segunda-feira, 8 de setembro:  25% comida boa (12 avaliacoes)

o que faltou: Acabou antes de eu chegar — 10x
```

A serie semanal existe porque a media do mes esconde a semana em que o
refeitorio caiu — nos numeros acima, a media do periodo diz 62% e some com a
queda de 100% para 25%.

O percentual ignora quem pulou aquela pergunta: quem nao deu nota de atendimento
nao conta como atendimento ruim.

## Privacidade

O app guarda dado de saude de funcionario dentro de uma relacao de emprego, o que
exige cuidado alem do aviso de consentimento:

- a empresa **nunca ve dado individual** — nem consumo, nem objetivo, nem restricao
- a adesao sai **agregada por setor**, e nada mais
- a avaliacao sai como media do refeitorio, suprimida abaixo de 5 avaliacoes, e
  nao guarda empresa nem setor de quem respondeu
- recorte com menos de **5 pessoas** e suprimido, porque setor pequeno mais dado
  alimentar reidentifica alguem sem precisar do nome
- **Apagar tudo** apaga cadastro, restricoes, consumo, favoritos, pontos e ficha
- a ficha do nutricionista entra como **numeros**: o documento nao e guardado

## O app (`demo/`)

A pasta `demo/` e o aplicativo: um **PWA** que abre no navegador do celular,
instala na tela inicial e roda em tela cheia, sem barra de navegador.

```bash
python scripts/demo_dados.py demo/dados.json Receitas-Cardapio-Nutri.xlsx
python scripts/demo_icone.py demo/
python -m http.server -d demo         # http://localhost:8000
```

**Todas as telas sao escritas em `demo/index.html`.** Ate a remocao do Telegram,
setenta delas eram transcricoes capturadas do Telegram, desenhadas por um
interpretador de texto — fiel ao bot e errado como aplicativo: a tela de
favoritos nao sabia o que a pessoa tinha guardado.

O conteudo que essas telas mostram continua saindo do Python, em
`demo/dados.json`: o veredito de alergenico, a sugestao de porcao, os pontos e
os relatorios sao calculados por `apetit/`, nunca reescritos em JavaScript.

O `sw.js` existe por dois motivos. Sem service worker o navegador nao oferece
instalar na tela inicial. E o refeitorio costuma ter sinal ruim: um "app" que
abre em branco no subsolo da fabrica nao demonstra nada, entao o shell fica em
cache e abre sempre. Os dados das telas vao por rede primeiro, para uma
republicacao chegar sem reinstalar.

**Precisa de HTTPS.** Service worker e instalacao so funcionam em origem segura
— `localhost` no desenvolvimento, e um host com TLS para as 15 pessoas. Servido
de dentro de um iframe nao instala: a instalacao sai do documento de topo.

Esse endereco e o **GitHub Pages**, publicado por `.github/workflows/pages.yml`:

> **https://vmaffeidev.github.io/ApetitFoodBot/**

O Pages serve numa subpasta (`/ApetitFoodBot/`), e por isso o `demo/` nao tem um
unico caminho absoluto: `start_url` e `scope` do manifest sao `"."`, o registro
do service worker e `"sw.js"` relativo, e o `SHELL` do cache usa `"."`. Servido
na raiz ou em subpasta, o escopo sai certo e o navegador oferece instalar nos
dois casos.

Sobe **so de `main`**, e so quando `demo/` muda. O endereco e o que as 15 pessoas
vao abrir: ele segue o que ja passou por revisao, nao o galho da vez. Para
publicar fora de hora, `workflow_dispatch` na aba Actions.

O workflow **nao gera** as telas nem os dados: sobe o que esta commitado em
`demo/`. Gerar no CI exigiria a planilha da empresa, que nao entra no
repositorio, e publicaria um app montado a partir de dado que ninguem conferiu.

Para ligar na primeira vez, uma vez so: **Settings -> Pages -> Source: GitHub
Actions**.

Os icones sao desenhados em codigo (`scripts/demo_icone.py`) em vez de virarem
binario solto: da para mudar a cor numa linha, e o `maskable` sai com margem
folgada porque o Android recorta o icone na forma do lancador.

### O cadastro, na primeira vez e depois

Quem abre o app pela primeira vez cai numa tela de boas-vindas e se cadastra do
zero: nome, refeitório, empresa e setor, objetivo, alergias e o termo. Depois
disso o cadastro fica guardado e a pessoa
edita quando quiser, no **Perfil → Editar meu cadastro**, ou apaga tudo ali
mesmo.

A tela de boas-vindas também oferece **"Só olhar, com o exemplo da Mariana"**.
O link do piloto é compartilhado, e quem só quer conferir o app precisa
conseguir, sem inventar um cadastro. O exemplo é dito com esse nome para
ninguém confundir dado de exemplo com dado seu.

**O cadastro muda o cardápio.** Os vereditos de alergênico não vêm prontos do
Python: eles são calculados no navegador, contra a lista de quem está usando o
app. Sem isso, quem se cadastrasse com outra alergia veria as cores da Mariana.
Isso põe a regra dos três estados em dois lugares — `apetit/allergens.py` e o
JavaScript de `demo/index.html` —, que é exatamente o jeito de os dois
discordarem um dia sem ninguém ver. A trava é `dados.conformidade`: o Python
calcula o veredito de cada prato para várias combinações de alergia, e

```bash
python scripts/conferir_vereditos.py
```

roda o app num navegador de verdade, se cadastra com cada combinação e falha se
um único prato divergir. A comparação é feita pelo que chega na tela
(`data-veredito` no botão do prato), e não pela função por dentro: o que importa
é o aviso que a pessoa viu.

Quem não declarou alergia **não** vê o cardápio inteiro de verde. `liberado`
afirma que alguém conferiu o prato para ela, e ninguém conferiu; o estado fica
`sem_restricao`, sem cor de segurança, e a tela diz por quê.

### A sugestao de porcoes segue quem se cadastrou

Um relato de teste achou o pior defeito que este app podia ter: alguem com
alergia a gluten via o macarrao marcado como **Nao pode** no cardapio, e a tela
**Quanto pegar hoje** sugeria esse mesmo macarrao. As duas telas liam fontes
diferentes — o cardapio ja calculava no navegador, a sugestao continuava
congelada no perfil de exemplo.

A saida nao foi reescrever `apetit/portions.py` em JavaScript. Aquele modulo
decide **quanto alguem come**, e uma segunda implementacao dele discordaria da
primeira um dia — a divergencia apareceria como um prato bloqueado dentro da
sugestao de quem tem alergia a ele.

O que resolveu foi olhar de que a sugestao depende: nao da lista de alergias, e
sim de **quais pratos sobram**, e do alvo do objetivo. Das 512 combinacoes de
alergia deste cardapio saem so **quatro** conjuntos de pratos bloqueados. Quatro
conjuntos x quatro objetivos = dezesseis respostas, todas calculadas pelo motor
de verdade em `scripts/demo_dados.py` e exportadas em `dados.combinacoes`. O
navegador escolhe uma; nao calcula nenhuma. Sem resposta para o cadastro em
questao, o app **nao mostra sugestao** — cair na de outra pessoa seria pior, por
vir com a cara de ter sido feita para quem esta lendo.

`scripts/conferir_vereditos.py` passou a cobrir isso: para cada combinacao de
alergia **vezes cada objetivo**, ele abre a tela de porcoes e falha se um prato
bloqueado aparecer ali, ou se o alvo nao for o do objetivo escolhido. A prova de
que a conferencia funciona e ter reintroduzido o defeito de proposito e visto o
script acusar `A SUGESTAO INCLUI PRATO BLOQUEADO: ['macarrao_alho_e_oleo']`.

### O historico e de quem se cadastrou, e o exemplo e da Mariana

Cadastro novo comeca **vazio**: zero ponto, nenhuma conquista, nenhum dia
registrado, nenhum favorito. Herdar os 25 pontos e os dois almocos da Mariana
daria a pessoa refeicoes que ela nunca fez.

O caso que mais importava era `meus_dados` — justamente a tela que promete
listar **tudo o que o app guarda sobre voce**, e que mostrava o nome, a empresa
e as alergias de outra pessoa. A tela da promessa desmentindo a promessa.

### Registro e avaliacao sao simulacao, e dizem isso

O cadastro fica guardado; o registro da refeicao e a avaliacao, nao — quem
gravaria isso e o servidor, e o app do piloto ainda roda sem um. A diferenca
aparece **na propria confirmacao**, e nao ao recarregar a pagina e ver os pontos
voltarem. Descobrir assim e a pior forma de saber: a pessoa passa a duvidar de
tudo que o app confirmou antes.

### A aba nao responde antes de o cardapio chegar

As abas sao montadas na hora, e `dados.json` chega depois. Tocar em **Cardapio**
nesse intervalo levava para a tela vazia — "nenhum cardapio publicado para hoje"
— quando o cardapio existia e so nao tinha carregado. A janela era curta no
desktop e passou de um segundo no celular modesto, depois que o arquivo cresceu
para suportar as quantidades da montagem.

Botao que responde antes da hora e pior que botao que espera: o primeiro mente
sobre o cardapio do dia. Agora as abas nascem desabilitadas e sao liberadas
quando os dados chegam.

O tempo de abertura foi medido com a CPU emulada mais lenta: **142 ms** no
desktop, **553 ms** num celular mediano (4x) e **1.115 ms** num celular fraco
(8x). Comprimido o `dados.json` sao 19 KB — o custo esta no `JSON.parse`, nao no
download.

### Onde o cadastro fica guardado

Em `localStorage`, como a foto de perfil: enquanto o app roda sem servidor,
inventar um "salvo na nuvem" seria prometer o que não existe. Com servidor, quem
guarda é o banco, com o mesmo `Employee`.

Num quadro embutido — e é assim que a prévia chega para quem só recebeu o link —
o navegador bloqueia o `localStorage` do endereço de dentro. Sem alternativa, o
cadastro morreria no último passo, e com uma mensagem sobre espaço em disco que
nem é o motivo. Então há dois lugares: o aparelho, quando dá, e a memória da
aba, quando não dá. O app funciona nos dois. O que muda é o que ele pode
prometer — e ele avisa **antes** dos seis passos, não depois.

### As cores, e por que o vermelho nao vai para todo lado

A interface usa o **vermelho `#EC003F`** e o **amarelo `#F5D94E`** já presentes
no app. O cabeçalho da home e os ícones usam vermelho; o amarelo destaca a ação
principal e o anel de pontos. O anel é decorativo: não representa porcentagem
ou meta de conclusão. Os cartões usam grafite, contornos discretos e navegação
inferior persistente, seguindo a referência visual aprovada.

Os avisos de alergênicos mantêm cores próprias: coral para bloqueio, âmbar para
confirmação e verde para liberação. Cor, ícone e texto aparecem juntos. A
reorganização visual não muda os vereditos nem as regras de pontuação.

### O "monta o prato"

Um relato de teste: selecionar um alimento no passo 1 pulava direto para o
passo 6. O defeito estava na captura das telas do Telegram, e a licao vale
alem dela: **deduplicar por conteudo perde o futuro num fluxo com estado.** A
tela do passo 2 e igual tendo ou nao marcado a carne — mas o fim do fluxo nao e.

A resolucao veio por outro caminho: a montagem virou **nativa**. Ela percorre as
categorias do cardapio atual, alimentos bloqueados ficam indisponiveis, e o
bloqueio e conferido de novo no clique de registrar — inclusive se a pessoa
trocou as restricoes depois de montar.

### Montagem e histórico do app com o mesmo registro

O montador do app percorre as categorias do cardápio atual. Alimentos bloqueados
ficam indisponíveis, e os avisos dos demais continuam visíveis na revisão.
O bloqueio é conferido novamente no clique de registrar, inclusive se a pessoa
trocou as restrições depois de montar o prato.

Os controles de quantidade usam as medidas de `apetit/portions.py`: porções,
colheres e conchas, dentro das faixas disponíveis na demonstração. Salada
mantém a indicação à vontade. A faixa do demo não é uma prescrição individual.
Os resultados das 431 combinações não vazias de alimentos e quantidades são
exportados por `demo_dados.py`, para cada objetivo, com e sem o histórico do
exemplo. Macros, medidas e pontos continuam saindo do domínio Python. A
exportação offline limita o produto das opções a 4.096 combinações; acima
disso, o cardápio precisa consultar o motor pelo servidor.

Ao confirmar a sugestão ou a montagem, o app guarda uma cópia da refeição na
memória da sessão: alimentos, medidas, totais e pontos. Home, Meu dia,
privacidade e os três períodos do progresso consultam esse mesmo registro.
Alterar o cadastro depois não modifica o almoço confirmado. A demonstração
aceita um almoço por sessão e não duplica pontos ao voltar às telas.

Depois do registro, a home destaca o almoço confirmado, os alimentos e seus
totais, e oferece **Avaliar o almoço**. As conquistas aparecem abaixo. Quando
a avaliação já foi feita, o botão passa a **Ver minha avaliação**.

No perfil próprio, nome, refeitório, empresa, setor, objetivo e restrições têm
edição direta. O app abre só o campo escolhido, oferece salvar ou cancelar e
preserva o aceite e os demais campos. Editar o cadastro não reescreve a refeição
já confirmada. O perfil de exemplo continua identificado como demonstração.

Para conferir os fluxos sem depender da renderização de um navegador:

```bash
npm ci --prefix scripts
node scripts/conferir_fluxos.cjs
```

A verificação percorre as restrições e objetivos exportados pelo Python,
montagem manual, sugestão, bloqueio no clique final, histórico, conquistas,
totais dos gráficos, quantidades nos limites suportados, edição por campo,
cancelamento e separação entre cadastro novo e exemplo.

### Prévia do visual, sem servidor

Para ver todas as telas, abra [Apetit-telas.html](preview/Apetit-telas.html).
A galeria é estática e vai do cadastro ao relatório, e permite conferir as telas
mesmo em leitores de anexos sem JavaScript.

Baixe e abra [Apetit-previa-referencia.html](preview/Apetit-previa-referencia.html) no
navegador. É uma demonstração interativa com os mesmos dados de exemplo do
`demo/`; refeições e avaliações não são persistidas. Essa versão não instala
como PWA. Fontes e leitor de PDF dependem de conexão; a navegação usa os dados
embutidos no arquivo, assim como as fotos ilustrativas.

A primeira tela já vem montada no HTML e aparece mesmo em leitores de anexos
que não executam JavaScript, como a prévia do iPhone. Nesse modo, os botões
não funcionam. Em um navegador com JavaScript, a demonstração é interativa.

O arquivo é gerado, não deve ser editado diretamente. Depois de alterar a
interface ou os dados de exemplo, atualize-o a partir da raiz do repositório:

```bash
npm ci --prefix scripts
python scripts/demo_pagina.py --documento --telas preview/Apetit-telas.html preview/Apetit-previa-referencia.html
```

Sem `--documento`, o script mantém o formato de fragmento para incorporação.

O cardápio permite filtrar categorias. A avaliação só é confirmada depois de
selecionar uma nota e tocar em **Enviar avaliação**, mantendo os códigos de
motivo de `apetit/feedback.py`. Os períodos do gráfico somam apenas registros disponíveis na
demonstração; números da referência visual não são dados do app.

As fotos foram geradas para ilustrar os pratos de exemplo; não são fotos da
operação nem comprovam ingredientes ou tamanho de porção. A origem e o mapa
do arquivo estão em [demo/assets/README.md](demo/assets/README.md).

## O relatorio do piloto

O piloto responde uma pergunta: **isso funciona na vida real do refeitorio?**
`/piloto 15 2026-09-01 2026-09-30` monta o retrato para levar a empresa.

O que entra:

- **adesao** — convidados, cadastrados, quantos chegaram a usar
- **retencao** — quantos voltaram noutro dia, quantos dias por pessoa, curva diaria
- **uso por funcao** — o que as pessoas realmente usam do app
- **objetivo declarado**, agregado e suprimido abaixo de 5 pessoas
- **qualidade do dado do cardapio** — quanto chegou sem macro e sem alergenico,
  porque isso limita o que o app consegue entregar
- **avaliacao do refeitorio**, agregada e sem autor

O que **nao** entra, e a razao importa: *o relatorio nao diz o que cada pessoa
comeu, nem qual e a meta dela, nem se ela bateu a meta.* Isso nao e cautela
excessiva — e o desenho do produto. Dado alimentar e meta nutricional sao dado de
saude (LGPD art. 5o, II) dentro de uma relacao de emprego, e o funcionario aceitou
o termo justamente porque o app promete que a empresa nao ve isso sobre ele. Um
relatorio que entregasse a meta de cada um quebraria o consentimento que tornou o
piloto possivel; num grupo de 15, qualquer recorte a mais aponta para alguem.

O proprio relatorio termina dizendo isso, para a ausencia ser lida como escolha e
nao como relatorio incompleto.

Taxa sem base volta como `—`, nao como `0%`: "0% de adesao" mentiria dizendo que
ninguem aderiu, quando o que houve foi ninguem ter sido contado.

## Seguranca dos segredos

`APETIT_SEGREDO`, `APETIT_PUBLICAR_TOKEN` e a senha de SMTP nunca entram no
codigo. Se um deles for colado em chat, issue, commit ou qualquer lugar publico,
gere outro — e trate o antigo como conhecido por estranhos.

**Este repositorio e publico por decisao do projeto.** Isso significa que qualquer
coisa commitada aqui e visivel para qualquer pessoa, para sempre — inclusive o que
for removido depois, porque o valor antigo continua no historico do Git.

Um token do Telegram ja esteve versionado no `.env.example` deste repositorio. O
valor foi removido do arquivo, mas continua acessivel no historico do Git —
entao **esse token precisa ser revogado no BotFather** (`/revoke`) mesmo com o
Telegram fora do projeto e o arquivo atual limpo. Remover num commit posterior
nao invalida credencial nenhuma; so a revogacao invalida.

## Quem pode o que

Dois privilegios, e nenhum deles mora em variavel de ambiente por acaso:

- **Publicar cardapio** exige `APETIT_PUBLICAR_TOKEN`. Quem publica define o que
  quinze pessoas leem sobre alergenico — e a acao de maior privilegio do
  sistema. Sem o token no ambiente a rota recusa tudo.
- **Entrar no app** exige estar na lista de `scripts/autorizar.py`, e receber um
  codigo de seis digitos no e-mail da empresa. A lista mora no banco, e nao no
  ambiente, porque ela muda quando alguem entra ou sai da empresa — e tirar da
  lista precisa derrubar a sessao aberta na hora, sem reiniciar nada.

A lista de quem entra e por linha de comando, e nao por tela: quem escreve nela
escolhe quem le o cardapio de alergenico de quem, e seria a porta mais valiosa
do sistema exposta na internet.

> **Um privilegio ainda sem tranca.** As quatro telas de gestao aparecem por uma
> chave no proprio aparelho e leem numeros de demonstracao de um arquivo
> publico. Enquanto o conteudo e inventado isso e aceitavel; antes de qualquer
> numero verdadeiro entrar ali, elas precisam de rota autenticada.

## Deploy

O servidor e o app vao para lugares diferentes, de proposito: o servidor precisa
de banco e segredo, e o `demo/` e um punhado de arquivo estatico que so precisa
de HTTPS.

**O app** sobe no GitHub Pages por `.github/workflows/pages.yml`, so de `main`:

> **https://vmaffeidev.github.io/ApetitFoodBot/**

Para ligar, uma vez so: Settings → Pages → Source: **GitHub Actions**.

**O servidor** roda em qualquer lugar que aceite um container:

```bash
docker build -t apetit .
docker run -d --name apetit --env-file .env -v apetit-dados:/data -p 8000:8000 apetit
```

O `-v apetit-dados:/data` nao e detalhe: **sem volume o banco some no proximo
deploy**, levando cadastro, historico e avaliacoes de todo mundo junto. O
container guarda o banco em `/data/apetit.db` e a imagem declara `/data` como
volume justamente para essa pegadinha nao passar despercebida.

| Provedor | Como | Cuidado |
|---|---|---|
| **Render** | Le o `render.yaml` | O plano gratuito nao tem disco persistente: o banco some a cada deploy |
| **Railway** | Deploy from repo, detecta o Dockerfile | Crie um **Volume** montado em `/data` |
| **Fly.io** | `fly launch` | `fly volumes create apetit_dados --size 1` e monte em `/data` |
| **VPS** | `docker run` acima | Use `--restart unless-stopped` |

`APETIT_SEGREDO`, `APETIT_PUBLICAR_TOKEN` e a senha de SMTP entram como variavel
de ambiente secreta, nunca versionadas.

Depois de subir, aponte o app para o servidor pela `<meta name="apetit-api">` do
`demo/index.html`. Sem isso o app continua funcionando com a fotografia
publicada ao lado dele — e dizendo, em toda tela que fala de comida, que aquele
nao e o cardapio de hoje.

### Banco

SQLite com volume atende o piloto de uma unidade. Para operacao com varias
unidades, migre para PostgreSQL: o gargalo do SQLite aparece em escrita
concorrente, que e exatamente o que acontece no horario do almoco.

Backup:

```powershell
python scripts/backup_db.py
```

## Estrutura

```
apetit/
  model.py       entidades do cardapio
  csv_import.py  leitura dos tres layouts de cardapio
  spreadsheet.py planilha do Excel -> as mesmas linhas do CSV
  validation.py  regras nutricionais de entrada
  catalog.py     persistencia, publicacao e conferencia
  allergens.py   alergenicos e verificacao em tres estados
  profile.py     cadastro e agregacao com n minimo
  portions.py    quanto pegar, em concha e colher
  humanize.py    o texto que o funcionario le
  tracking.py    historico congelado, favoritos e pontos
  feedback.py    avaliacao do refeitorio, agregada e sem autor
  intake.py      de que semana e o arquivo que chegou
  prescription.py ficha do nutricionista: le, confirma, guarda so os numeros
  nudges.py      quem recebe qual aviso, e quando o app cala
  recipes.py     alergenico deduzido da lista de ingredientes
  pilot.py       relatorio do piloto, sem individualizar ninguem
  preflight.py   decide o que publicar, sem tocar em disco
  payload.py     o dia da unidade, impessoal, como o app o consome
  diario.py      o dia de uma pessoa: cardapio conferido e alvo
  identidade.py  quem entra: e-mail, codigo de seis digitos, sessao
  entrega_email.py  o unico pedaco que fala com o mundo (SMTP)
  api.py         as rotas HTTP
demo/            o app: PWA instalavel, e a pagina de conferencia
scripts/         gera os dados e os icones do app; autoriza quem entra;
                 e os conferidores que provam o app contra o Python
LICENSE          todos os direitos reservados
Dockerfile       imagem, com o banco em /data
render.yaml      blueprint do Render, ja com disco persistente
```

**Nada em `apetit/` depende de uma camada de entrega.** Foi o que permitiu
apagar o Telegram sem reescrever regra nenhuma, e `tests/test_independencia.py`
verifica que continua assim — por leitura de AST e por import num interpretador
novo, porque um import tardio dentro de uma funcao passaria despercebido numa
revisao.

## Licenca

**Todos os direitos reservados.** Veja [LICENSE](LICENSE).

O repositorio e publico para consulta e avaliacao; isso nao concede licenca de
uso. Se o software for entregue a Apetit, o titular no `LICENSE` precisa mudar
para refletir isso.

## Fluxo

```mermaid
flowchart TD
    A["Operacao exporta o cardapio em CSV"] --> B["Importador valida"]
    B -->|"passou"| C["Cardapio publicado"]
    B -->|"barrado"| D["Fila de revisao do nutricionista"]
    D --> B

    E["Funcionario abre o app"] --> F{Tem cadastro?}
    F -- Nao --> G["Nome, unidade, empresa, setor, objetivo, restricoes"]
    G --> H{Aceita o termo?}
    H -- Nao --> X["Nada e salvo"]
    H -- Sim --> I["Cadastro salvo"]
    F -- Sim --> J["Menu"]
    I --> J

    C --> K["Cardapio do dia"]
    J --> K
    K --> L{Confere com as alergias}
    L -->|"contem"| M["Bloqueado, com o motivo"]
    L -->|"sem declaracao"| N["Atencao: confirmar no balcao"]
    L -->|"declarado sem"| O["Liberado"]

    N --> P["Quanto pegar / monta o prato"]
    O --> P
    P --> Q["Registra a refeicao do dia"]
    Q --> R["Fotografia: nome, quantidade e macros congelados"]
    R --> U["Meu dia: prato, total e dias anteriores"]
    R --> V["Pontos por constancia e composicao"]
    P --> S["Guarda favorito"]
    S --> T["Aviso quando o prato voltar"]
```
