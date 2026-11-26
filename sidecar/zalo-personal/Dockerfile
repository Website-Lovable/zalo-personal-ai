# Isolated sidecar for personal Zalo (zca-js 2.4.0, pinned). Internal network only.
FROM node:22-alpine
ENV NODE_ENV=production
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts && npm cache clean --force
COPY src ./src
USER node
EXPOSE 3100
HEALTHCHECK --interval=30s --timeout=3s CMD wget -qO- http://127.0.0.1:3100/healthz || exit 1
CMD ["node", "src/server.js"]
