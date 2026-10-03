# The PR admin panel.
#
# Two stages. The builder needs dev dependencies and the source; the runner needs
# neither, so the image that actually runs carries no TypeScript, no test
# framework and no source tree - only the compiled standalone output.
#
# It runs as a non-root user. The panel is the first thing in this deployment
# meant to be reachable from a browser, and a container that gets a shell should
# not get one as root.

FROM node:22-alpine AS builder
WORKDIR /app

# Dependencies first, so a source-only change does not reinstall them.
COPY package.json package-lock.json ./
RUN npm ci

COPY tsconfig.json next.config.mjs postcss.config.mjs ./
COPY src ./src
# Empty today, but the runner stage copies it unconditionally and a missing
# directory fails the build rather than being treated as nothing.
COPY public ./public
# `output: "standalone"` is set by the build below rather than in the config, so
# `next dev` locally is unaffected.
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000

# Next writes a self-contained server here, with only the modules it actually
# imports traced in.
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public

USER node
EXPOSE 3000
# No healthcheck curl: the image has no curl, and adding one to probe a page
# would be adding a binary for the probe's sake.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD node -e "fetch('http://127.0.0.1:3000/auth/failed').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

CMD ["node", "server.js"]
