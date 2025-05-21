let audioRecorder = null;
let isRecording = false;

// Get DOM elements
const recordButton = document.getElementById('recordButton');
const recordingIndicator = document.getElementById('recordingIndicator');
const sentenceElement = document.getElementById('sentence');

// Fetch sentence when page loads
window.addEventListener('load', fetchSentence);

async function fetchSentence() {
    try {
        const response = await fetch('/get-sentence');
        const data = await response.json();
        sentenceElement.textContent = data.sentence;
    } catch (error) {
        console.error('Error fetching sentence:', error);
        sentenceElement.textContent = 'Error loading sentence';
    }
}

// Initialize audio recorder
function initializeRecorder(stream) {
    const audioContext = new (window.AudioContext || window.webkitAudioContext)();
    const input = audioContext.createMediaStreamSource(stream);
    audioRecorder = new Recorder(input);
}

// Handle recording button click
recordButton.addEventListener('click', async () => {
    if (!isRecording) {
        // Start recording
        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            if (!audioRecorder) {
                initializeRecorder(stream);
            }
            audioRecorder.record();
            isRecording = true;
            recordButton.textContent = 'Stop Recording';
            recordingIndicator.style.display = 'block';
        } catch (error) {
            console.error('Error starting recording:', error);
            alert('Error starting recording');
        }
    } else {
        // Stop recording
        audioRecorder.stop();
        isRecording = false;
        recordButton.textContent = 'Start Recording';
        recordingIndicator.style.display = 'none';

        // Export the audio
        audioRecorder.exportWAV(async (blob) => {
            const formData = new FormData();
            formData.append('audio', blob, 'recording.wav');

            try {
                const response = await fetch('/upload-audio', {
                    method: 'POST',
                    body: formData,
                });

                if (response.ok) {
                    alert('Recording uploaded successfully!');
                    // Clear recorder for next recording
                    audioRecorder.clear();
                } else {
                    alert('Error uploading recording');
                }
            } catch (error) {
                console.error('Error uploading recording:', error);
                alert('Error uploading recording');
            }
        });
    }
});