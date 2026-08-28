# ContentForge 前端镜像：构建 → Nginx 托管 + /api 反向代理
FROM node:20-alpine AS build
WORKDIR /app
# 构建时注入 API Token（与 backend API_TOKEN 保持一致；空则不鉴权）
ARG VITE_API_TOKEN=""
ENV VITE_API_TOKEN=$VITE_API_TOKEN
COPY frontend/package.json ./
RUN npm install --no-fund --no-audit
COPY frontend/ .
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
