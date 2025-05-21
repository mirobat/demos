from fastapi import FastAPI, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import random
import os
from datetime import datetime

app = FastAPI()

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
async def read_root():
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