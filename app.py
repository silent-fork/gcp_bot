import os
import asyncio
import base64
import io
import traceback
import threading

import cv2
import pyaudio
import PIL.Image
import mss

from flask import Flask, render_template
from flask_socketio import SocketIO, emit

from google import genai
from google.genai import types

# --- Configuration from sample.txt --- (with modifications for Flask)
FORMAT = pyaudio.paInt16
CHANNELS = 1
SEND_SAMPLE_RATE = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE = 1024

MODEL = "models/gemini-2.5-flash-preview-native-audio-dialog"

# Ensure GEMINI_API_KEY is set in your environment
API_KEY = os.environ.get("GEMINI_API_KEY")
if not API_KEY:
    raise ValueError("GEMINI_API_KEY environment variable not set.")

client = genai.Client(
    # http_options={"api_version": "v1beta"}, # This might be outdated or not needed for latest SDK
    api_key=API_KEY,
)

tools = [
    types.Tool(google_search=types.GoogleSearch()),
]

CONFIG = types.LiveConnectConfig(
    response_modalities=[
        "AUDIO",
        "TEXT", # Added TEXT for potential debugging or alternative output
    ],
    media_resolution="MEDIA_RESOLUTION_MEDIUM",
    speech_config=types.SpeechConfig(
        voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Zephyr")
        )
    ),
    # context_window_compression=types.ContextWindowCompressionConfig(
    #     trigger_tokens=25600,
    #     sliding_window=types.SlidingWindow(target_tokens=12800),
    # ),
    # tools=tools, # Temporarily removed for debugging ConnectionClosedError
)

pya = pyaudio.PyAudio()
# --- End of Configuration ---

app = Flask(__name__)
app.config['SECRET_KEY'] = os.urandom(24) # For session management
socketio = SocketIO(app, async_mode='threading') # Using threading for async operations

# Global session state (consider a more robust session management for multiple users)
global_session_vars = {
    'live_session': None,
    'audio_out_queue': None,
    'audio_in_queue': None,
    'video_mode': 'none', # Default to no video, can be changed by client
    'is_running': False,
    'tasks': []
}

def _get_frame(cap):
    ret, frame = cap.read()
    if not ret:
        return None
    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    img = PIL.Image.fromarray(frame_rgb)
    img.thumbnail([1024, 1024])
    image_io = io.BytesIO()
    img.save(image_io, format="jpeg")
    image_io.seek(0)
    mime_type = "image/jpeg"
    image_bytes = image_io.read()
    return {"mime_type": mime_type, "data": base64.b64encode(image_bytes).decode()}

async def get_frames_task(out_queue):
    cap = await asyncio.to_thread(cv2.VideoCapture, 0)
    while global_session_vars['is_running'] and global_session_vars['video_mode'] == 'camera':
        try:
            frame = await asyncio.to_thread(_get_frame, cap)
            if frame is None:
                socketio.emit('status', {'message': 'Error reading camera frame.'})
                break
            await out_queue.put(frame)
            await asyncio.sleep(1.0) # Match original
        except Exception as e:
            print(f"Error in get_frames_task: {e}")
            socketio.emit('error', {'message': f'Camera error: {str(e)}'})
            break
    if cap.isOpened():
        cap.release()
    print("Camera task ended")

def _get_screen():
    sct = mss.mss()
    monitor = sct.monitors[0]
    i = sct.grab(monitor)
    image_bytes = mss.tools.to_png(i.rgb, i.size)
    img = PIL.Image.open(io.BytesIO(image_bytes))
    image_io = io.BytesIO()
    img.save(image_io, format="jpeg")
    image_io.seek(0)
    image_bytes = image_io.read()
    return {"mime_type": "image/jpeg", "data": base64.b64encode(image_bytes).decode()}

async def get_screen_task(out_queue):
    while global_session_vars['is_running'] and global_session_vars['video_mode'] == 'screen':
        try:
            frame = await asyncio.to_thread(_get_screen)
            if frame is None:
                socketio.emit('status', {'message': 'Error capturing screen.'})
                break
            await out_queue.put(frame)
            await asyncio.sleep(1.0)
        except Exception as e:
            print(f"Error in get_screen_task: {e}")
            socketio.emit('error', {'message': f'Screen capture error: {str(e)}'})
            break
    print("Screen task ended")

