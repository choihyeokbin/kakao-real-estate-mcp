FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN pip install --no-cache-dir -e .

ENV DATA_GO_KR_API_KEY=b20f498fbb8a9b9dfe87df17e5b4e2573c3c20e311ec039ccb3123fb4a385566
ENV KAKAO_REST_API_KEY=fb663fed4f17af4695bf43ff8e3a3fd0

EXPOSE 8000

CMD ["python", "-m", "kakao_real_estate.server"]
