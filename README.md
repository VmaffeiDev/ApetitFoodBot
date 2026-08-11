# ApetitFoodBot

MVP de bot Telegram da Apetit com fluxos inspirados no simulador HTML:

- boas-vindas com `/start`
- cadastro obrigatorio antes de pedidos
- aceite LGPD antes de salvar dados pessoais
- objetivo do cliente no cadastro: perder peso, ganhar massa, manter equilibrio, alimentacao saudavel ou praticidade
- banco SQLite com clientes e historico de pedidos
- comandos `/meus_dados` e `/excluir_dados`
- cardapio real no banco com preco, dia, ingredientes, alergenicos e tags
- cardapio do dia
- cardapio sem carne
- recomendacao inteligente baseada no cadastro e historico do cliente
- bloqueio de pedido incompatavel com restricao alimentar cadastrada
- reclamacao com escuta empatica
- feedback positivo
- alerta de restricao alimentar
- perfil nutricional demonstrativo

## Seguranca do token

Se um token foi colado em chat, issue, commit ou qualquer lugar publico, gere outro no BotFather.
Nao salve o token diretamente no codigo.

Um token de verdade ja esteve versionado no `.env.example` deste repositorio, que e publico. O valor foi removido do arquivo, mas continua acessivel no historico do Git, entao **esse token precisa ser revogado no BotFather** (`/revoke`) mesmo que o arquivo atual esteja limpo. Remover de um commit posterior nao invalida a credencial.

## Rodar localmente

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Edite o arquivo `.env` e coloque o token novo:

```env
TELEGRAM_BOT_TOKEN=seu_token_novo
APETIT_USER_NAME=Mariana
APETIT_DB_PATH=apetit.db
```

Depois inicie:

```powershell
python bot.py
```

Para rodar os testes:

```powershell
python -m unittest discover -s tests
```

No Telegram, abra o bot e envie:

```text
/start
```

O bot vai pedir nome, telefone, endereco/bairro, objetivo alimentar, restricao alimentar e aceite LGPD antes de liberar cardapio, recomendacoes ou pedidos.
Quando o cliente escolhe um prato, o bot confere os alergenicos, ingredientes e tags antes de registrar o pedido. Se houver conflito com a restricao cadastrada, o pedido nao e gravado e o bot sugere alternativas mais seguras.
As recomendacoes consideram o objetivo do cliente e o historico de pedidos.

Para refazer o cadastro, envie:

```text
/recadastrar
```

Para ver o historico de pedidos:

```text
/historico
```

Para ver o cardapio disponivel:

```text
/cardapio
```

Para consultar ou excluir os dados do cliente:

```text
/meus_dados
/excluir_dados
```

## Importacao do cardapio com informacao nutricional

O cardapio da operacao chega em CSV, com uma coluna de nome por categoria seguida
de KCAL, CHO, LIP e PTN. O importador aceita os dois layouts observados (largo,
uma linha por dia; e longo, uma linha por item), separador `;` ou `,` e decimal
com virgula.

```powershell
python scripts/import_cardapio.py cardapio.csv --unidade SM --refeicao almoco
```

Antes de publicar, cada item passa por validacao:

- **energia inconsistente** — a kcal declarada e comparada com a conta de Atwater
  (4 kcal/g de carboidrato, 9 de lipideo, 4 de proteina). So bloqueia quando a
  divergencia passa de 25% **e** de 30 kcal, para nao acusar arredondamento de
  salada de 4 kcal
- **macro maior que a porcao** — quando o export traz a gramagem
- **valor negativo**
- **macro incompleto** — publica, mas marcado como sem informacao nutricional

O que nao passa **nao e publicado**: fica na fila de revisao com o motivo, porque
num app que orienta o funcionario um macro errado vira orientacao errada.

```powershell
python scripts/import_cardapio.py --pendencias
```

A fila agrupa por ficha tecnica, nao por ocorrencia: um item errado que aparece
em doze dias do mes e uma correcao, nao doze.

Colunas de **custo e per capita sao descartadas** na importacao. Sao dado
comercial da operacao e nao podem chegar ao app do funcionario.

## Cadastro do funcionario

O vinculo e triplo: unidade da Apetit que serve, empresa onde a pessoa trabalha
e setor. O cadastro so vale com o aceite do termo e com os tres preenchidos.

