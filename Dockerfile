# 배포용 컨테이너. run.py는 로컬 실행 시 브라우저를 자동으로 여는 것이
# 목적이라 헤드리스 컨테이너에는 맞지 않는다 -- 여기서는 uvicorn을 직접
# 실행하고 PORT는 플랫폼(Render/Railway/Fly 등)이 주입하는 값을 그대로 쓴다.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY static ./static
COPY data/samples ./data/samples

ENV HOST=0.0.0.0
EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
