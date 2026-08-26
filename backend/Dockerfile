# ContentForge 后端镜像
FROM python:3.10-slim

WORKDIR /app

# 先复制依赖清单（利用层缓存）
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码（含 templates/channels.yaml）
COPY backend/app ./app

ENV PYTHONPATH=/app \
    DATA_DIR=/app/data

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
