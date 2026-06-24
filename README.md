# ApetitFoodBot

Bot interno de Telegram para colaboradores consultarem o cardapio servido pela empresa.

O ApetitFoodBot nao e um sistema de venda para clientes. Ele e um assistente interno para funcionarios: mostra o cardapio do dia, ajuda na escolha da refeicao e registra a preferencia para historico, planejamento operacional e relatorios administrativos.

Importante: as refeicoes sao um beneficio da empresa. O bot nao exibe valores, nao calcula preco, nao recebe pagamento e nao usa botoes de compra.

## Objetivo do sistema

- apresentar o cardapio diario servido aos colaboradores;
- registrar a escolha do prato principal do funcionario;
- apoiar recomendacoes educativas conforme objetivo e restricao cadastrada;
- manter historico de escolhas e progresso nutricional opcional;
- gerar relatorios internos de adesao e pratos mais escolhidos;
- respeitar LGPD, consentimento e exclusao de dados.

## Fluxo do colaborador

1. O colaborador inicia o bot com `/start`.
2. O bot coleta nome, empresa/unidade e setor.
3. O colaborador aceita ou recusa o consentimento LGPD.
4. O bot libera o cardapio diario.
5. O colaborador escolhe uma das opcoes principais.
6. A escolha fica registrada no historico.

O colaborador pode consultar e apagar seus dados a qualquer momento.

## Cardapio Cotton de setembro

O cardapio base foi transcrito do arquivo `Cardapio_Cotton_Setembro kcal-1.pdf` para [cotton_menu.py](cotton_menu.py).

Como o PDF nao informa o ano, o sistema guarda as datas por dia e mes, no formato interno `09-01` a `09-30`.

Para testar uma data publicada:

```text
/cardapio 01/09
```

O cardapio de cada dia possui:

- prato principal 1;
- prato principal 2;
- opcao alternativa, geralmente com ovo;
- duas guarnicoes;
- tres saladas;
- sobremesa;
- fruta;
- arroz parboilizado;
- arroz integral;
- feijao;
- bebida.

Os valores de kcal e proteina sao os informados no PDF. Como o documento nao traz ingredientes completos nem alergenicos, o bot orienta colaboradores com restricoes a confirmarem a composicao com a equipe do restaurante.

## Comandos do colaborador

- `/start` - iniciar ou retomar o cadastro;
- `/recadastrar` - refazer o cadastro funcional;
- `/cardapio` - ver o cardapio de hoje;
- `/cardapio 01/09` - consultar uma data especifica publicada;
- `/historico` - ver escolhas de refeicao registradas;
- `/minha_meta` - criar ou consultar meta educativa;
- `/meu_progresso` - ver pontos, streak e badges;
- `/ranking` - ver ranking pseudonimizado;
- `/meus_dados` - consultar dados salvos;
- `/excluir_dados` - excluir cadastro, historico e dados nutricionais.

## Comandos administrativos

Defina os administradores pela variavel `ADMIN_TELEGRAM_IDS` no `.env`.

Listar o cardapio de uma data:

```text
/cardapio_list 01/09
```

Publicar ou alterar um componente do cardapio:

```text
/cardapio_add 01/09 | main_1 | Strogonoff de carne | 134 | 12
```

Formato:

```text
/cardapio_add data | categoria | nome | kcal | proteina_g
```

Categorias aceitas:

```text
main_1, main_2, main_option, side_1, side_2,
salad_1, salad_2, salad_3, dessert, fruit,
rice_1, rice_2, beans, drink
```

Atualizar a lista semanal usada para avisos de pratos favoritos/historico:

```text
/cardapio_semana Lasanha de Legumes
Peixe Assado com Legumes
```

Gerar relatorio administrativo:

```text
/relatorio
```

O relatorio mostra colaboradores cadastrados, consentimentos, escolhas registradas, dias de cardapio publicados, opcoes mais escolhidas, objetivos cadastrados, unidades/empresas e refeicoes recentes.

## Como executar localmente

Requisitos:

- Python 3.11 ou superior;
- token de um bot criado no Telegram pelo BotFather.

No PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Preencha o arquivo `.env`:

```dotenv
TELEGRAM_BOT_TOKEN=seu_token_novo
APETIT_USER_NAME=Colaborador
ADMIN_TELEGRAM_IDS=123456789
```

Inicie o bot:

```powershell
python bot.py
```

O banco SQLite e criado automaticamente em `apetit.db`. Para usar outro caminho:

```dotenv
APETIT_DB_PATH=C:\caminho\para\apetit.db
```

Aviso de seguranca: se um token do Telegram apareceu em captura de tela, log, commit ou terminal compartilhado, revogue o token no BotFather e gere outro antes de rodar o bot.

## Como testar no Telegram

Depois de iniciar `python bot.py`, envie ao bot:

```text
/start
```

Finalize o cadastro e teste:

```text
/cardapio 01/09
```

O resultado esperado e:

- titulo "Cardapio servido em 01/09";
- lista completa da refeicao;
- kcal e proteina quando informadas;
- botoes "Escolher...";
- mensagem informando que nao ha pagamento.

O resultado nao deve conter:

- `R$`;
- preco do prato;
- botao "Pedir";
- linguagem de venda, compra ou cobranca.

Se o Telegram ainda mostrar valores ou botoes "Pedir", provavelmente existe uma instancia antiga do bot rodando ou a branch antiga ainda esta aberta. Pare o processo atual, atualize o codigo e execute novamente.

## Testes automatizados

Execute:

```powershell
python -m unittest discover -s tests
```

Os testes validam:

- carga dos 22 dias uteis do cardapio Cotton;
- 14 componentes de refeicao por dia;
- ausencia de preco e botoes de compra;
- remocao das tabelas comerciais antigas;
- recomendacoes por objetivo/restricao;
- registro de escolhas de refeicao;
- relatorios administrativos;
- consentimento LGPD;
- exclusao de dados.

## Estrutura principal

- [bot.py](bot.py) - bot Telegram, fluxos, comandos, banco SQLite e relatorios;
- [cotton_menu.py](cotton_menu.py) - cardapio Cotton transcrito;
- [nutrition.py](nutrition.py) - calculos educativos de meta nutricional;
- [tests/test_core.py](tests/test_core.py) - testes automatizados do nucleo.

## Privacidade e LGPD

O cadastro funcional e o perfil nutricional possuem consentimentos separados.

O colaborador pode:

- consultar dados salvos com `/meus_dados`;
- excluir cadastro e historico com `/excluir_dados`;
- recusar criacao de meta nutricional opcional.

A exclusao remove cadastro, empresa/unidade, setor, historico de refeicoes, favoritos, perfil nutricional, pontos e badges.

## Observacao sobre o modelo antigo

Versoes anteriores tinham cardapio comercial, preco, pedido e tabelas como `menu_items` e `orders`. O modelo atual remove esse comportamento. O sistema agora trabalha com `daily_menu_components` e `meal_selections`, ou seja:

- componente do cardapio servido;
- escolha de refeicao do colaborador.

Nao ha carrinho, pagamento, checkout ou pedido comercial.
