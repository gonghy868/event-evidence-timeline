FROM python:3.11-slim

WORKDIR /srv
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/data

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY static ./static
COPY scenarios ./scenarios
COPY scripts ./scripts

# 取回的数据、事件文件、AI 缓存都放在 /data（部署平台挂载的持久卷），不打进镜像。
# 密钥通过环境变量注入：IFIND_API_KEY、FUYAO_API_KEY、DEEPSEEK_API_KEY。
VOLUME ["/data"]
EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
