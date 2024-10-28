FROM python:3.10

WORKDIR /app/tests

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY tests/testEndon.py ./
COPY ./epistula.py ./jugo.py ./

CMD ["python", "testEndon.py"]
