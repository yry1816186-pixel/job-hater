# 多阶段：Node 构建前端 → Python 运行时单进程服务 UI+API
FROM node:20-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npx tsc -b && npx vite build

FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY jobhater/ ./jobhater/
COPY --from=frontend /build/dist/ ./frontend/dist/
RUN pip install --no-cache-dir .
# 数据目录挂载点（业务数据全部在此卷内）
ENV JOBHATER_DATA=/data
VOLUME ["/data"]
EXPOSE 8787
CMD ["job-hater", "serve", "--host", "0.0.0.0", "--port", "8787"]
