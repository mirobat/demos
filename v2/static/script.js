document.addEventListener('alpine:init', () => {
    Alpine.data('recordingApp', () => ({
        audioRecorder: null,
        isRecording: false,
        hasRecording: false,
        sentence: '',
        audioBlob: null,

        async init() {
            await this.fetchSentence();
        },

        async fetchSentence() {
            try {
                const response = await fetch('/get-sentence');
                const data = await response.json();
                this.sentence = data.sentence;
            } catch (error) {
                console.error('Error fetching sentence:', error);
                this.sentence = 'Error loading sentence';
            }
        },

        initializeRecorder(stream) {
            const audioContext = new (window.AudioContext || window.webkitAudioContext)();
            const input = audioContext.createMediaStreamSource(stream);
            this.audioRecorder = new Recorder(input);
        },

        async toggleRecording() {
            if (!this.isRecording) {
                // Start recording
                try {
                    const stream = await navigator.mediaDevices.getUserMedia({audio: true});
                    if (!this.audioRecorder) {
                        this.initializeRecorder(stream);
                    }
                    this.audioRecorder.record();
                    this.isRecording = true;
                } catch (error) {
                    console.error('Error starting recording:', error);
                    alert('Error starting recording');
                }
            } else {
                // Stop recording
                this.audioRecorder.stop();
                this.isRecording = false;

                // Create preview
                this.audioRecorder.exportWAV((blob) => {
                    this.audioBlob = blob;
                    const audioUrl = URL.createObjectURL(blob);
                    this.$refs.audioPlayer.src = audioUrl;
                    this.hasRecording = true;
                });
            }
        },

        async saveRecording() {
            if (!this.audioBlob) {
                return;
            }

            const formData = new FormData();
            formData.append('audio', this.audioBlob, 'recording.wav');

            try {
                const response = await fetch('/upload-audio', {
                    method: 'POST',
                    body: formData,
                });

                if (response.ok) {
                    // alert('Recording uploaded successfully!');
                    this.resetRecording();
                    await this.fetchSentence();
                } else {
                    alert('Error uploading recording');
                }
            } catch (error) {
                console.error('Error uploading recording:', error);
                alert('Error uploading recording');
            }
        },

        cancelRecording() {
            this.resetRecording();
        },

        resetRecording() {
            if (this.audioRecorder) {
                this.audioRecorder.clear();
            }
            this.hasRecording = false;
            this.audioBlob = null;
            this.$refs.audioPlayer.src = '';
        }
    }));
});