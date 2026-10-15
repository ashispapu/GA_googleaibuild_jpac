# PlantGuard web app (Next.js standalone).   docker build --build-arg NEXT_PUBLIC_API_URL=https://api.example.com -t plantguard-web p3-frontend
FROM node:20-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

FROM node:20-slim AS build
WORKDIR /app
ARG NEXT_PUBLIC_API_URL
ARG NEXT_PUBLIC_PLANT_TZ=UTC
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL NEXT_PUBLIC_PLANT_TZ=$NEXT_PUBLIC_PLANT_TZ NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules node_modules
COPY . .
RUN test -n "$NEXT_PUBLIC_API_URL" || (echo "build-arg NEXT_PUBLIC_API_URL is required" && exit 1) && npm run build

FROM node:20-slim
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0 AUTH_TRUST_HOST=true
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
USER node
EXPOSE 3000
CMD ["node", "server.js"]