Como setor pequeno mais dado alimentar reidentifica alguem sem precisar do nome,
qualquer leitura agregada passa por `aggregate_by_sector`, que **suprime recortes
com menos de 5 pessoas** em vez de mostrar a media.

## Alergenicos e alerta no momento da escolha

A lista segue os alergenicos de declaracao obrigatoria da RDC 26/2015 da ANVISA.
A conferencia tem **tres estados, nao dois**:

| Situacao | Resposta ao funcionario |
|---|---|
| Ficha declara que contem | Bloqueio, com o alergenico nomeado |
| Ficha declara "pode conter" | Atencao — confirmar no balcao |
| **Ninguem declarou** | Atencao — o app nao afirma que e seguro |
| Todos os alergenicos da pessoa constam como nao contem | Liberado |

A regra que sustenta isso: **falta de informacao nunca vira liberacao**. Deduzir
alergenico do nome do prato e o erro que machuca — "STROGONOFF DE CARNE" nao
avisa que leva creme de leite, "FILE DE FRANGO A MILANESA" nao avisa que leva
ovo e trigo.

`coverage()` mede quanto da lista obrigatoria cada prato declara, para o
nutricionista saber se o cardapio ja sustenta a funcao de alerta.

## Historico, favoritos e pontos

- todo prato montado fica registrado, com os macros somados no dia
- o funcionario favorita um prato e recebe aviso quando ele volta ao cardapio
- pontos por **constancia, composicao, variedade e meta de proteina**

Nenhuma regra de pontuacao premia deficit calorico ou perda de peso, e nao ha
ranking entre colegas. Num app corporativo, premiar comer menos sob o olhar do
empregador empurra para uma relacao ruim com comida. Cada regra carrega o campo
`basis` e existe teste garantindo que nenhuma se apoie em deficit ou peso.

## Banco de dados

O bot cria automaticamente o arquivo `apetit.db` com:

- clientes cadastrados
- aceite LGPD e data do consentimento
- historico de pedidos
- pratos cadastrados no cardapio
- pratos favoritos/aguardados
- atualizacoes de cardapio semanal

Esse arquivo fica fora do Git por seguranca e privacidade.

## LGPD e consentimento

Como o bot guarda telefone, endereco/bairro, historico e preferencias, o cadastro so e concluido depois do aceite do cliente.

O cliente pode:

- ver os dados salvos com `/meus_dados`
- excluir cadastro, historico e favoritos com `/excluir_dados`
- refazer o cadastro com `/recadastrar`

O banco guarda `consent_accepted` e `consented_at`. Se o cliente nao aceitar, o bot nao conclui o cadastro nem libera pedidos.

## Grafo do fluxo

```mermaid
flowchart TD
    A["Cliente abre o bot no Telegram"] --> B{Cliente ja tem cadastro?}
    B -- "Nao" --> C["Cadastro obrigatorio: nome, telefone, endereco/bairro, objetivo e restricao alimentar"]
    C --> LGPD{Cliente aceita guardar dados?}
    LGPD -- "Nao" --> X["Bot nao conclui cadastro nem libera pedidos"]
    LGPD -- "Sim" --> D["Salvar cliente no SQLite com data do consentimento"]
    B -- "Sim" --> E["Menu principal"]
    D --> E

    E --> F["Ver cardapio"]
    E --> G["Receber recomendacao"]
    E --> H["Ver perfil e historico"]
    E --> LGPD2["/meus_dados ou /excluir_dados"]

    F --> I["Bot lista pratos com preco, dia, tags e alergenicos"]
    G --> J["Bot cruza objetivo + restricao + historico + cardapio disponivel"]
    J --> K["Sugere prato compativel"]
    K --> L["Cliente escolhe prato"]
    I --> L

    L --> M{Prato conflita com restricao?}
    M -- "Sim" --> N["Pedido nao e registrado"]
    N --> O["Bot avisa o motivo e sugere alternativas seguras"]
    O --> L
    M -- "Nao" --> P["Registrar pedido no historico"]
    P --> Q["Oferecer aviso quando o prato voltar"]
    Q --> R{Cliente quer ser avisado?}
    R -- "Sim" --> S["Salvar prato em favoritos/aguardados"]
    R -- "Nao" --> E
    S --> E

    T["Admin atualiza cardapio semanal"] --> U["Salvar semana no SQLite"]
    U --> V["Buscar clientes com prato favorito ou recorrente"]
    V --> W["Enviar alerta no Telegram quando o prato voltar"]
```