async def send_realtime_task(live_session, out_queue):
    while global_session_vars['is_running']:
        try:
            msg = await out_queue.get()
            if live_session and global_session_vars['is_running']:
                await live_session.send(input=msg)
            out_queue.task_done()
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"Error in send_realtime_task: {e}")
            socketio.emit('error', {'message': f'Realtime sending error: {str(e)}'})
            break
    print("Send realtime task ended")

async def listen_audio_task(live_session, out_queue, socketio_instance): # Added socketio_instance
    print("[DEBUG] Entered listen_audio_task") # DEBUG_LOG_4
    mic_info = pya.get_default_input_device_info()
    audio_stream = await asyncio.to_thread(
        pya.open,
        format=FORMAT,
        channels=CHANNELS,
        rate=SEND_SAMPLE_RATE,
        input=True,
        input_device_index=mic_info["index"],
        frames_per_buffer=CHUNK_SIZE,
    )
    print("Microphone stream opened.")
    while global_session_vars['is_running']:
        try:
            # Use a timeout to prevent blocking indefinitely if is_running becomes false
            data = await asyncio.to_thread(audio_stream.read, CHUNK_SIZE, exception_on_overflow=False)
            if global_session_vars['is_running']:
                 await out_queue.put({"data": data, "mime_type": "audio/pcm"})
                 # Placeholder for user_text event. In a real app, this would come from a speech-to-text process.
                 # Emitting a generic message for now when audio is detected.
                 socketio_instance.emit('user_text', {'text': '[User speaking...]'})
                 # A more robust solution would be to only emit this once per user speech segment.
        except asyncio.CancelledError:
            break
        except IOError as e:
            if e.errno == pyaudio.paInputOverflowed:
                print("Input overflowed. Skipping frame.")
            else:
                print(f"IOError in listen_audio_task: {e}")
                socketio_instance.emit('error', {'message': f'Audio input error: {str(e)}'})
                break # Exit on other IOErrors
        except Exception as e:
            print(f"Error in listen_audio_task: {e}")
            socketio_instance.emit('error', {'message': f'Audio input error: {str(e)}'})
            break
    
    audio_stream.stop_stream()
    audio_stream.close()
    print("Microphone stream closed.")

async def receive_gemini_audio_task(live_session, audio_in_queue, socketio_instance):
    while global_session_vars['is_running']:
        try:
            if not live_session or not global_session_vars['is_running']:
                await asyncio.sleep(0.1)
                continue
            
            turn = live_session.receive() # This might block, ensure it's handled
            async for response in turn:
                if not global_session_vars['is_running']:
                    break
                if data := response.data:
                    socketio_instance.emit('bot_speech_start') # Emit before putting to queue
                    audio_in_queue.put_nowait(data)
                if text := response.text:
                    print(f"Gemini Text: {text}")
                    socketio_instance.emit('bot_text', {'text': text})
            # Clear queue on interruption (turn_complete)
            while not audio_in_queue.empty():
                audio_in_queue.get_nowait()
                audio_in_queue.task_done()

        except asyncio.CancelledError:
            break
        except Exception as e:
            # CORRECTED exception handling
            if type(e).__name__ == "StopCandidateException":
                print("Gemini turn ended (StopCandidateException).")
                break
            else:
                print(f"Error in receive_gemini_audio_task: {e}")
                socketio_instance.emit('error', {'message': f'Gemini audio receiving error: {str(e)}'})
                await asyncio.sleep(1) # Avoid tight loop on persistent error
    print("Receive Gemini audio task ended")

