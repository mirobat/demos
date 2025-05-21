# /// script
# requires-python = ">=3.12"
# dependencies = ["soundfile", "boto3", "loguru", "fastapi", "python-multipart", "uvicorn"]
# ///
# DO NOT MODIFY THE COMMENT ABOVE
# Usage: install uv from https://docs.astral.sh/uv/, then simply
# `uv run demo.py <cutset> <backup_bucket_name> <UI_password>`.
from fastapi import FastAPI, UploadFile, File, Depends, HTTPException, status
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
import secrets
import random
import os
from datetime import datetime

app = FastAPI()

# Initialize HTTP Basic Auth
security = HTTPBasic()
USERNAME = "demo"

def verify_credentials(credentials: HTTPBasicCredentials = Depends(security)):
    """Verify HTTP Basic Auth credentials."""
    correct_username = secrets.compare_digest(credentials.username, USERNAME)
    correct_password = secrets.compare_digest(credentials.password, USERNAME)
    
    if not (correct_username and correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Sample sentences
sentences = [
    "The quick brown fox jumps over the lazy dog.",
    "She sells seashells by the seashore.",
    "Peter Piper picked a peck of pickled peppers.",
    "How much wood would a woodchuck chuck if a woodchuck could chuck wood?",
]

# Create uploads directory if it doesn't exist
UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

@app.get("/")
async def read_root(credentials: HTTPBasicCredentials = Depends(verify_credentials)):
    """Serve the main HTML page."""
    return FileResponse("static/index.html")

@app.get("/get-sentence")
async def get_sentence():
    """Return a random sentence from the list."""
    return {"sentence": random.choice(sentences)}

@app.post("/upload-audio")
async def upload_audio(audio: UploadFile = File(...)):
    """Save the uploaded audio file."""
    try:
        # Generate unique filename using timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"recording_{timestamp}.wav"
        file_path = os.path.join(UPLOAD_DIR, filename)
        
        # Save the file
        with open(file_path, "wb") as f:
            content = await audio.read()
            f.write(content)
        
        return {"message": "Audio uploaded successfully", "filename": filename}
    except Exception as e:
        return {"error": str(e)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)