FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Системные зависимости: git + ssh-клиент для Git Sync по SSH-remote
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    openssh-client \    
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
RUN git config --global --add safe.directory /vault
RUN git config --global user.email "LetoPisez@gmail.com"
RUN git config --global user.name "LetoPisez"
RUN git branch --set-upstream-to=origin/master master

COPY . /app

# По умолчанию Vault будет смонтирован как volume и путь задан через VAULT_PATH

CMD ["python", "-m", "bot.main"]

