FROM python:3.13-slim
WORKDIR /guide
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY *.py schema.sql index.html ./
CMD ["sh","-c","exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}"]

