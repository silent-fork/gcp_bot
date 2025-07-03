import asyncio
import base64
import io
import traceback

import PIL.Image
import cv2
import mss
from google.genai import types

from src.audio import listen_audio_task, play_audio_task

socketio = None

global_session_vars = {
    'live_session': None,
    'audio_out_queue': None,
    'audio_in_queue': None,
    'video_mode': 'none',
    'is_running': False,
    'mic_is_on': False,
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
            await asyncio.sleep(1.0)
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
                data_bytes = msg['data']

                # Use types.Blob for sending media data
                media_blob = types.Blob(data=data_bytes, mime_type=msg['mime_type'])
                await live_session.send_realtime_input(media=media_blob)
            out_queue.task_done()
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"Error in send_realtime_task: {e}")
            socketio.emit('error', {'message': f'Realtime sending error: {str(e)}'})
            break
    print("Send realtime task ended")

async def receive_gemini_audio_task(live_session, audio_in_queue, socketio_instance):
    while global_session_vars['is_running']:
        try:
            if not live_session or not global_session_vars['is_running']:
                await asyncio.sleep(0.1)
                continue

            async for response in live_session.receive():
                if not global_session_vars['is_running']:
                    break
                if response.audio:
                    socketio_instance.emit('bot_speech_start')
                    audio_in_queue.put_nowait(response.audio)
                if response.text:
                    print(f"Gemini Text: {response.text}")
                    socketio_instance.emit('bot_text', {'text': response.text})

        except asyncio.CancelledError:
            break
        except Exception as e:
            if type(e).__name__ == "StopCandidateException":
                print("Gemini turn ended (StopCandidateException).")
                break
            else:
                print(f"Error in receive_gemini_audio_task: {e}")
                socketio_instance.emit('error', {'message': f'Gemini audio receiving error: {str(e)}'})
                await asyncio.sleep(1)
    print("Receive Gemini audio task ended")


async def stop_gemini_session_async():
    print("Stopping Gemini session...")
    global_session_vars['is_running'] = False
    global_session_vars['mic_is_on'] = False

    live_session = global_session_vars.get('live_session')
    if live_session:
        if global_session_vars['tasks']:
            for task in global_session_vars['tasks']:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*global_session_vars['tasks'], return_exceptions=True)
            global_session_vars['tasks'] = []

        if global_session_vars['live_session']:
            # No async close for live_session, it's managed by the context
            global_session_vars['live_session'] = None

        # Reset queues by creating new ones
        global_session_vars['audio_out_queue'] = asyncio.Queue()
        global_session_vars['audio_in_queue'] = asyncio.Queue()

        print("Gemini session stopped.")
        socketio.emit('interaction_stopped', {'message': 'Session stopped by user.'})


async def run_gemini_interaction(live_session, audio_out_queue, audio_in_queue, video_mode):
    print(f"Starting Gemini interaction with video_mode: {video_mode}")
    global_session_vars['is_running'] = True
    global_session_vars['mic_is_on'] = True
    global_session_vars['live_session'] = live_session
    global_session_vars['audio_out_queue'] = audio_out_queue
    global_session_vars['audio_in_queue'] = audio_in_queue
    global_session_vars['video_mode'] = video_mode

    tasks = []
    try:
        tasks.append(asyncio.create_task(listen_audio_task(audio_out_queue, global_session_vars)))

        if video_mode == 'camera':
            tasks.append(asyncio.create_task(get_frames_task(audio_out_queue)))
        elif video_mode == 'screen':
            tasks.append(asyncio.create_task(get_screen_task(audio_out_queue)))

        tasks.append(asyncio.create_task(send_realtime_task(live_session, audio_out_queue)))
        tasks.append(asyncio.create_task(receive_gemini_audio_task(live_session, audio_in_queue, socketio)))
        tasks.append(asyncio.create_task(play_audio_task(audio_in_queue)))

        global_session_vars['tasks'] = tasks
        await asyncio.gather(*tasks)

    except Exception as e:
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