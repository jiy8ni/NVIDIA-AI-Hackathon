FROM node:22-bookworm-slim

# This is a runtime image, not a source image.  The sandbox clones the verified
# Git ref into /sandbox/work, so source edits cannot be silently baked in.
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv python3-pip git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/handoff-venv \
    && /opt/handoff-venv/bin/pip install --no-cache-dir --upgrade pip

WORKDIR /opt/handoff-deps
COPY requirements-lock.txt pyproject.toml ./
RUN /opt/handoff-venv/bin/pip install --no-cache-dir -r requirements-lock.txt \
    && /opt/handoff-venv/bin/pip install --no-cache-dir "mcp>=1.12,<2"

COPY services/api/package.json services/api/package-lock.json ./services/api/
COPY apps/web/package.json apps/web/package-lock.json ./apps/web/
RUN npm ci --prefix ./services/api \
    && npm ci --prefix ./apps/web

COPY deploy/openshell/bootstrap-and-run.sh /usr/local/bin/bootstrap-and-run-handoffos
RUN chmod 0555 /usr/local/bin/bootstrap-and-run-handoffos

ENV HANDOFF_PYTHON=/opt/handoff-venv/bin/python \
    PYTHONUTF8=1 \
    PATH=/opt/handoff-venv/bin:$PATH

ENTRYPOINT ["/usr/local/bin/bootstrap-and-run-handoffos"]
