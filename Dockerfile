# ---------- 第一阶段：构建前端 ----------
# web/dist 被 .gitignore 排除，CI / 全新克隆里没有现成产物，所以镜像内自己构建。
FROM node:20-alpine AS web

WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build


# ---------- 第二阶段：运行时镜像 ----------
FROM python:3.12-slim

ENV TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt \
    && mkdir -p /app/data/reports

COPY . /app

# 前端产物由 web 阶段提供，覆盖仓库里可能存在的旧 dist
COPY --from=web /web/dist /app/web/dist

VOLUME ["/app/data"]

ENTRYPOINT ["python3", "run.py"]
# 默认 --plan 是只读模式（不写库、不推送）。正式采集需显式传 --run，
# 例如: docker run ... weekend-radar --run
CMD ["--plan"]
