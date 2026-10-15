# PlantGuard web app (Next.js standalone). Build from the REPO ROOT (the app imports the API types from generated/):
#   docker build -f p3-frontend/Dockerfile --build-arg NEXT_PUBLIC_API_URL=https://api.example.com -t plantguard-web .
FROM node:20-slim AS deps
WORKDIR /app
COPY p3-frontend/package.json p3-frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund

FROM node:20-slim AS build
WORKDIR /app
ARG NEXT_PUBLIC_API_URL
ARG NEXT_PUBLIC_PLANT_TZ=UTC
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL NEXT_PUBLIC_PLANT_TZ=$NEXT_PUBLIC_PLANT_TZ NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules node_modules
COPY generated /generated
COPY p3-frontend/ .
RUN test -n "$NEXT_PUBLIC_API_URL" || (echo "build-arg NEXT_PUBLIC_API_URL is required" && exit 1) && npm run build

FROM node:20-slim
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0 AUTH_TRUST_HOST=true
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
USER node
EXPOSE 3000
CMD ["node", "server.js"]
