import pyaudio

# --- Audio Configuration ---
FORMAT = pyaudio.paInt16
CHANNELS = 1
SEND_SAMPLE_RATE = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE = 1024
INPUT_DEVICE_INDEX = 2 # Default to device 2

# --- Gemini Model Configuration ---
MODEL = "models/gemini-2.5-flash-preview-native-audio-dialog"