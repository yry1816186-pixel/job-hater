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
# 前端产物放独立目录并显式声明（app 按 JOBHATER_DIST 定位，与 pip 安装布局解耦）
COPY --from=frontend /build/dist/ /opt/jobhater/dist/
ENV JOBHATER_DIST=/opt/jobhater/dist
RUN pip install --no-cache-dir .
# 数据目录挂载点（业务数据全部在此卷内）
ENV JOBHATER_DATA=/data
VOLUME ["/data"]
EXPOSE 8787
# 容器自检（slim 镜像无 curl，用标准库打 /api/health）
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/api/health', timeout=4)"]
CMD ["job-hater", "serve", "--host", "0.0.0.0", "--port", "8787"]
