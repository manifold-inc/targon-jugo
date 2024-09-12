# Run the FastAPI application using uvicorn
run:
    uvicorn ingestor:app --host 0.0.0.0 --port 8000 --reload

# Alias for the run command
up: run