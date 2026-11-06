FROM python:3.12-slim

ENV TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt \
    && mkdir -p /app/data/reports

COPY . /app

VOLUME ["/app/data"]

ENTRYPOINT ["python3", "run.py"]
# 默认 --plan 是只读模式（不写库、不推送）。正式采集需显式传 --run，
# 例如: docker run ... weekend-radar --run
CMD ["--plan"]
