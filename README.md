# ApetitFoodBot

Bot de Telegram para o **controle nutricional dos colaboradores** da empresa. Não é um sistema de pedido/entrega:
o refeitório já serve um cardápio fixo definido pela empresa; o bot existe para que cada colaborador:

- saiba com antecedência o cardápio da semana
- receba uma recomendação de prato por categoria (lanche, acompanhamento, sobremesa, fruta, bebida) de acordo com o
  seu objetivo (emagrecer, ganhar massa, manter equilíbrio, alimentação saudável ou praticidade)
- seja avisado quando algum prato do cardápio contiver um alérgeno/restrição que ele cadastrou
- registre o que pretende comer em cada dia, para a empresa acompanhar as escolhas
- ganhe pontos, sequência (streak) e badges por manter o registro em dia e escolher pratos alinhados ao objetivo

## Fluxo do colaborador

1. `/start` inicia o cadastro: nome, objetivo e restrição/alergia alimentar.
2. Antes de concluir, o bot pede o aceite do uso dos dados (LGPD). Sem aceite, o cadastro não é salvo.
3. `/cardapio_semana` mostra um resumo dos dias da semana com cardápio já importado, com aviso ⚠️ nas categorias
   que têm algum prato incompatível com a restrição do colaborador.
4. Ao abrir um dia específico, o bot lista os pratos de cada categoria, marca com ⭐ o mais indicado para o
   objetivo do colaborador, bloqueia com ⚠️ os pratos que batem com a restrição cadastrada (não deixa escolher) e
   permite escolher um prato por categoria.
5. `/minhas_escolhas` mostra o que o colaborador já escolheu na semana.
6. `/meu_progresso` mostra pontos, sequência de dias úteis com escolha registrada e badges conquistados;
   `/ranking` mostra o top 10 colaboradores por pontos (só primeiro nome, sem expor objetivo/restrição de ninguém).
7. `/meus_dados` mostra os dados salvos; `/excluir_dados` apaga cadastro e escolhas.
8. `/recadastrar` refaz nome, objetivo e restrição.

## Gamificação

Pontos e sequência são calculados sempre a partir do histórico real de escolhas (`selections`), nunca de um
contador separado — evita drift entre o que o colaborador vê e o que realmente foi registrado.

- **+10 pontos** por dia em que o colaborador registra pelo menos uma escolha no cardápio
- **+5 pontos** extra por escolha que bate com o prato recomendado (⭐) para o objetivo dele naquela categoria/dia
- **Sequência**: dias úteis consecutivos com pelo menos uma escolha registrada (pula fim de semana automaticamente)
- **Badges**: Primeiro Passo (primeira escolha), Em Chamas (sequência ≥ 3), Guardião do Objetivo (5+ escolhas
  alinhadas ao objetivo), Vida Saudável (100+ pontos), Mestre Apetit (300+ pontos e sequência ≥ 5)

## Cadastro do cardápio pela empresa (admin)

O cardápio é importado a partir da planilha que a empresa já usa para o refeitório (formato "Oficina do Lanche"):
uma linha por dia do mês (coluna `Dia`) e uma coluna por categoria (`LANCHE`, `ACOMPANHAMENTO`, `ACOMPANHAMENTO 2`,
`SOBREMESA`, `FRUTA`, `BEBIDA`, `BEBIDA 2`, ...). Cada célula preenchida segue o padrão
`<percentual> - <código> - <NOME DO PRATO> - <peso/custo>`.

Para importar, um administrador (configurado em `ADMIN_TELEGRAM_IDS`) envia o arquivo `.xlsx` diretamente no chat do
bot, com a legenda no formato `MM/AAAA` (ex.: `07/2026`) indicando o mês/ano daquele calendário. Sem legenda, o bot
assume o mês atual.

Assim que a importação termina, o bot avisa automaticamente todos os colaboradores cadastrados (com aceite LGPD)
de que o cardápio da semana está disponível, já com botão para abrir `/cardapio_semana`. Se algum prato daquele
período bate com a restrição/alergia cadastrada do colaborador, o aviso já vem com essa observação — sem precisar
o colaborador ir checar por conta própria. Pratos ainda sem ingredientes/alergênicos cadastrados também geram um
aviso genérico pedindo para confirmar com o refeitório.

Pratos com código novo (nunca importado antes) entram no catálogo sem ingredientes/alergênicos cadastrados. O bot
avisa quais são no retorno da importação e eles também aparecem em:

```text
/pratos_pendentes
```

Para completar o cadastro de um prato (reaproveitado em toda vez que aquele código aparecer em cardápios futuros):

```text
/prato_add <codigo> | ingredientes | alergenicos | tags
```

Exemplo:

```text
/prato_add 08.03.01.110 | pao, hamburguer de frango, queijo, alface, tomate | gluten, leite | proteico, praticidade
```

As `tags` são palavras-chave livres usadas para casar o prato com os objetivos dos colaboradores (ex.: `leve`,
`proteico`, `integral`, `vegano`, `perda de peso`, `ganho de massa`).

Para ver um resumo administrativo (colaboradores por objetivo, pratos mais escolhidos, pratos pendentes):

```text
/relatorio
```

Para restringir os comandos administrativos, configure no `.env`:

```env
ADMIN_TELEGRAM_IDS=123456789,987654321
```

Sem essa variável configurada, **ninguém** consegue rodar comandos administrativos (o bot nega por padrão).

## Segurança do token

Se um token foi colado em chat, issue, commit ou qualquer lugar público, gere outro no BotFather.
Não salve o token diretamente no código.

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

## Banco de dados

O bot cria automaticamente o arquivo `apetit.db` com:

- colaboradores cadastrados (nome, objetivo, restrição, aceite LGPD e data do consentimento)
- catálogo de pratos (nome, categoria, ingredientes, alergênicos, tags)
- cardápio importado por dia/categoria
- escolhas dos colaboradores por dia/categoria

Esse arquivo fica fora do Git por segurança e privacidade.

## LGPD e consentimento

O bot guarda nome, objetivo e restrição/alergia alimentar — dado de saúde é dado sensível segundo a LGPD — então o
cadastro só é concluído depois do aceite do colaborador.

O colaborador pode:

- ver os dados salvos com `/meus_dados`
- apagar cadastro e escolhas com `/excluir_dados`
- refazer o cadastro com `/recadastrar`

O banco guarda `consent_accepted` e `consented_at`. Sem aceite, o bot não conclui o cadastro nem libera o cardápio.

## Backup do banco

```powershell
python scripts/backup_db.py
```

O script cria uma cópia em `backups/`. Em produção, agende esse comando no provedor ou na VPS e guarde cópias fora
da máquina principal.

## Checklist antes de usar com colaboradores

- gerar um token novo no BotFather se o token antigo foi exposto
- preencher `TELEGRAM_BOT_TOKEN` no `.env`
- configurar `ADMIN_TELEGRAM_IDS` para liberar os comandos administrativos
- importar o cardápio do mês/semana enviando o `.xlsx` com a legenda `MM/AAAA`
- completar `/prato_add` para todo prato listado em `/pratos_pendentes` antes de divulgar o cardápio
- configurar rotina de backup do banco
- testar um cadastro novo com `/start`, incluindo o aceite LGPD
- testar `/meus_dados` e `/excluir_dados`
- testar uma restrição/alergia e confirmar que o prato incompatível aparece bloqueado (⚠️) no `/cardapio_semana`
