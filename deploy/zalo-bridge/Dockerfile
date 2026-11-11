# Hộp chat CSKH (0102): the Zalo bridge (zca-js, read only). See bridge.mjs.
FROM node:22.11.0-alpine3.20
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts --omit=dev && npm cache clean --force
COPY bridge.mjs ./
RUN mkdir -p /data/sessions && chown -R node:node /data
USER node
ENV BRIDGE_PORT=8091 SESS_DIR=/data/sessions
EXPOSE 8091
CMD ["node", "bridge.mjs"]
