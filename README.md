# Jawa Voice Navigator

A real-time voice assistant web application powered by Google Gemini, Flask, and Socket.IO. This project enables users to interact with a conversational AI using their microphone, with optional camera or screen sharing for richer context.

## Features

- 🎤 Real-time voice interaction with Google Gemini's audio dialog model
- 🖥️ Optional camera or screen sharing for multimodal input
- 💬 Web-based chat interface with live status and error updates
- 🔊 Audio playback of Gemini's responses
- ⚡ Fast, responsive UI using Flask, Socket.IO, and modern HTML/CSS

## Quickstart

### 1. Clone the Repository

```sh
git clone <your-repo-url>
cd gcp_bot
```

### 2. Install Dependencies

Ensure you have Python 3.8+ and `pip` installed.

```sh
pip install -r requirements.txt
```

### 3. Set Up Google Gemini API Key

Obtain your [Google Gemini API key](https://ai.google.dev/) and set it as an environment variable:

```sh
export GEMINI_API_KEY="your-api-key-here"
```

### 4. Run the Application

```sh
python app.py
```

The server will start on [http://localhost:5001](http://localhost:5001).

### 5. Open the Web UI

Navigate to [http://localhost:5001](http://localhost:5001) in your browser.

## Project Structure

```
.
├── app.py                # Flask + Socket.IO backend
├── requirements.txt      # Python dependencies
├── sample.txt            # Reference script for Gemini Live API
├── templates/
│   └── index.html        # Web UI template
├── .gitignore
```

## Usage

- Click the 🎤 mic button to start or stop an interaction.
- Choose "No Video", "Share Camera", or "Share Screen" for multimodal input.
- Speak into your microphone; Gemini will respond with audio and text.
- All interactions and statuses are displayed in the chat area.

## Notes

- This project uses [google-genai](https://github.com/google-gemini/cookbook) for Gemini integration.
- Audio and video processing use `pyaudio`, `opencv-python`, and `mss`.
- For production, secure your API keys and consider session management for multiple users.

## License

This project is for educational/demo purposes. See [LICENSE](LICENSE) if provided.

---

Inspired by [Google Gemini Cookbook](https://github.com/google-gemini/cookbook).
