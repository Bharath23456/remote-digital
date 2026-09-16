FROM node:22-alpine AS dependencies
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

FROM node:22-alpine AS builder
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=dependencies /app/node_modules ./node_modules
COPY frontend ./
ARG DJANGO_API_URL=http://backend:8000
ARG STORAGE_GATEWAY_URL=http://storage-gateway:9000
ARG IDENTITY_SERVICE_URL=http://identity-service:8100
ENV DJANGO_API_URL=$DJANGO_API_URL
ENV STORAGE_GATEWAY_URL=$STORAGE_GATEWAY_URL
ENV IDENTITY_SERVICE_URL=$IDENTITY_SERVICE_URL
RUN npm run build

FROM node:22-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 HOSTNAME=0.0.0.0 PORT=3000
RUN addgroup --system --gid 1001 nodejs && adduser --system --uid 1001 nextjs
COPY --from=builder --chown=nextjs:nodejs /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
USER nextjs
EXPOSE 3000
CMD ["node", "server.js"]