async def play_audio_task(audio_in_queue, socketio_instance): # Added socketio_instance
    stream = await asyncio.to_thread(
        pya.open,
        format=FORMAT, # Assuming output format is same as input for simplicity here, might need adjustment
        channels=CHANNELS,
        rate=RECEIVE_SAMPLE_RATE, # Gemini output rate
        output=True,
    )
    print("Audio output stream opened.")
    while global_session_vars['is_running']:
        try:
            bytestream = await asyncio.wait_for(audio_in_queue.get(), timeout=1.0) # Timeout to check is_running
            if global_session_vars['is_running']:
                await asyncio.to_thread(stream.write, bytestream)
            audio_in_queue.task_done()
            # Simplified bot_speech_end: emit if queue is empty. Real end-of-speech detection is more complex.
            if audio_in_queue.empty(): 
                 socketio_instance.emit('bot_speech_end')
        except asyncio.TimeoutError:
            continue # Check is_running and continue
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"Error in play_audio_task: {e}")
            socketio_instance.emit('error', {'message': f'Audio playback error: {str(e)}'})
            break
    stream.stop_stream()
    stream.close()
    print("Audio output stream closed.")

async def run_gemini_interaction(live_session, audio_out_queue, audio_in_queue, video_mode):
    print("[DEBUG] Entered run_gemini_interaction") # DEBUG_LOG_3
    print(f"Starting Gemini interaction with video_mode: {video_mode}")
    global_session_vars['is_running'] = True
    global_session_vars['live_session'] = live_session
    global_session_vars['audio_out_queue'] = audio_out_queue
    global_session_vars['audio_in_queue'] = audio_in_queue
    global_session_vars['video_mode'] = video_mode

    tasks = []
    try:
        # Start microphone input task
        tasks.append(asyncio.create_task(listen_audio_task(live_session, audio_out_queue, socketio))) # Passed socketio

        # Start video input task if applicable
        if video_mode == 'camera':
            tasks.append(asyncio.create_task(get_frames_task(audio_out_queue)))
        elif video_mode == 'screen':
            tasks.append(asyncio.create_task(get_screen_task(audio_out_queue)))

        # Start task to send data from queue to Gemini
        tasks.append(asyncio.create_task(send_realtime_task(live_session, audio_out_queue)))

        # Start task to receive audio from Gemini and play it
        tasks.append(asyncio.create_task(receive_gemini_audio_task(live_session, audio_in_queue, socketio))) # Passed socketio
        tasks.append(asyncio.create_task(play_audio_task(audio_in_queue, socketio))) # Passed socketio
        
        global_session_vars['tasks'] = tasks
        await asyncio.gather(*tasks)

    except Exception as e:
        # CORRECTED exception handling
        if type(e).__name__ == "StopCandidateException":
            print("StopCandidateException received, interaction ended normally.")
            socketio.emit('interaction_stopped', {'message': 'Interaction ended by Gemini.'})
        elif type(e).__name__ == "DeadlineExceeded":
            print("DeadlineExceeded during Gemini interaction.")
            socketio.emit('error', {'message': 'Interaction timed out.'})
        else:
            print(f"Exception in run_gemini_interaction: {e}")
            traceback.print_exc()
            socketio.emit('error', {'message': f'An error occurred: {str(e)}'})
    finally:
        print("run_gemini_interaction cleaning up...")
        await stop_gemini_session_async()

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
                
                # Connect to Gemini Live
                live_session = client.aio.live.connect(model=MODEL, config=CONFIG)
                print("SocketIO: Connected to Gemini Live.")
                socketio.emit('status', {'message': 'Interaction session started. Listening...'})
                
                # Run the interaction
                loop.run_until_complete(run_gemini_interaction(live_session, audio_out_queue, audio_in_queue, video_mode))
                
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

@socketio.on('stop_interaction') # Renamed event
def handle_stop_interaction(): # Renamed function
    print("SocketIO: Received stop_interaction event.")
    emit('status', {'message': 'Stop request received. Attempting to stop interaction...'})
    
    # Simply set the flag to stop the interaction
    # The background thread will handle the cleanup
    if global_session_vars.get('is_running'):
        global_session_vars['is_running'] = False
        emit('status', {'message': 'Interaction stopped.'})
        emit('interaction_stopped', {'message': 'Interaction stopped by user.'})
    else:
        emit('status', {'message': 'No active interaction to stop.'})


if __name__ == '__main__':
    print("Starting Flask-SocketIO server...")
    # It's important that PyAudio is terminated when the app exits.
    try:
        socketio.run(app, debug=True, host='0.0.0.0', port=5001, use_reloader=False) # Changed port to 5001
    finally:
        print("Terminating PyAudio...")
        pya.terminate()
        print("PyAudio terminated.")