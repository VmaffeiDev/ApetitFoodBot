# ApetitFoodBot

Bot interno do Telegram para colaboradores consultarem a refeicao fornecida pela empresa.

O sistema **nao vende comida, nao recebe pagamentos e nao exibe precos**. Ele publica o cardapio servido no dia, ajuda o colaborador a escolher uma das opcoes principais e registra essa escolha para historico e planejamento.

## O que o bot faz

- cadastro funcional com nome, empresa/unidade e setor;
- aceite LGPD antes de salvar dados pessoais;
- cardapio diario completo com kcal e proteina quando informadas;
- escolha entre prato principal 1, prato principal 2 e opcao alternativa;
- recomendacao educativa conforme objetivo e restricao cadastrada;
- historico de refeicoes, metas nutricionais e gamificacao opcional;
- relatorio administrativo de adesao e opcoes mais escolhidas;
- exclusao integral dos dados pelo proprio colaborador.

## Cardapio Cotton de setembro

O arquivo `Cardapio_Cotton_Setembro kcal-1.pdf` foi transcrito para `cotton_menu.py` como cardapio operacional de 22 dias uteis. O documento nao informa o ano; por isso as datas sao armazenadas como dia e mes (`09-01` ate `09-30`).

Para consultar uma data durante os testes:

```text
/cardapio 01/09
```

O cardapio inclui:

- dois pratos principais e uma opcao com ovo;
- duas guarnicoes;
- tres saladas;
- sobremesa e fruta;
- arroz parboilizado, arroz integral e feijao;
- bebida.

Os valores nutricionais sao os informados no PDF. Como o documento nao inclui ingredientes nem alergenicos, o bot orienta colaboradores com restricoes a confirmarem a composicao com a equipe do restaurante.

## Como executar

Requisitos: Python 3.11 ou superior e um bot criado no Telegram.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Preencha ao menos:

```dotenv
TELEGRAM_BOT_TOKEN=seu_token_novo
APETIT_USER_NAME=Colaborador
ADMIN_TELEGRAM_IDS=123456789
```

Depois execute:

```powershell
python bot.py
```

O banco SQLite e criado automaticamente em `apetit.db`. Para usar outro local, defina `APETIT_DB_PATH`.

> Se um token do Telegram tiver sido exibido em tela, log ou captura, revogue-o no BotFather e gere outro antes de iniciar o bot.

## Comandos do colaborador

- `/start` — iniciar ou retomar o cadastro;
- `/cardapio` — ver o cardapio de hoje;
- `/cardapio 01/09` — consultar uma data publicada;
- `/historico` — ver escolhas de refeicao;
- `/minha_meta` — criar ou consultar meta educativa;
- `/meu_progresso` — ver pontos, streak e badges;
- `/ranking` — ver ranking pseudonimizado;
- `/meus_dados` — consultar dados salvos;
- `/excluir_dados` — excluir cadastro e historico.

## Comandos administrativos

Defina os IDs autorizados em `ADMIN_TELEGRAM_IDS`.

Para listar uma data:

```text
/cardapio_list 01/09
```

Para publicar ou alterar um componente:

```text
/cardapio_add 01/09 | main_1 | Strogonoff de carne | 134 | 12
```

Categorias aceitas:

```text
main_1, main_2, main_option, side_1, side_2,
salad_1, salad_2, salad_3, dessert, fruit,
rice_1, rice_2, beans, drink
```

O comando `/relatorio` mostra colaboradores cadastrados, escolhas de refeicao, dias publicados e opcoes mais escolhidas.

## Privacidade

O cadastro funcional e o perfil nutricional possuem consentimentos separados. O colaborador pode consultar os dados em `/meus_dados` e apaga-los em `/excluir_dados`. A exclusao inclui cadastro, refeicoes, favoritos, perfil nutricional, pontos e badges.

## Testes

```powershell
python -m unittest discover -s tests
```

O conjunto de testes valida carga do cardapio, ausencia de precos, recomendacao, registro de refeicao, LGPD, relatorios e exclusao de dados.
