# =============================================================================
# DESTINO: frontend/Dockerfile
# CORREÇÃO: pnpm install --no-frozen-lockfile (sem pnpm-lock.yaml no repo)
# Após o primeiro build bem-sucedido, o lockfile gerado deve ser commitado
# e esta linha revertida para --frozen-lockfile (builds reproduzíveis).
# =============================================================================
ARG VITE_API_BASE_URL=http://localhost:8000
ARG VITE_WS_URL=ws://localhost:8000/ws

# ── Stage 1: build do React ───────────────────────────────────────────────────
FROM node:20-alpine AS builder
WORKDIR /app

RUN npm install -g pnpm

# Copia manifesto de dependências primeiro (cache layer do Docker)
COPY package.json pnpm-lock.yaml* ./

# CORREÇÃO: --no-frozen-lockfile quando pnpm-lock.yaml ainda não existe
RUN pnpm install --no-frozen-lockfile

# Copia o restante do código e compila
COPY . .

ARG VITE_API_BASE_URL
ARG VITE_WS_URL
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
ENV VITE_WS_URL=$VITE_WS_URL

RUN pnpm run build

# ── Stage 2: serve com Nginx ──────────────────────────────────────────────────
FROM nginx:1.25-alpine AS final

RUN rm /etc/nginx/conf.d/default.conf
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
