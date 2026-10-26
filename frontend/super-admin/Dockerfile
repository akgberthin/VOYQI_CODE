# Build the console, then serve it with nginx, which also forwards /api to
# the gateway so the session cookie stays same-origin.
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
# nginx's image renders templates/*.template with these at start-up.
ENV GATEWAY_URL=http://api-gateway-svc:8000 RESOLVER=127.0.0.11
ENV NGINX_ENVSUBST_FILTER="^(GATEWAY_URL|RESOLVER)$"
COPY nginx.conf /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
