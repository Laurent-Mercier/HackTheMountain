import pyaudio
import numpy as np

CHUNK = 1024
FORMAT = pyaudio.paFloat32
CHANNELS = 2
RATE = 44100

p = pyaudio.PyAudio()

# Find BlackHole device index
def find_blackhole():
    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        if "BlackHole" in info["name"] and info["maxInputChannels"] > 0:
            print(f"Found BlackHole at index {i}: {info['name']}")
            return i
    raise RuntimeError("BlackHole not found!")

# Find your speakers output
def find_speakers():
    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        print(f"[{i}] {info['name']} | out channels: {info['maxOutputChannels']}")
    index = int(input("Pick your speaker device index: "))
    return index

input_index = find_blackhole()
output_index = find_speakers()

stream_in = p.open(
    format=FORMAT,
    channels=CHANNELS,
    rate=RATE,
    input=True,
    input_device_index=input_index,
    frames_per_buffer=CHUNK
)

stream_out = p.open(
    format=pyaudio.paFloat32,
    channels=CHANNELS,
    rate=RATE,
    output=True,
    output_device_index=output_index,
    frames_per_buffer=CHUNK,
)

print("Listening from GarageBand... Press Ctrl+C to stop")

try:
    while True:
        data = stream_in.read(CHUNK, exception_on_overflow=False)
        audio = np.frombuffer(data, dtype=np.float32).copy()
        stream_out.write(audio.tobytes())

        # audio is now a numpy array of live samples
        # send it to your C++ program here
        print(f"Peak volume: {np.max(np.abs(audio)):.4f}")

except KeyboardInterrupt:
    print("Stopped.")
    stream_in.stop_stream()
    stream_in.close()
    stream_out.stop_stream()
    stream_out.close()
    p.terminate()