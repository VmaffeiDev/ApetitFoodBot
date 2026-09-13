# Imagem do servidor da Apetit.
#
# Sem etapa de build: as dependencias (tornado, openpyxl, pypdf) sao Python
# puro, entao a slim basta e a imagem fica pequena.
#
# O banco vive em /data, e /data precisa ser um volume persistente no provedor.
# Sem volume, o SQLite mora no disco do container e some no proximo deploy —
# junto com o cadastro, o historico e as avaliacoes de todo mundo.
#
# O app (`demo/`) **nao** entra aqui: ele e arquivo estatico e vai para o GitHub
# Pages. Sao duas coisas com necessidades diferentes — uma precisa de banco e
# segredo, a outra so de HTTPS.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    APETIT_DB_PATH=/data/apetit.db \
    PORT=8000

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY apetit/ ./apetit/
COPY scripts/ ./scripts/

# Usuario sem privilegio, dono de /data para conseguir gravar o banco.
RUN useradd --create-home --uid 10001 apetit \
    && mkdir -p /data \
    && chown -R apetit:apetit /data /app
USER apetit

VOLUME ["/data"]
EXPOSE 8000

# `main` cria as tabelas antes de escutar: um banco novo responde "ainda nao ha
# cardapio publicado", e nao um erro de tabela ausente.
CMD ["python", "-m", "apetit.api"]
