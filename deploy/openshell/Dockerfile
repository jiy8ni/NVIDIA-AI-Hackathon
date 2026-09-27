FROM node:22-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements-lock.txt ./
RUN python3 -m venv .venv \
    && .venv/bin/python -m pip install --no-cache-dir -r requirements-lock.txt

COPY services/api/package*.json ./services/api/
COPY apps/web/package*.json ./apps/web/
RUN npm ci --prefix services/api \
    && npm ci --prefix apps/web

COPY . ./
RUN chown -R node:node /app

USER node
ENV PYTHONUTF8=1

EXPOSE 5173

CMD ["node", "scripts/dev.mjs"]
