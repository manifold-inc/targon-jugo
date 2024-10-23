build:
  docker build -t manifoldlabs/sn4-exegestor .

run: build
  docker run -p 8000:8000 --env-file .env -d --name dev_image manifoldlabs/sn4-exegestor

# Alias for the run command
up: run
