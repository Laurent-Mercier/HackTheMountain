import pyaudio
import numpy as np
# Initialize pyAudio
p = pyaudio.PyAudio()
# Open audio stream
stream = p.open(format=pyaudio.paFloat32, channels=1, rate=44100, input=True, output=True)
# Record and play audio in real-time
frames_per_buffer = 1024
for _ in range(100):
    audio_data = np.random.randn(frames_per_buffer).astype(np.float32)
    stream.write(audio_data.tobytes())
# Close the stream and terminate pyAudio
stream.stop_stream()
stream.close()
p.terminate()
