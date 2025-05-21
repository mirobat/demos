document.addEventListener('alpine:init', () => {
    Alpine.data('recordingApp', () => ({
        audioRecorder: null,
        isRecording: false,
        hasRecording: false,
        sentence: '',
        recordedCount: 0,
        accent: '',
        audioBlob: null,
        isLoading: false,

        async init() {
            await this.fetchSentence();
        },

        async fetchSentence(skip=false) {
            this.isLoading = true;
            try {
                const response = await fetch('/get-sentence?' + new URLSearchParams({skip}).toString());
                const data = await response.json();
                this.sentence = data.sentence;
                this.recordedCount = data.count;
            } catch (error) {
                console.error('Error fetching sentence:', error);
                this.sentence = 'Error loading sentence';
            } finally {
                this.isLoading = false;
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

            const audioContext = new (window.AudioContext || window.webkitAudioContext)();
            const sampleRate = audioContext.sampleRate;

            const formData = new FormData();
            formData.append('audio', this.audioBlob, 'recording.wav');
            formData.append('sampleRate', sampleRate);
            formData.append('accent', this.accent);

            this.isLoading = true;
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
            } finally {
                this.isLoading = false;
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
        },

        async skip(){
            this.resetRecording();
            await this.fetchSentence(true);
        }
    }));
});