## Cadastrar pratos

Administradores podem cadastrar ou atualizar pratos assim:

```text
/cardapio_add Nome do prato | 29,90 | segunda | ingredientes | alergenicos | tags | disponivel
```

Exemplo:

```text
/cardapio_add Frango Grelhado | 31,90 | quinta | frango, arroz, legumes | nenhum | proteico, caseiro | sim
```

Para listar o cardapio cadastrado:

```text
/cardapio_list
```

Para ver um resumo administrativo com clientes, pedidos, favoritos e pratos mais pedidos:

```text
/relatorio
```

## Atualizar cardapio semanal

Envie o comando abaixo no Telegram para registrar os pratos da semana e avisar clientes que aguardam algum deles ou ja pediram o prato varias vezes:

```text
/cardapio_semana Lasanha de Legumes
Peixe Assado com Legumes
Sopa de Lentilha
```

## Comandos administrativos

`/cardapio_add`, `/cardapio_list`, `/cardapio_semana` e `/relatorio` sao restritos a administradores.

A lista de administradores e obrigatoria: enquanto `ADMIN_TELEGRAM_IDS` estiver vazio, **ninguem** consegue usar esses comandos. Isso e proposital, porque `/relatorio` expoe nome de clientes e historico de pedidos e `/cardapio_semana` dispara mensagem para toda a base.

```env
ADMIN_TELEGRAM_IDS=123456789,987654321
APETIT_DB_PATH=apetit.db
```

## Deploy

Para operar de verdade, use webhook em producao e deixe polling apenas para testes locais.

Variaveis recomendadas:

```env
TELEGRAM_BOT_TOKEN=seu_token_novo
ADMIN_TELEGRAM_IDS=123456789
APETIT_DB_PATH=apetit.db
TELEGRAM_WEBHOOK_URL=https://seu-app.onrender.com
TELEGRAM_WEBHOOK_PATH=telegram-webhook
TELEGRAM_WEBHOOK_SECRET_TOKEN=um-segredo-forte
PORT=8000
```

Com `TELEGRAM_WEBHOOK_URL` preenchido, o bot sobe com webhook automaticamente. Sem essa variavel, ele continua usando polling.

Opcoes de hospedagem:

- Render, Railway ou Fly para uma primeira versao gerenciada
- VPS quando voce quiser mais controle de sistema, disco e backups
- SQLite funciona para MVP; para escala maior, considere migrar para PostgreSQL

Backup do banco:

```powershell
python scripts/backup_db.py
```

O script cria uma copia em `backups/`. Em producao, agende esse comando no provedor ou na VPS e guarde copias fora da maquina principal.

Logs e monitoramento:

- os logs saem no stdout do processo, que Render/Railway/Fly/VPS conseguem capturar
- monitore reinicios, erros de webhook e espaco em disco
- mantenha um alerta simples para queda do servico e falhas de backup

## Checklist antes de usar com clientes

- gerar um token novo no BotFather se o token antigo foi exposto
- preencher `TELEGRAM_BOT_TOKEN` no `.env`
- configurar `ADMIN_TELEGRAM_IDS` para proteger comandos administrativos
- preencher `TELEGRAM_WEBHOOK_URL` em producao
- configurar rotina de backup do banco
- cadastrar pratos reais com ingredientes, alergenicos e tags
- testar um cadastro novo com `/start`
- testar aceite LGPD, `/meus_dados` e `/excluir_dados`
- testar objetivos diferentes, como perder peso e ganhar massa
- testar uma restricao alimentar e confirmar que prato incompatavel e bloqueado
- testar `/cardapio_semana` com um prato favorito para confirmar o alerta

## Exemplos de frases

- `O que tem hoje sem carne?`
- `O que voce recomenda hoje?`
- `A comida estava fria.`
- `Gostei muito do almoco de hoje!`
- `Posso pedir o estrogonofe de cogumelos?`
