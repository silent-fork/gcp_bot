import os
import asyncio
import base64
import io
import traceback
import threading



from flask import Flask, render_template
from flask_socketio import SocketIO, emit

from google import genai
from google.genai import types

import config

def get_api_key():
    """Reads the API key from local.properties or environment variables."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if api_key:
        return api_key

    try:
        with open('local.properties', 'r') as f:
            for line in f:
                if line.startswith('GEMINI_API_KEY'):
                    return line.split('=')[1].strip()
    except FileNotFoundError:
        pass  # File doesn't exist, which is fine

    raise ValueError("GEMINI_API_KEY not found. Please set it in your environment variables or in a local.properties file.")

API_KEY = get_api_key()

client = genai.Client(
    # http_options={"api_version": "v1beta"}, # This might be outdated or not needed for latest SDK
    api_key=API_KEY,
)

tools = [
    types.Tool(google_search=types.GoogleSearch()),
]

CONFIG = types.LiveConnectConfig(
    response_modalities=["AUDIO"],
)

from src.audio import list_audio_devices, play_audio_task, terminate_pyaudio
from src.gemini import (
    run_gemini_interaction,
    stop_gemini_session_async,
    global_session_vars,
)

app = Flask(__name__)
app.config['SECRET_KEY'] = os.urandom(24)
socketio = SocketIO(app, async_mode='threading')

# List audio devices on startup
list_audio_devices()

# Pass socketio object to gemini module
import src.gemini
import src.audio
src.gemini.socketio = socketio
src.audio.socketio = socketio


@app.route('/')
def index():
    return render_template('index.html')

async def stop_gemini_session_async():
    """Synchronous helper to stop the Gemini session and tasks."""
    if global_session_vars['is_running']:
        global_session_vars['is_running'] = False # Signal tasks to stop
        
        # Cancel all running asyncio tasks
        # This needs to be done carefully if tasks are in a different thread's loop
        # For simplicity here, assuming they can be cancelled. 
        # A more robust solution might involve queues or events for inter-thread communication.
        tasks_to_cancel = global_session_vars.get('tasks', [])
        for task in tasks_to_cancel:
            if not task.done():
                # If tasks run in a separate loop, you'd need to use loop.call_soon_threadsafe(task.cancel)
                task.cancel() 

        # Close live session if it exists (ensure it's done safely)
        live_session = global_session_vars.get('live_session')
        if live_session:
            # Assuming live_session.close() is thread-safe or called from the correct thread
            # If it's an async close, it needs to be handled in its event loop
            # For now, let's assume it can be called. If it's async, it needs special handling.
            # This part might need refinement depending on gemini SDK's close behavior.
            print("Attempting to close live session (if any). This might need async handling.")
            # A proper async close would be: asyncio.run_coroutine_threadsafe(live_session.close(), loop_of_session)

        global_session_vars['live_session'] = None
        global_session_vars['tasks'] = []
        
        # Clean up queues if necessary, though tasks should handle their queues on exit
        if global_session_vars.get('audio_out_queue'):
            while not global_session_vars['audio_out_queue'].empty():
                try:
                    global_session_vars['audio_out_queue'].get_nowait()
                    global_session_vars['audio_out_queue'].task_done()
                except Exception:
                    pass # Ignore errors during cleanup
        if global_session_vars.get('audio_in_queue'):
            while not global_session_vars['audio_in_queue'].empty():
                try:
                    global_session_vars['audio_in_queue'].get_nowait()
                    global_session_vars['audio_in_queue'].task_done()
                except Exception:
                    pass

        print("Gemini interaction stopped.")
        # socketio.emit('status', {'message': 'Gemini interaction stopped.'}) # Will be emitted by handle_stop_interaction
    else:
        socketio.emit('status', {'message': 'No active Gemini interaction to stop.'})

@socketio.on('connect')
def handle_connect():
    print('Client connected')
    emit('status', {'message': 'Connected to server. Ready.'})

@socketio.on('disconnect')
def handle_disconnect():
    print('Client disconnected')
    # Consider stopping Gemini session if a client disconnects unexpectedly
    # This needs careful handling if the asyncio loop is in a different thread or not running.
    # For now, we'll rely on the user explicitly stopping or a timeout.

@socketio.on('start_interaction') # Renamed event
def handle_start_interaction(data): # Renamed function
    print("[DEBUG] Entered handle_start_interaction") # DEBUG_LOG_1
    video_mode = data.get('video_mode', 'none')
    print(f"SocketIO: Received start_interaction event with video_mode: {video_mode}")

    if global_session_vars.get('is_running'):
        print("SocketIO: Interaction already in progress.")
        emit('error', {'message': 'Interaction already in progress.'})
        return

    try:
        print("SocketIO: Connecting to Gemini Live...")
        emit('status', {'message': 'Connecting to Gemini Live...'})
        
        # Function to run the asyncio tasks in a new thread
        def run_interaction_in_background():
            # Create a new event loop for this thread
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            try:
                # Create queues in the new loop
                audio_out_queue = asyncio.Queue()
                audio_in_queue = asyncio.Queue()
                
                # Run the interaction using an async context manager for the session
                async def main():
                    async with client.aio.live.connect(model=config.MODEL, config=CONFIG) as live_session:
                        print("SocketIO: Connected to Gemini Live.")
                        socketio.emit('status', {'message': 'Interaction session started. Listening...'})
                        await run_gemini_interaction(live_session, audio_out_queue, audio_in_queue, video_mode)

                loop.run_until_complete(main())
                
            except Exception as e_run:
                print(f"Error in background Gemini interaction task: {e_run}")
                traceback.print_exc()
                socketio.emit('error', {'message': f'Background task error: {str(e_run)}'})
            finally:
                print("Background interaction task finished. Closing loop.")
                loop.close()
                print("Asyncio loop for Gemini interaction closed.")

        # Start the asyncio tasks in a new thread so it doesn't block the SocketIO handler
        interaction_thread = threading.Thread(target=run_interaction_in_background)
        interaction_thread.daemon = True # Ensure thread exits when main program exits
        print("[DEBUG] Starting interaction_thread") # DEBUG_LOG_2
        interaction_thread.start()
        global_session_vars['interaction_thread'] = interaction_thread # Store thread to potentially join later

    except Exception as e:
        print(f"Error in handle_start_interaction: {e}")
        traceback.print_exc()
        emit('error', {'message': f'Failed to start interaction: {str(e)}'})

@socketio.on('stop_interaction')
def handle_stop_interaction():
    print("SocketIO: Received stop_interaction event.")
    emit('status', {'message': 'Stop request received. Attempting to stop interaction...'})
    
    if global_session_vars.get('is_running'):
        asyncio.run(stop_gemini_session_async())
    else:
        emit('status', {'message': 'No active interaction to stop.'})




if __name__ == '__main__':
    print("Starting Flask-SocketIO server...")
    list_audio_devices() # List devices on startup
    socketio.run(app, debug=True, host='0.0.0.0', port=5004, use_reloader=False)