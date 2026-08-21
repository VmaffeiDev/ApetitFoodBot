# Imagem do ApetitFoodBot.
#
# Sem etapa de build: as dependencias (python-telegram-bot, openpyxl) sao Python
# puro, entao a slim basta e a imagem fica pequena.
#
# O banco vive em /data, e /data precisa ser um volume persistente no provedor.
# Sem volume, o SQLite mora no disco do container e some no proximo deploy —
# junto com o cadastro, o historico e as avaliacoes de todo mundo.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    APETIT_DB_PATH=/data/apetit.db

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY apetit/ ./apetit/
COPY scripts/ ./scripts/
COPY bot.py .

# Usuario sem privilegio, dono de /data para conseguir gravar o banco.
RUN useradd --create-home --uid 10001 apetit \
    && mkdir -p /data \
    && chown -R apetit:apetit /data /app
USER apetit

VOLUME ["/data"]

# Sem TELEGRAM_WEBHOOK_URL o bot sobe em polling, que e o modo que nao precisa
# de URL publica nem certificado — o caminho mais curto para colocar de pe.
CMD ["python", "bot.py"]
