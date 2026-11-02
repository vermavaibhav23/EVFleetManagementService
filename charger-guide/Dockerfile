FROM python:3.13-slim
WORKDIR /guide
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY *.py schema.sql index.html ./
COPY assets ./assets
COPY cab_watch ./cab_watch
CMD ["sh","-c","exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}"]

