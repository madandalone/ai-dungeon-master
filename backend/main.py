from fastapi import FastAPI


app = FastAPI(title="AI Dungeon Master API")


@app.get("/health")
def health_check():
    return {"status": "ok"}