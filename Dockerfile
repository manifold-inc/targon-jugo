FROM python:3.10

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY ./epistula.py ./jugo.py ./

CMD ["python",  "-m", "uvicorn", "jugo:app", "--host", "0.0.0.0", "--port", "80"]
